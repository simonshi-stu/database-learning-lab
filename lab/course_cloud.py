"""路线 C · 云后端实战引擎：Outbox + 双进程 + 本地数据湖(对象存储模拟) + DuckDB。

贯穿 request_id(订单键) 与 event_id(outbox 主键)。业务库=PostgreSQL 隔离 schema；
数据湖=本地目录 runs/cloud/<run>/lake（明确标为对象存储模拟）；Parquet/查询=DuckDB。
学生可改一处 SQL/YAML（数据而非命令）；自由代码经本地脚本运行，Web 不执行任意命令。
"""
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import psycopg

from . import pglab
from .course_engines import case, step, ENGINES, _verdict

ROOT = Path(__file__).resolve().parent.parent

DDL = """
CREATE TABLE products(sku text PRIMARY KEY, qty int NOT NULL CHECK (qty>=0));
CREATE TABLE orders(request_key text PRIMARY KEY, sku text, qty int, amount_cents int, status text, biz_date text);
CREATE TABLE outbox(event_id serial PRIMARY KEY, request_key text, kind text, payload text, published boolean DEFAULT false);
CREATE TABLE consumer_results(event_id text PRIMARY KEY, request_key text, net_cents int, room_nights int, biz_date text);
CREATE TABLE failures(id serial PRIMARY KEY, event_id text, reason text, attempts int);
CREATE TABLE refunds(refund_id text PRIMARY KEY, request_key text, amount_cents int, biz_date text);
CREATE TABLE rooms(room_id text PRIMARY KEY);
CREATE TABLE room_nights(room_id text, night text, blocked int DEFAULT 0, PRIMARY KEY(room_id,night));
CREATE TABLE bookings(request_id text PRIMARY KEY, status text);
CREATE TABLE booking_nights(request_id text, room_id text, night text, PRIMARY KEY(request_id,night), UNIQUE(room_id,night));
"""


def _seed(c, hotel=False):
    c.execute("INSERT INTO products VALUES('A', 5)")
    if hotel:
        c.execute("INSERT INTO rooms VALUES('S101'),('S102')")
        for n in ('2026-12-07', '2026-12-08', '2026-12-09'):
            for r in ('S101', 'S102'):
                c.execute('INSERT INTO room_nights VALUES(%s,%s,0)', (r, n))


def _place(c, key, sku, qty, amount, biz_date, guard_sql):
    c.execute('BEGIN')
    if guard_sql:
        old = c.execute(guard_sql, (key,)).fetchone() if '%s' in guard_sql else c.execute(guard_sql).fetchone()
        if old:
            c.execute('COMMIT')
            return dict(replayed=True, request_key=key)
    cur = c.execute('UPDATE products SET qty=qty-%s WHERE sku=%s AND qty>=%s', (qty, sku, qty))
    if cur.rowcount != 1:
        c.execute('ROLLBACK')
        return dict(error='INSUFFICIENT_STOCK', request_key=key)
    try:
        c.execute('INSERT INTO orders VALUES(%s,%s,%s,%s,%s,%s)', (key, sku, qty, amount, 'PLACED', biz_date))
        c.execute('INSERT INTO outbox(request_key, kind, payload) VALUES(%s,%s,%s)',
                  (key, 'order_placed', json.dumps(dict(net_cents=amount, room_nights=0, biz_date=biz_date, sku=sku, qty=qty))))
        c.execute('COMMIT')
    except psycopg.errors.UniqueViolation:
        c.execute('ROLLBACK')
        return dict(replayed=False, duplicate_attempt=True, error='DUPLICATE_ORDER', request_key=key)
    return dict(replayed=False, request_key=key)


def _book(c, key, nights, guard_sql):
    c.execute('BEGIN')
    if guard_sql:
        old = c.execute(guard_sql, (key,)).fetchone() if '%s' in guard_sql else c.execute(guard_sql).fetchone()
        if old:
            c.execute('COMMIT')
            return dict(replayed=True, request_key=key)
    cand = None
    for n in nights:
        rows = c.execute('SELECT room_id FROM room_nights WHERE night=%s AND blocked=0 AND room_id NOT IN (SELECT room_id FROM booking_nights WHERE night=%s)', (n, n)).fetchall()
        ids = {r[0] for r in rows}
        cand = ids if cand is None else cand & ids
    if not cand:
        c.execute('INSERT INTO bookings VALUES(%s,%s)', (key, 'REJECTED'))
        c.execute('COMMIT')
        return dict(error='NO_ROOM', request_key=key)
    room = sorted(cand)[0]
    try:
        c.execute('INSERT INTO bookings VALUES(%s,%s)', (key, 'BOOKED'))
        for n in nights:
            c.execute('INSERT INTO booking_nights VALUES(%s,%s,%s)', (key, room, n))
        c.execute('INSERT INTO outbox(request_key, kind, payload) VALUES(%s,%s,%s)',
                  (key, 'booking', json.dumps(dict(net_cents=0, room_nights=len(nights), biz_date=nights[0], room_id=room))))
        c.execute('COMMIT')
    except psycopg.errors.UniqueViolation:
        c.execute('ROLLBACK')
        return dict(replayed=False, duplicate_attempt=True, error='DUPLICATE_BOOKING', request_key=key)
    return dict(replayed=False, request_key=key, room_id=room)


def _refund(c, rid, key, amount, biz_date):
    c.execute('BEGIN')
    c.execute('INSERT INTO refunds VALUES(%s,%s,%s,%s)', (rid, key, amount, biz_date))
    c.execute('INSERT INTO outbox(request_key, kind, payload) VALUES(%s,%s,%s)',
              (key, 'refund', json.dumps(dict(net_cents=-amount, room_nights=0, biz_date=biz_date, refund_id=rid))))
    c.execute('COMMIT')


def _worker(schema, lake, **kw):
    payload = Path(lake) / 'worker_payload.json'
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_text(json.dumps(dict(schema=schema, lake_dir=str(lake), **kw)), encoding='utf-8')
    proc = subprocess.run([sys.executable, '-B', '-m', 'lab.cloud_worker', str(payload)],
                          cwd=str(ROOT), capture_output=True, timeout=40)
    events = []
    for line in proc.stdout.decode('utf-8', 'replace').splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events, proc.returncode


def _lake_clean(lake, dedup_sql):
    con = duckdb.connect()
    raw = str(Path(lake) / 'raw' / '*.json')
    con.execute(f"CREATE TABLE raw AS SELECT * FROM read_json_auto('{raw}')")
    total = con.execute('SELECT COUNT(*) FROM raw').fetchone()[0]
    bad = con.execute("SELECT COUNT(*) FROM raw WHERE net_cents IS NULL OR biz_date IS NULL OR TRY_CAST(biz_date AS DATE) IS NULL").fetchone()[0]
    con.execute("CREATE TABLE valid AS SELECT * FROM raw WHERE net_cents IS NOT NULL AND biz_date IS NOT NULL AND TRY_CAST(biz_date AS DATE) IS NOT NULL")
    con.execute(f'CREATE TABLE dedup AS SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY event_id) rn FROM valid) WHERE {dedup_sql}')
    dup = con.execute('SELECT COUNT(*) FROM valid').fetchone()[0] - con.execute('SELECT COUNT(*) FROM dedup').fetchone()[0]
    part_dir = Path(lake) / 'parquet'
    con.execute(f"COPY (SELECT * FROM dedup) TO '{part_dir.as_posix()}' (FORMAT parquet, PARTITION_BY (biz_date))")
    files = [str(p.relative_to(lake)) for p in part_dir.rglob('*.parquet')]
    return con, dict(total=total, bad=bad, dup=dup, files=files)


def _report(con, sql):
    try:
        rows = con.execute(sql).fetchall()
        cols = [d[0] for d in con.description]
        return [dict(zip(cols, r)) for r in rows], None
    except Exception as exc:
        return None, str(exc).splitlines()[0]


DEFAULT_DEDUP = 'rn = 1'
DEFAULT_REPORT = "SELECT biz_date, SUM(net_cents) AS net_cents, SUM(room_nights) AS room_nights FROM dedup GROUP BY biz_date ORDER BY biz_date"


def _run_cloud(lesson, code, run_dir, choice='', scenario=''):
    ok, why = pglab.available()
    if not ok:
        return [case(lesson['title'], None, error=None, steps=[],
                     extra=dict(not_measured=f'跳过：{why}。配置后重跑；不计失败也不计通过。'))]
    cc = lesson.get('cloud_case', 'd2')
    schema = pglab.new_schema()
    lake = run_dir / 'lake'
    c = pglab.connect(schema, autocommit=True)
    c.execute(DDL)
    hotel = cc in ('l4', 'f1') or lesson.get('cloud_hotel')
    _seed(c, hotel)
    steps = []
    extra = dict(schema=schema, lake=str(lake), storage='本地目录=对象存储模拟（非真实 S3）')
    guard = code.strip() if cc in ('c1', 'c2', 'd2') and code.strip() else "SELECT request_key FROM orders WHERE request_key=%s"
    dedup = code.strip() if cc in ('l3', 'l4', 'l5') and code.strip() else DEFAULT_DEDUP
    report_sql = code.strip() if cc == 'l5' and code.strip().upper().startswith('SELECT') else DEFAULT_REPORT

    def snap(label):
        t = {}
        for n in ('products', 'orders', 'outbox', 'consumer_results', 'refunds', 'bookings', 'booking_nights'):
            cur = c.execute(f'SELECT * FROM {n} ORDER BY 1')
            cols = [d[0] for d in cur.description]
            t[n] = [dict(zip(cols, r)) for r in cur.fetchall()]
        steps.append(step(label, 'committed', t, None, None, '业务库'))

    if cc == 'c1':
        r = _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', guard)
        steps.append(step('POST /orders（示意入口）→ 真实函数 _place', 'committed', {}, None, f'request_id=ord-1 replayed={r.get("replayed")}', 'API'))
        snap('下单后业务库')
        cur = c.execute(code if code.strip().upper().startswith('SELECT') else 'SELECT sku, qty FROM products')
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        steps.append(step('你的确认查询', 'read', {'查询结果': rows}, code, None, '学生'))
        passed = c.execute('SELECT qty FROM products').fetchone()[0] == 4 and c.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 1
        return [case('发一次请求并查表确认', passed, expected='库存4 订单1', actual='见快照', steps=steps, extra=extra)]
    if cc == 'c2':
        _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', guard)
        snap('退款前')
        _refund(c, 'ref-1', 'ord-1', 9900, '2026-12-08')
        c.execute("UPDATE orders SET status='REFUNDED' WHERE request_key='ord-1'")
        c.execute("UPDATE products SET qty=qty+1 WHERE sku='A'")
        snap('退款后')
        passed = c.execute("SELECT status FROM orders").fetchone()[0] == 'REFUNDED' and c.execute('SELECT qty FROM products').fetchone()[0] == 5
        return [case('退款后哪些数据改变', passed, expected='订单REFUNDED 库存回5 退款行+退款事件', actual='见前后快照', steps=steps, extra=extra)]
    if cc == 'd1':
        _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', guard)
        ev, rc = _worker(schema, lake)
        steps += [step(e['op'], 'committed', {}, None, f"pid={e.get('pid')} request={e.get('request_key')} event={e.get('event_id')}", 'worker') for e in ev]
        snap('两进程处理后')
        passed = rc == 0 and c.execute('SELECT COUNT(*) FROM consumer_results').fetchone()[0] == 1
        return [case('下单与后台拆成两个进程', passed, expected='worker 独立 pid 消费1事件', actual=f'rc={rc}', steps=steps, extra=extra)]
    if cc == 'd2':
        r1 = _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', guard)
        steps.append(step('首次提交成功但回复丢失（模拟客户端超时）', 'committed', {}, None, 'request_id=ord-1 已提交', 'API'))
        r2 = _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', guard)
        steps.append(step('同键重试', 'committed', {}, None, f'replayed={r2.get("replayed")}', 'API'))
        snap('重试后')
        n_orders = c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
        qty = c.execute('SELECT qty FROM products').fetchone()[0]
        passed = n_orders == 1 and qty == 4 and r2.get('replayed')
        return [case('提交后丢回复·同键重试只留一单', passed, expected='订单1 库存4 重试replayed',
                     actual=f'订单{n_orders} 库存{qty} replayed={r2.get("replayed")}', steps=steps, extra=extra)]
    if cc == 'd3':
        _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', guard)
        _place(c, 'ord-2', 'A', 1, 9900, '2026-12-07', guard)
        pending = c.execute('SELECT COUNT(*) FROM outbox WHERE published=false').fetchone()[0]
        steps.append(step('worker 停止期间积压 outbox', 'pending', {}, None, f'未发布{pending}条', 'outbox'))
        ev, rc = _worker(schema, lake)
        steps += [step(e['op'], 'committed', {}, None, f"event={e.get('event_id')}", 'worker') for e in ev]
        snap('worker 补发后')
        passed = c.execute('SELECT COUNT(*) FROM outbox WHERE published=false').fetchone()[0] == 0 and c.execute('SELECT COUNT(*) FROM consumer_results').fetchone()[0] == 2
        return [case('Outbox 停worker再启动补发', passed, expected='积压清零 结果2', actual='见快照', steps=steps, extra=extra)]
    if cc == 'd4':
        _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', guard)
        ev1, rc1 = _worker(schema, lake, stop_before_ack=True)
        steps += [step(e['op'], 'committed', {}, None, f"event={e.get('event_id')} wrote={e.get('wrote_result')}", 'worker#1') for e in ev1]
        ev2, rc2 = _worker(schema, lake)
        steps += [step(e['op'], 'committed', {}, None, f"event={e.get('event_id')} wrote={e.get('wrote_result')}", 'worker#2') for e in ev2]
        snap('重复投递后')
        n_res = c.execute('SELECT COUNT(*) FROM consumer_results').fetchone()[0]
        passed = rc1 == 73 and n_res == 1
        return [case('确认前退出·重复事件结果只写一次', passed, expected='结果1行（消息可重复）', actual=f'结果{n_res}行 rc1={rc1}', steps=steps, extra=extra)]
    if cc == 'd5':
        _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', guard)
        ev1, _ = _worker(schema, lake, fail_first_n=2)
        steps += [step(e['op'], 'error' if e['op'] == 'downstream_fail' else 'committed', {}, None, f"event={e.get('event_id')} {e.get('reason','')}", 'worker') for e in ev1]
        ev2, _ = _worker(schema, lake)
        steps += [step(e['op'], 'committed', {}, None, f"event={e.get('event_id')}", 'worker重试') for e in ev2]
        snap('有限重试后')
        fails = c.execute('SELECT COUNT(*) FROM failures').fetchone()[0]
        passed = fails >= 1 and c.execute('SELECT COUNT(*) FROM consumer_results').fetchone()[0] == 1
        return [case('下游不可用·超时有限重试退避失败记录', passed, expected='失败记录≥1 最终结果1', actual=f'失败{fails}', steps=steps, extra=extra)]
    # ---- 数据湖 ----
    _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', guard)
    _place(c, 'ord-2', 'A', 1, 5000, '2026-12-08', guard)
    if cc in ('l4', 'l5', 'f1'):
        _refund(c, 'ref-late', 'ord-1', 9900, '2026-12-08')  # 次日迟到退款
    if hotel:
        _book(c, 'bk-1', ['2026-12-07', '2026-12-08'], "SELECT request_id FROM bookings WHERE request_id=%s")
    ev, rc = _worker(schema, lake)
    steps += [step(e['op'], 'committed', {}, None, f"event={e.get('event_id')} request={e.get('request_key')}", 'worker') for e in ev]
    if cc == 'l4':
        # 重复投递同一事件 + 跨午夜入住
        dup_src = next((Path(lake) / 'raw' / f) for f in os.listdir(Path(lake) / 'raw') if f.endswith('.json'))
        dup_data = json.loads(dup_src.read_text(encoding='utf-8'))
        (Path(lake) / 'raw' / 'event_dup.json').write_text(json.dumps(dup_data), encoding='utf-8')
        _book(c, 'bk-mid', ['2026-12-08', '2026-12-09'], "SELECT request_id FROM bookings WHERE request_id=%s")
        ev2, _ = _worker(schema, lake)
        steps += [step(e['op'], 'committed', {}, None, f"event={e.get('event_id')}", 'worker#2') for e in ev2]
    con, lakeinfo = _lake_clean(lake, dedup)
    extra.update(lakeinfo)
    steps.append(step('清洗+去重+按业务日期写 Parquet', 'committed', {}, None,
                      f"总{lakeinfo['total']} 坏{lakeinfo['bad']} 重复{lakeinfo['dup']} 分区文件{len(lakeinfo['files'])}", 'DuckDB'))
    if cc == 'l1':
        biz = c.execute('SELECT SUM(amount_cents) FROM orders').fetchone()[0]
        rawsum = con.execute('SELECT SUM(net_cents) FROM raw').fetchone()[0]
        passed = biz == rawsum
        return [case('业务库 vs 原始文件各能回答什么', passed, expected=f'业务净额={biz} 原始净额={rawsum}', actual='相等', steps=steps, extra=extra)]
    if cc == 'l2':
        badrows = con.execute("SELECT * FROM raw WHERE net_cents IS NULL OR biz_date IS NULL OR TRY_CAST(biz_date AS DATE) IS NULL").fetchall()
        bcols = [d[0] for d in con.description]
        steps.append(step('坏记录单独列出', 'error', {'坏记录': [dict(zip(bcols, r)) for r in badrows]}, None, '字段缺失/日期非法', '校验'))
        passed = lakeinfo['bad'] == 0 or len(badrows) == lakeinfo['bad']
        return [case('校验字段·坏记录列原因', passed, expected='坏行数与列出一致', actual=f"坏{lakeinfo['bad']}", steps=steps, extra=extra)]
    if cc == 'l3':
        rows, err = _report(con, code if code.strip().upper().startswith('SELECT') else DEFAULT_REPORT)
        steps.append(step('你的 DuckDB SQL', 'read', {'报表': rows or []}, code, err or '见分区文件', '学生'))
        passed = err is None and len(lakeinfo['files']) >= 1
        return [case('改一条 DuckDB SQL 看读了哪些分区', passed, expected='查询成功且分区≥1', actual=f'文件{len(lakeinfo["files"])} err={err}', steps=steps, extra=extra)]
    if cc == 'l4':
        rows, err = _report(con, DEFAULT_REPORT)
        net = sum(r['net_cents'] for r in rows or [])
        nights = sum(r['room_nights'] for r in rows or [])
        # 期望：ord1 9900 + ord2 5000 - ref 9900 = 5000；hotel bk1 2晚 + bk-mid 2晚 = 4
        passed = err is None and net == 5000 and (nights == 4 if hotel else True) and lakeinfo['dup'] >= 1
        return [case('重复/迟到/跨午夜重跑对账', passed, expected='净额5000 间夜4 重复被去重', actual=f'净{net} 间夜{nights} 重复{lakeinfo["dup"]}', steps=steps, extra=extra)]
    if cc == 'l5':
        rows, err = _report(con, report_sql)
        steps.append(step('改口径从原始重建', 'read', {'报表': rows or []}, report_sql, err or f"来源{lakeinfo['total']}行 坏{lakeinfo['bad']} 重复{lakeinfo['dup']}", '学生'))
        passed = err is None
        return [case('改报表口径从原始重建', passed, expected='查询成功且来源/行数/坏/重复可见', actual=f'err={err}', steps=steps, extra=extra)]
    if cc == 'f1':
        rows, err = _report(con, DEFAULT_REPORT)
        net = sum(r['net_cents'] for r in rows or [])
        passed = err is None and net == 5000 and c.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 2
        return [case('结课：同一订单贯穿各环节', passed, expected='净额5000 订单2 去重正确', actual=f'净{net}', steps=steps, extra=extra)]
    return [case(cc, False, error='unknown cloud case', steps=steps, extra=extra)]


ENGINES.update({'cloud': _run_cloud})
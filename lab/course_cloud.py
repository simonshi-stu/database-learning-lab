"""路线 C · 云后端实战引擎：Outbox + 双进程 + 本地数据湖(对象存储模拟) + DuckDB。

贯穿 request_id(订单键) 与 event_id(outbox 主键)。业务库=PostgreSQL 隔离 schema；
数据湖=本地目录 runs/cloud/<run>/lake（对象存储模拟）；Parquet/查询=DuckDB（仅本次教学数据）。
学生 SQL 只在隔离 schema 执行并设 statement_timeout；去重条件与报表 SQL 分离。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import duckdb
import psycopg

from . import pglab
from .course_engines import case, step, ENGINES

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
DEFAULT_GUARD = "SELECT request_key FROM orders WHERE request_key=%s"
DEFAULT_DEDUP = 'rn = 1'
DEFAULT_REPORT = "SELECT biz_date, SUM(net_cents) AS net_cents, SUM(room_nights) AS room_nights FROM dedup GROUP BY biz_date ORDER BY biz_date"


def _seed(c, hotel=False):
    c.execute("INSERT INTO products VALUES('A', 5)")
    if hotel:
        c.execute("INSERT INTO rooms VALUES('S101'),('S102')")
        for n in ('2026-12-07', '2026-12-08', '2026-12-09', '2026-12-10'):
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
        for n in nights:
            c.execute('INSERT INTO outbox(request_key, kind, payload) VALUES(%s,%s,%s)',
                      (key, 'booking', json.dumps(dict(net_cents=0, room_nights=1, biz_date=n, room_id=room))))
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
                          cwd=str(ROOT), capture_output=True, timeout=60)
    events = []
    for line in proc.stdout.decode('utf-8', 'replace').splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events, proc.returncode


def _rows(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _try_cast(value, typ):
    con = duckdb.connect()
    try:
        return con.execute(f"SELECT TRY_CAST(? AS {typ})", [str(value)]).fetchone()[0]
    finally:
        con.close()


def _lake_clean(lake, dedup_cond, subdir='parquet'):
    con = duckdb.connect()
    con.execute("SET memory_limit='256MB'")
    con.execute("SET threads=2")
    raw = str(Path(lake) / 'raw' / '*.json')
    con.execute(f"CREATE TABLE raw AS SELECT * FROM read_json_auto('{raw}')")
    total = con.execute('SELECT COUNT(*) FROM raw').fetchone()[0]
    con.execute("CREATE TABLE valid AS SELECT * REPLACE (TRY_CAST(net_cents AS INTEGER) AS net_cents) FROM raw WHERE TRY_CAST(net_cents AS INTEGER) IS NOT NULL AND biz_date IS NOT NULL AND TRY_CAST(biz_date AS DATE) IS NOT NULL")
    vcols = [d[0] for d in con.execute('SELECT * FROM valid LIMIT 0').description]
    for col, typ in (('room_nights', 'INTEGER'), ('net_cents', 'INTEGER'), ('biz_date', 'VARCHAR'), ('event_id', 'BIGINT'), ('request_key', 'VARCHAR'), ('kind', 'VARCHAR')):
        if col not in vcols:
            con.execute(f'ALTER TABLE valid ADD COLUMN {col} {typ}')
    bad_rows = _rows(con.execute("SELECT * FROM raw WHERE TRY_CAST(net_cents AS INTEGER) IS NULL OR biz_date IS NULL OR TRY_CAST(biz_date AS DATE) IS NULL"))
    reasons = []
    for r in bad_rows:
        why = []
        if r.get('net_cents') is None:
            why.append('缺失金额 net_cents')
        elif _try_cast(r.get('net_cents'), 'INTEGER') is None:
            why.append('非法金额 net_cents=' + str(r.get('net_cents')))
        if r.get('biz_date') is None:
            why.append('缺失业务日期 biz_date')
        elif _try_cast(r.get('biz_date'), 'DATE') is None:
            why.append('非法日期 biz_date=' + str(r.get('biz_date')))
        reasons.append(dict(event_id=r.get('event_id'), reason='；'.join(why) or '字段非法'))
    con.execute(f'CREATE TABLE dedup AS SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY event_id) rn FROM valid) WHERE {dedup_cond}')
    valid_n = con.execute('SELECT COUNT(*) FROM valid').fetchone()[0]
    dedup_n = con.execute('SELECT COUNT(*) FROM dedup').fetchone()[0]
    part_dir = Path(lake) / subdir
    con.execute(f"COPY (SELECT * FROM dedup) TO '{part_dir.as_posix()}' (FORMAT parquet, PARTITION_BY (biz_date))")
    files = [p.relative_to(lake).as_posix() for p in part_dir.rglob('*.parquet')]
    info = dict(total=total, valid=valid_n, bad=len(bad_rows), rejected=reasons, dedup=dedup_n,
                dup=valid_n - dedup_n, files=files)
    return con, info


def _report(con, sql, source='dedup'):
    try:
        q = re.sub(r'\bfrom\s+dedup\b', f' FROM {source}', sql, flags=re.I)
        cur = con.execute(q)
        return _rows(cur), [d[0] for d in cur.description], None
    except Exception as exc:
        return None, None, str(exc).splitlines()[0]


def _run_cloud(lesson, code, run_dir, choice='', scenario=''):
    ok, why = pglab.available()
    if not ok:
        return [case(lesson['title'], None, error=None, steps=[],
                     extra=dict(not_measured=f'跳过：{why}。配置后重跑；不计失败也不计通过。'))]
    cc = lesson.get('cloud_case', 'd2')
    schema = pglab.new_schema()
    lake = run_dir / 'lake'
    c = pglab.connect(schema, autocommit=True)
    c.execute("SET statement_timeout='15s'")
    c.execute(DDL)
    hotel = cc in ('l4', 'f1') or lesson.get('cloud_hotel')
    _seed(c, hotel)
    steps = []
    extra = dict(schema=schema, lake=str(lake), storage='本地目录=对象存储模拟（非真实 S3）', engine='PostgreSQL + DuckDB/本地数据湖')
    code = (code or '').strip()
    guard = code if cc == 'd2' and code else DEFAULT_GUARD
    dedup_cond = code if cc in ('l4', 'f1') and code else DEFAULT_DEDUP
    report_sql = code if cc in ('l3', 'l5') and code.upper().startswith('SELECT') else DEFAULT_REPORT

    def snap(label):
        t = {}
        for n in ('products', 'orders', 'outbox', 'consumer_results', 'refunds', 'bookings', 'booking_nights', 'failures'):
            t[n] = _rows(c.execute(f'SELECT * FROM {n} ORDER BY 1'))
        steps.append(step(label, 'committed', t, None, None, '业务库'))

    def wstep(e, actor='worker'):
        ph = 'error' if e['op'] in ('attempt_fail', 'exhausted') else 'committed'
        steps.append(step(e['op'], ph, {}, None,
                          f"pid={e.get('pid')} request={e.get('request_key')} event={e.get('event_id')} attempt={e.get('attempt','')}", actor))

    if cc == 'c1':
        r = _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', DEFAULT_GUARD)
        steps.append(step('执行一次下单（真实事务）', 'committed', {}, None, f'request_id=ord-1 replayed={r.get("replayed")}', 'API'))
        snap('下单后业务库')
        q = code if code.upper().startswith('SELECT') else 'SELECT sku, qty FROM products'
        try:
            got = _rows(c.execute(q))
            err = None
        except Exception as exc:
            got, err = None, str(exc).splitlines()[0]
        steps.append(step('学生查表确认', 'read', {'查询结果': got or []}, q, err, '学生'))
        want = [dict(sku='A', qty=4)]
        if err:
            passed, msg = False, '查询报错：' + err
        elif got != want:
            passed, msg = False, f'查错列/查错行/没返回应有结果：期望 {want}，实际 {got}'
        else:
            passed, msg = True, None
        n_orders = c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
        qty = c.execute('SELECT qty FROM products').fetchone()[0]
        passed = passed and n_orders == 1 and qty == 4
        return [case('发一次请求并查表确认', passed, expected='订单1 库存5→4 且确认查询返回 sku,qty',
                     actual=f'订单{n_orders} 库存{qty} 查询{got}', error=msg, steps=steps, extra=extra)]
    if cc == 'c2':
        _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', DEFAULT_GUARD)
        snap('退款前')
        _refund(c, 'ref-1', 'ord-1', 9900, '2026-12-08')
        c.execute("UPDATE orders SET status='REFUNDED' WHERE request_key='ord-1'")
        c.execute("UPDATE products SET qty=qty+1 WHERE sku='A'")
        snap('退款后')
        passed = c.execute("SELECT status FROM orders").fetchone()[0] == 'REFUNDED' and c.execute('SELECT qty FROM products').fetchone()[0] == 5
        return [case('退款后哪些数据改变', passed, expected='订单REFUNDED 库存回5 退款行+退款事件', actual='见前后快照', steps=steps, extra=extra)]
    if cc == 'd1':
        _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', DEFAULT_GUARD)
        ev, rc = _worker(schema, lake)
        for e in ev:
            wstep(e)
        snap('两进程处理后')
        passed = rc == 0 and c.execute('SELECT COUNT(*) FROM consumer_results').fetchone()[0] == 1
        return [case('下单与后台拆成两个进程', passed, expected='worker 独立 pid 消费1事件', actual=f'rc={rc}', steps=steps, extra=extra)]
    if cc == 'd2':
        _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', guard)
        steps.append(step('首次提交成功（真实数据库事务）', 'committed', {}, None, 'request_id=ord-1 已提交', 'API'))
        steps.append(step('回复丢失（模拟客户端超时，非数据库行为）', 'illustrative', {}, None, '模拟：客户端没收到回复', '客户端'))
        r2 = _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', guard)
        steps.append(step('同键重试（真实事务）', 'committed', {}, None, f'replayed={r2.get("replayed")} duplicate_attempt={r2.get("duplicate_attempt", False)}', 'API'))
        snap('重试后')
        n_orders = c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
        qty = c.execute('SELECT qty FROM products').fetchone()[0]
        passed = n_orders == 1 and qty == 4 and r2.get('replayed')
        msg = None if passed else '唯一约束挡住重复写入，但重试无法返回原结果（需幂等检查返回原单）'
        return [case('提交后丢回复·同键重试只留一单', passed, expected='订单1 库存4 重试replayed',
                     actual=f'订单{n_orders} 库存{qty} replayed={r2.get("replayed")}', error=msg, steps=steps, extra=extra)]
    if cc == 'd3':
        _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', DEFAULT_GUARD)
        _place(c, 'ord-2', 'A', 1, 9900, '2026-12-07', DEFAULT_GUARD)
        pending = c.execute('SELECT COUNT(*) FROM outbox WHERE published=false').fetchone()[0]
        steps.append(step('worker 停止期间积压 outbox', 'pending', {}, None, f'未发布{pending}条', 'outbox'))
        ev, rc = _worker(schema, lake)
        for e in ev:
            wstep(e)
        snap('worker 补发后')
        passed = c.execute('SELECT COUNT(*) FROM outbox WHERE published=false').fetchone()[0] == 0 and c.execute('SELECT COUNT(*) FROM consumer_results').fetchone()[0] == 2
        return [case('Outbox 停worker再启动补发', passed, expected='积压清零 结果2', actual='见快照', steps=steps, extra=extra)]
    if cc == 'd4':
        _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', DEFAULT_GUARD)
        ev1, rc1 = _worker(schema, lake, stop_before_ack=True)
        for e in ev1:
            wstep(e, 'worker#1')
        ev2, rc2 = _worker(schema, lake)
        for e in ev2:
            wstep(e, 'worker#2')
        snap('重复投递后')
        n_res = c.execute('SELECT COUNT(*) FROM consumer_results').fetchone()[0]
        passed = rc1 == 73 and n_res == 1
        return [case('确认前退出·重复事件结果只写一次', passed, expected='结果1行（消息可重复）', actual=f'结果{n_res}行 rc1={rc1}', steps=steps, extra=extra)]
    if cc == 'd5':
        m = re.search(r'max_attempts:\s*(\d+)', code)
        b = re.search(r'backoff_ms:\s*(\d+)', code)
        max_attempts = int(m.group(1)) if m else 4
        backoff_ms = int(b.group(1)) if b else 50
        exhaust = scenario == 'exhaust'
        _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', DEFAULT_GUARD)
        ev, rc = _worker(schema, lake, fail_first_n=(99 if exhaust else 2), max_attempts=max_attempts, backoff_ms=backoff_ms)
        for e in ev:
            wstep(e)
        snap('重试后')
        fails = _rows(c.execute('SELECT * FROM failures ORDER BY id'))
        n_res = c.execute('SELECT COUNT(*) FROM consumer_results').fetchone()[0]
        if exhaust:
            passed = any(f['reason'] == 'exhausted_permanent_failure' and f['attempts'] == max_attempts for f in fails) and n_res == 0
            want = f'次数用尽({max_attempts})写永久失败 结果0'
        else:
            passed = n_res == 1 and sum(1 for f in fails if f['reason'] == 'downstream_unavailable') == 2
            want = '失败2次后恢复 结果1'
        extra.update(max_attempts=max_attempts, backoff_ms=backoff_ms, failures=fails, mode='真实worker短退避')
        return [case('下游不可用·有限重试退避', passed, expected=want, actual=f'结果{n_res} 失败{len(fails)}', steps=steps, extra=extra)]
    # ---- 数据湖公共前置 ----
    _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', DEFAULT_GUARD)
    _place(c, 'ord-2', 'A', 1, 5000, '2026-12-08', DEFAULT_GUARD)
    if cc in ('l4', 'l5', 'f1'):
        _refund(c, 'ref-late', 'ord-1', 9900, '2026-12-08')
    if hotel:
        _book(c, 'bk-1', ['2026-12-07', '2026-12-08'], "SELECT request_id FROM bookings WHERE request_id=%s")
    if cc == 'l2':
        rawdir = Path(lake) / 'raw'
        rawdir.mkdir(parents=True, exist_ok=True)
        if scenario != 'no-bad':
            (rawdir / 'bad_missing.json').write_text(json.dumps(dict(event_id=901, request_key='bad-1', kind='order_placed', biz_date='2026-12-07')), encoding='utf-8')
            (rawdir / 'bad_date.json').write_text(json.dumps(dict(event_id=902, request_key='bad-2', kind='order_placed', net_cents=100, biz_date='not-a-date')), encoding='utf-8')
            (rawdir / 'bad_amount.json').write_text(json.dumps(dict(event_id=903, request_key='bad-3', kind='order_placed', net_cents='abc', biz_date='2026-12-07')), encoding='utf-8')
    ev, rc = _worker(schema, lake)
    if cc == 'l2' and scenario == 'all-bad':
        for f in (Path(lake) / 'raw').glob('event_*.json'):
            f.unlink()
    for e in ev:
        wstep(e)
    if cc in ('l4', 'f1'):
        dup_src = next((Path(lake) / 'raw' / f) for f in os.listdir(Path(lake) / 'raw') if f.startswith('event_'))
        (Path(lake) / 'raw' / 'event_dup.json').write_text(dup_src.read_text(encoding='utf-8'), encoding='utf-8')
        if hotel:
            _book(c, 'bk-mid', ['2026-12-08', '2026-12-09'], "SELECT request_id FROM bookings WHERE request_id=%s")
        ev2, _ = _worker(schema, lake)
        for e in ev2:
            wstep(e, 'worker#2')
    con, info = _lake_clean(lake, dedup_cond)
    extra.update(info)
    steps.append(step('清洗+去重+按业务日期写 Parquet', 'committed', {}, None,
                      f"原始{info['total']} 有效{info['valid']} 坏{info['bad']} 去重后{info['dedup']} 重复{info['dup']}", 'DuckDB'))
    if cc == 'l1':
        biz = c.execute('SELECT SUM(amount_cents) FROM orders').fetchone()[0]
        rawsum = con.execute('SELECT SUM(TRY_CAST(net_cents AS INTEGER)) FROM raw').fetchone()[0]
        passed = biz == rawsum
        return [case('业务库 vs 原始文件各能回答什么', passed, expected=f'业务净额={biz} 原始净额={rawsum}', actual='相等', steps=steps, extra=extra)]
    if cc == 'l2':
        steps.append(step('坏记录逐条列原因', 'error', {'被拒记录': info['rejected']}, None, '坏数据不进报表', '校验'))
        raw_n, valid_n, bad_n = info['total'], info['valid'], info['bad']
        report_rows, _, rerr = _report(con, DEFAULT_REPORT)
        bad_in_report = any(str(r.get('biz_date')) == 'not-a-date' or r.get('net_cents') is None for r in (report_rows or []))
        passed = (raw_n == valid_n + bad_n) and not bad_in_report and rerr is None
        if scenario == 'all-bad':
            passed = passed and valid_n == 0
        if scenario == 'no-bad':
            passed = passed and bad_n == 0
        return [case('校验字段·坏记录列原因', passed, expected=f'原始{raw_n}=有效{valid_n}+拒绝{bad_n} 且坏不进报表',
                     actual=f'原始{raw_n} 有效{valid_n} 坏{bad_n} 等式={raw_n == valid_n + bad_n} 坏进报表={bad_in_report} rerr={rerr}', steps=steps, extra=extra)]
    if cc == 'l3':
        con.execute(f"CREATE VIEW pq AS SELECT * FROM read_parquet('{(Path(lake)/'parquet').as_posix()}/**/*.parquet', hive_partitioning=true)")
        rows, cols, err = _report(con, report_sql, source='pq')
        m = re.search(r"biz_date\s*=\s*'([^']+)'", report_sql)
        hit = [f for f in info['files'] if m.group(1) in f] if m else info['files']
        steps.append(step('你的 DuckDB SQL 真读 Parquet', 'read', {'报表': rows or [], '命中文件': hit}, report_sql, err, '学生'))
        base_rows, _, _ = _report(con, DEFAULT_REPORT)
        base_net = {str(r['biz_date']): r['net_cents'] for r in (base_rows or [])}
        got_net = {str(r.get('biz_date')): r.get('net_cents') for r in (rows or [])}
        values_ok = (got_net == base_net)
        extra.update(hit_files=hit, columns=cols, base_net=base_net)
        passed = err is None and len(hit) >= 1 and cols and 'biz_date' in cols and 'net_cents' in cols and values_ok
        return [case('改一条 DuckDB SQL 看读了哪些分区', passed, expected='查询成功且命中分区文件≥1 含 biz_date/net_cents 列',
                     actual=f'err={err} 命中{len(hit)} 列{cols} 值符合={values_ok}', error=err or (None if values_ok else '净额与基准不符（如全0）'), steps=steps, extra=extra)]
    if cc == 'l4':
        con2, info2 = _lake_clean(lake, dedup_cond, subdir='parquet2')
        rb1 = duckdb.connect(); rb1.execute("SET memory_limit='256MB'")
        p1 = rb1.execute(f"SELECT * FROM read_parquet('{(Path(lake)/'parquet').as_posix()}/**/*.parquet', hive_partitioning=true) ORDER BY event_id").fetchall()
        rb2 = duckdb.connect(); rb2.execute("SET memory_limit='256MB'")
        p2 = rb2.execute(f"SELECT * FROM read_parquet('{(Path(lake)/'parquet2').as_posix()}/**/*.parquet', hive_partitioning=true) ORDER BY event_id").fetchall()
        r1, _, e1 = _report(con, DEFAULT_REPORT)
        r2, _, e2 = _report(con2, DEFAULT_REPORT)
        same = (p1 == p2) and (r1 == r2) and ([f.replace('parquet/', '') for f in info['files']] == [f.replace('parquet2/', '') for f in info2['files']])
        net = sum(r['net_cents'] for r in r1 or [])
        nights = sum(r['room_nights'] for r in r1 or [])
        perday = {str(r['biz_date']): r['room_nights'] for r in r1 or []}
        netday = {str(r['biz_date']): r['net_cents'] for r in r1 or []}
        steps.append(step('同批原始连跑两次独立读回比较', 'read', {'跑1': r1 or [], '跑2': r2 or []}, None, f'两次一致={same}', '对账'))
        extra.update(run2=info2, perday=perday, netday=netday)
        passed = (e1 is None and e2 is None and same and net == 5000 and nights == 4
                  and perday.get('2026-12-07') == 1 and perday.get('2026-12-08') == 2 and perday.get('2026-12-09') == 1
                  and netday.get('2026-12-08') == -4900 and info['dup'] >= 1)
        return [case('重复/迟到/跨午夜重跑对账', passed, expected='两次Parquet独立读回一致 净5000 间夜4 逐日{07:1,08:2,09:1} 迟到退款归08',
                     actual=f'净{net} 间夜{nights} 逐日{perday} 重复{info["dup"]} 一致={same}', steps=steps, extra=extra)]
    if cc == 'l5':
        rows, cols, err = _report(con, report_sql)
        want = [dict(biz_date='2026-12-07', net_cents=9900, room_nights=0), dict(biz_date='2026-12-08', net_cents=-4900, room_nights=0)]
        got = sorted([{k: (str(r.get(k)) if k == 'biz_date' else r.get(k)) for k in ('biz_date', 'net_cents', 'room_nights')} for r in (rows or [])], key=lambda x: str(x['biz_date']))
        steps.append(step('改口径从原始重建', 'read', {'报表': rows or []}, report_sql,
                          err or f"输入{info['total']} 有效{info['valid']} 坏{info['bad']} 去重后{info['dedup']}", '学生'))
        extra.update(columns=cols, expected=want)
        passed = err is None and got == sorted(want, key=lambda x: str(x['biz_date']))
        return [case('改报表口径从原始重建', passed, expected=f'列biz_date/net_cents/room_nights 行值{want}',
                     actual=f'err={err} 行{got}', error=err or (None if passed else '报表列/行/值与基准不符'), steps=steps, extra=extra)]
    if cc == 'f1':
        r_retry = _place(c, 'ord-1', 'A', 1, 9900, '2026-12-07', DEFAULT_GUARD)
        steps.append(step('同请求键重试（真实事务）', 'committed', {}, None, f'replayed={r_retry.get("replayed")}', 'API'))
        redel = c.execute('SELECT MIN(event_id) FROM outbox').fetchone()[0]
        c.execute('UPDATE outbox SET published=false WHERE event_id=%s', (redel,))
        steps.append(step('把同一事件重新置为待发送（模拟重投）', 'pending', {}, None, f'event_id={redel}', 'outbox'))
        ev3, rc3 = _worker(schema, lake, stop_before_ack=True)
        for e in ev3:
            wstep(e, 'worker#3')
        ev4, _ = _worker(schema, lake)
        for e in ev4:
            wstep(e, 'worker#4')
        con3, info3 = _lake_clean(lake, dedup_cond, subdir='parquet3')
        rows, _, err = _report(con3, DEFAULT_REPORT)
        net = sum(r['net_cents'] for r in rows or [])
        n_orders = c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
        n_res = c.execute('SELECT COUNT(*) FROM consumer_results').fetchone()[0]
        deliver_n = sum(1 for e in ev3 + ev4 if e['op'] == 'deliver' and e.get('event_id') == redel)
        res_redel = c.execute('SELECT COUNT(*) FROM consumer_results WHERE event_id=%s', (str(redel),)).fetchone()[0]
        extra.update(pod='未实测（无 kind 集群）', net=net, redelivered_event_id=redel, deliver_count=deliver_n, res_for_redel=res_redel)
        steps.append(step('Pod 环节', 'illustrative', {}, None, '未实测：无 kind 集群，不计入通过', 'K8s'))
        passed = err is None and net == 5000 and n_orders == 2 and n_res >= 1 and r_retry.get('replayed') and deliver_n == 2 and res_redel == 1 and rc3 == 73
        return [case('结课：同一订单贯穿各环节', passed, expected='净5000 订单2(重试回放不新增) 结果去重正确',
                     actual=f'净{net} 订单{n_orders} 结果{n_res}', error=err or (None if passed else '去重条件错误导致净额不符'), steps=steps, extra=extra)]
    return [case(cc, False, error='unknown cloud case', steps=steps, extra=extra)]


ENGINES.update({'cloud': _run_cloud})
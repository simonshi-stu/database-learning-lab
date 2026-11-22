"""第 1 批末尾综合练习：从空目录开写（销售 / 酒店），真实 PostgreSQL + 可信验收 harness。

学生提交 schema.sql / service.py / tests_extra.py 三个文件；平台在子进程跑基线测试，
最终用新连接复核。Web 服务不执行学生代码。
"""
import json
import os
import subprocess
import sys

from . import pglab
from .course_engines import case, step, ENGINES
from .course_pg import LAYERS, _unavailable

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONTRACT_SALES = """业务图：前端下单 → POST /orders → 事务内幂等检查 + 条件扣减 + 订单/明细/流水 → 提交或回滚。
接口约定：service.place_order(payload) -> {'status':'PLACED','order_id',...,'replayed':False}
  或 {'error':{'code':'INSUFFICIENT_STOCK'}} / {'error':{'code':'IDEMPOTENCY_CONFLICT'}}；同键同内容回放 replayed=True。
必需表：inventory(sku text PK, qty int CHECK>=0)；orders(request_key text PK, request_hash text, status text, total_cents int)；
  order_items(order_id text, sku text, qty int)；movements(id serial PK, kind text, reference text, delta int)。
初始数据：harness 插入 inventory A=1。测试：两笔抢最后一件 / 同键重试 / 同键不同内容 / 新连接复核。"""

CONTRACT_HOTEL = """业务图：前端订房 → POST /reservations → 事务内幂等检查 + 两晚同房交集 + 逐晚占用 → 提交或回滚。
接口约定：service.reserve(payload) -> {'status':'BOOKED','decision':{'room_id','nights'},'replayed':False}
  或 {'status':'REJECTED'}；payload.inject_failure=True 时第二晚写入后抛异常（整单回滚）；同键回放 replayed=True。
必需表：rooms(room_id text PK, room_type text)；room_nights(room_id text, night text, blocked int, PK(room_id,night))；
  bookings(request_id text PK, request_hash text, status text, decision text)；booking_nights(request_id text, room_id text, night text, PK(request_id,night), UNIQUE(room_id,night) 防同晚双占)。
初始数据：S101/S102；12-08/09 可用、12-10 封锁；PRE 占 S101 的 12-08。半开区间：离店日不占。"""

STARTER_SCHEMA_SALES = "-- 从空目录开写：按契约建表（inventory / orders / order_items / movements）\n"
STARTER_SERVICE_SALES = """\"\"\"按契约实现 place_order(payload)。可用：from lab import pglab; pglab.connect(os.environ['BUILD_SCHEMA'])\"\"\"
import os
from lab import pglab


def place_order(payload):
    raise NotImplementedError('先写事务边界与幂等检查')
"""
STARTER_SCHEMA_HOTEL = "-- 从空目录开写：按契约建表（rooms / room_nights / bookings / booking_nights）\n"
STARTER_SERVICE_HOTEL = """\"\"\"按契约实现 reserve(payload)。可用：from lab import pglab; pglab.connect(os.environ['BUILD_SCHEMA'])\"\"\"
import os
from lab import pglab


def reserve(payload):
    raise NotImplementedError('先写两晚同房交集与事务边界')
"""

REF_SERVICE_SALES = '''import hashlib
import json
import os
import uuid
from lab import pglab


def _conn():
    return pglab.connect(os.environ['BUILD_SCHEMA'])


def place_order(payload):
    items = sorted(payload['items'], key=lambda i: i['sku'])
    digest = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
    c = _conn()
    try:
        c.execute('BEGIN')
        old = c.execute('SELECT request_hash, status FROM orders WHERE request_key=%s',
                        (payload['request_key'],)).fetchone()
        if old:
            if old[0] != digest:
                c.execute('ROLLBACK')
                return {'error': {'code': 'IDEMPOTENCY_CONFLICT'}}
            c.execute('COMMIT')
            return {'status': old[1], 'replayed': True}
        oid = uuid.uuid4().hex
        c.execute('INSERT INTO orders(request_key, request_hash, status, total_cents) VALUES(%s,%s,%s,0)',
                  (payload['request_key'], digest, 'PLACED'))
        total = 0
        for it in items:
            cur = c.execute('UPDATE inventory SET qty=qty-%s WHERE sku=%s AND qty>=%s',
                            (it['qty'], it['sku'], it['qty']))
            if cur.rowcount != 1:
                c.execute('ROLLBACK')
                return {'error': {'code': 'INSUFFICIENT_STOCK'}}
            c.execute('INSERT INTO order_items(order_id, sku, qty) VALUES(%s,%s,%s)',
                      (oid, it['sku'], it['qty']))
            c.execute("INSERT INTO movements(kind, reference, delta) VALUES('SALE',%s,%s)",
                      (oid, -it['qty']))
            total += 100 * it['qty']
        c.execute('UPDATE orders SET total_cents=%s WHERE request_key=%s', (total, payload['request_key']))
        c.execute('COMMIT')
        return {'status': 'PLACED', 'order_id': oid, 'replayed': False}
    except Exception:
        c.execute('ROLLBACK')
        raise
    finally:
        c.close()
'''

REF_SCHEMA_SALES = """CREATE TABLE inventory(sku text PRIMARY KEY, qty int NOT NULL CHECK (qty >= 0));
CREATE TABLE orders(request_key text PRIMARY KEY, request_hash text NOT NULL, status text NOT NULL, total_cents int NOT NULL DEFAULT 0);
CREATE TABLE order_items(order_id text NOT NULL, sku text NOT NULL, qty int NOT NULL);
CREATE TABLE movements(id serial PRIMARY KEY, kind text NOT NULL, reference text NOT NULL, delta int NOT NULL);
"""

REF_SERVICE_HOTEL = '''import hashlib
import json
import os
from datetime import date, timedelta
from lab import pglab


def _conn():
    return pglab.connect(os.environ['BUILD_SCHEMA'])


def reserve(payload):
    ident = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    start = date.fromisoformat(payload['check_in'])
    end = date.fromisoformat(payload['check_out'])
    nights = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)]
    c = _conn()
    try:
        c.execute('BEGIN')
        old = c.execute('SELECT request_hash, status, decision FROM bookings WHERE request_id=%s',
                        (payload['request_id'],)).fetchone()
        if old:
            if old[0] != ident:
                c.execute('ROLLBACK')
                return {'error': {'code': 'IDEMPOTENCY_CONFLICT'}}
            c.execute('COMMIT')
            return {'status': old[1], 'replayed': True, 'decision': json.loads(old[2])}
        candidates = None
        for n in nights:
            rows = c.execute('SELECT room_id FROM room_nights WHERE night=%s AND blocked=0 '
                             'AND room_id NOT IN (SELECT room_id FROM booking_nights WHERE night=%s)',
                             (n, n)).fetchall()
            ids = {r[0] for r in rows}
            candidates = ids if candidates is None else candidates & ids
        if not candidates:
            c.execute("INSERT INTO bookings(request_id, request_hash, status, decision) VALUES(%s,%s,'REJECTED','{}')",
                      (payload['request_id'], ident))
            c.execute('COMMIT')
            return {'status': 'REJECTED', 'replayed': False}
        room = sorted(candidates)[0]
        decision = json.dumps({'room_id': room, 'nights': nights})
        c.execute("INSERT INTO bookings(request_id, request_hash, status, decision) VALUES(%s,%s,'BOOKED',%s)",
                  (payload['request_id'], ident, decision))
        for i, n in enumerate(nights):
            if payload.get('inject_failure') and i == 1:
                raise RuntimeError('injected second-night failure')
            c.execute('INSERT INTO booking_nights(request_id, room_id, night) VALUES(%s,%s,%s)',
                      (payload['request_id'], room, n))
        c.execute('COMMIT')
        return {'status': 'BOOKED', 'replayed': False, 'decision': json.loads(decision)}
    except Exception:
        c.execute('ROLLBACK')
        raise
    finally:
        c.close()
'''

REF_SCHEMA_HOTEL = """CREATE TABLE rooms(room_id text PRIMARY KEY, room_type text NOT NULL);
CREATE TABLE room_nights(room_id text NOT NULL, night text NOT NULL, blocked int NOT NULL DEFAULT 0, PRIMARY KEY(room_id, night));
CREATE TABLE bookings(request_id text PRIMARY KEY, request_hash text NOT NULL, status text NOT NULL, decision text NOT NULL);
CREATE TABLE booking_nights(request_id text NOT NULL, room_id text NOT NULL, night text NOT NULL, PRIMARY KEY(request_id, night), UNIQUE(room_id, night));
"""


def _build(lesson, code, run_dir, files, kind, stage=None):
    ok, why = pglab.available()
    if not ok:
        return _unavailable(why, '从空目录开写 · ' + kind)
    schema = pglab.new_schema()
    ws = run_dir / 'build'
    ws.mkdir(parents=True, exist_ok=True)
    for name in ('schema.sql', 'service.py', 'tests_extra.py'):
        short = {'schema.sql': 'schema', 'service.py': 'service', 'tests_extra.py': 'tests'}[name]
        content = (files or {}).get(name) or (files or {}).get(short, '')
        (ws / name).write_text(content, encoding='utf-8')
    env = dict(os.environ, BUILD_SCHEMA=schema, PYTHONDONTWRITEBYTECODE='1')
    try:
        proc = subprocess.run([sys.executable, '-B', '-m', 'lab.build_harness', str(ws), kind] + ([stage] if stage else []),
                              cwd=ROOT, env=env, capture_output=True, timeout=60)
        report = json.loads(proc.stdout.decode('utf-8', 'replace') or '{}')
    except subprocess.TimeoutExpired:
        report = dict(tests=[], error='验收超时（60 秒）', passed=False)
    except json.JSONDecodeError:
        report = dict(tests=[], error='harness 输出异常：' + proc.stderr.decode('utf-8', 'replace')[-300:], passed=False)
    steps = [step(t['name'], 'committed' if t['passed'] else 'error', {}, None,
                  t.get('error') or '通过。', '验收 harness') for t in report.get('tests', [])]
    steps.append(step('新连接复核 schema', 'committed', {}, None,
                      f'schema={schema}；基线测试含新连接查询。', '新连接'))
    passed_n = sum(1 for t in report.get('tests', []) if t['passed'])
    return [case('从空目录开写 · ' + kind, bool(report.get('passed')),
                 expected='基线测试全过 + 新连接复核',
                 actual=f'{passed_n}/{len(report.get("tests", []))} 通过',
                 error=report.get('error'), steps=steps,
                 extra=dict(schema=schema, layers=LAYERS))]


ENGINES.update({
    'pg_build_sales': lambda lesson, code, rd, choice='', scenario='', files=None: _build(lesson, code, rd, files, 'sales'),
    'pg_build_hotel': lambda lesson, code, rd, choice='', scenario='', files=None: _build(lesson, code, rd, files, 'hotel'),
})

def _guide(lesson, code, run_dir, choice='', scenario='', files=None):
    return _build(lesson, code, run_dir, files, lesson['guide_kind'], lesson['guide_stage'])


GUIDE_STARTERS = {
    ('sales', 'schema'): REF_SCHEMA_SALES.replace('sku text PRIMARY KEY', 'sku text', 1),
    ('sales', 'single'): REF_SERVICE_SALES.replace(
        "c.execute('INSERT INTO orders(request_key, request_hash, status, total_cents) VALUES(%s,%s,%s,0)',\n                  (payload['request_key'], digest, 'PLACED'))",
        "# TODO: 插入订单行（orders 表）"),
    ('sales', 'insufficient'): REF_SERVICE_SALES.replace(
        "            if cur.rowcount != 1:\n                c.execute('ROLLBACK')\n                return {'error': {'code': 'INSUFFICIENT_STOCK'}}",
        "            # TODO: 检查影响行数；不足则回滚并返回 INSUFFICIENT_STOCK"),
    ('sales', 'retry'): REF_SERVICE_SALES.replace(
        "        old = c.execute('SELECT request_hash, status FROM orders WHERE request_key=%s',\n                        (payload['request_key'],)).fetchone()\n        if old:\n            if old[0] != digest:\n                c.execute('ROLLBACK')\n                return {'error': {'code': 'IDEMPOTENCY_CONFLICT'}}\n            c.execute('COMMIT')\n            return {'status': old[1], 'replayed': True}",
        "        # TODO: 同键检查：同内容回放原单、不同内容返回冲突"),
    ('sales', 'race'): REF_SERVICE_SALES.replace(
        "cur = c.execute('UPDATE inventory SET qty=qty-%s WHERE sku=%s AND qty>=%s',\n                            (it['qty'], it['sku'], it['qty']))",
        "cur = c.execute('UPDATE inventory SET qty=qty-%s WHERE sku=%s',\n                            (it['qty'], it['sku']))  # TODO: 加库存条件"),
    ('hotel', 'schema'): REF_SCHEMA_HOTEL.replace('PRIMARY KEY(request_id, night)', '', 1),
    ('hotel', 'single'): REF_SERVICE_HOTEL.replace(
        "c.execute('INSERT INTO booking_nights(request_id, room_id, night) VALUES(%s,%s,%s)',\n                      (payload['request_id'], room, n))",
        "# TODO: 写入房晚占用（booking_nights）"),
    ('hotel', 'twonights'): REF_SERVICE_HOTEL.replace(
        "            candidates = ids if candidates is None else candidates & ids",
        "            candidates = ids  # TODO: 求每晚可用房间的交集，保证两晚同房"),
    ('hotel', 'conflict'): REF_SERVICE_HOTEL.replace(
        "            if payload.get('inject_failure') and i == 1:\n                raise RuntimeError('injected second-night failure')",
        "            # TODO: 第二晚写入前按 inject_failure 注入失败，让整单回滚"),
    ('hotel', 'retry'): REF_SERVICE_HOTEL.replace(
        "        old = c.execute('SELECT request_hash, status, decision FROM bookings WHERE request_id=%s',\n                        (payload['request_id'],)).fetchone()\n        if old:\n            if old[0] != ident:\n                c.execute('ROLLBACK')\n                return {'error': {'code': 'IDEMPOTENCY_CONFLICT'}}\n            c.execute('COMMIT')\n            return {'status': old[1], 'replayed': True, 'decision': json.loads(old[2])}",
        "        # TODO: 同键检查：同内容回放、不同内容冲突"),
}

ENGINES.update({'pg_guide': _guide})

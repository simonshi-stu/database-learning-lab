"""销售题：两笔请求抢最后一件库存 + 同键幂等重试。

来源：commerce-ledger @ 0659bd591d3b23b05f21e07609acd3dbe8f18c8b
  app/service.py place_order (L76-103)、app/db.py Database.transaction (L74-83)、app/schema.sql
教学简化：单商品、无认证/迁移/HTTP 框架；并发固定为两线程 + 一次同键重试；
异常→HTTP 映射沿用 app/main.py（IntegrityError→409 CONSTRAINT_CONFLICT，锁忙→503）。
"""
import sqlite3
import threading
import uuid

from ..harness import call_user, validate_sql
from ..sandbox import (BusinessError, compile_solution, canonical, digest_of)
from ..trace import TracedDatabase, connect_raw

TABLES = ['products', 'inventory', 'orders', 'order_items', 'stock_movements']

SCHEMA = """
CREATE TABLE products (
 id INTEGER PRIMARY KEY, sku TEXT NOT NULL UNIQUE,
 name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
 price_cents INTEGER NOT NULL CHECK(typeof(price_cents)='integer' AND price_cents BETWEEN 1 AND 100000000),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);
CREATE TABLE inventory (
 product_id INTEGER PRIMARY KEY REFERENCES products(id),
 available INTEGER NOT NULL DEFAULT 0 CHECK(typeof(available)='integer' AND available BETWEEN 0 AND 1000000000)
);
CREATE TABLE orders (
 id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PLACED','CANCELLED','FULFILLED')),
 total_cents INTEGER NOT NULL CHECK(typeof(total_cents)='integer' AND total_cents>=0),
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE order_items (
 order_id TEXT NOT NULL REFERENCES orders(id), product_id INTEGER NOT NULL REFERENCES products(id),
 quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity BETWEEN 1 AND 10000),
 unit_price_cents INTEGER NOT NULL CHECK(typeof(unit_price_cents)='integer' AND unit_price_cents>0),
 PRIMARY KEY(order_id,product_id)
);
CREATE TABLE stock_movements (
 id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
 delta INTEGER NOT NULL CHECK(typeof(delta)='integer' AND delta<>0),
 kind TEXT NOT NULL CHECK(kind IN ('ADJUST','SALE','CANCEL')),
 reference TEXT NOT NULL, reason TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 UNIQUE(product_id,kind,reference)
);
"""

REQUEST_A = {'request_key': 'buy-A', 'items': [{'product_id': 1, 'quantity': 1}]}
REQUEST_B = {'request_key': 'buy-B', 'items': [{'product_id': 1, 'quantity': 1}]}

STARTER = '''# 起始代码：补全幂等检查与条件扣减。
# 可用 API：
#   with db.transaction() as c:      开始/提交/回滚写事务（BEGIN IMMEDIATE）
#   c.execute(sql, params)           执行 SQL；写操作看 .rowcount，读操作 .fetchone()/.fetchall()
#   fail(status, code, message)      抛出业务错误（映射为对应 HTTP 响应）
#   digest_of(obj) / new_id()        内容摘要 / 新订单号
with db.transaction() as c:
    # TODO 1: 按 request_key 查旧订单；同键同内容直接返回原订单（replayed=True）
    # TODO 2: 插入订单，用带库存条件的 UPDATE 扣减，并检查 rowcount == 1
    # TODO 3: 写 order_items 与 SALE 流水，更新订单总额
    return {'order_id': 'TODO', 'status': 'PLACED', 'total_cents': 0, 'replayed': False}
'''

REFERENCE = '''# 参考解：改编自 commerce-ledger app/service.py place_order（单商品简化）
digest = digest_of(request['items'])
with db.transaction() as c:
    old = c.execute("SELECT id,request_hash,status,total_cents FROM orders WHERE request_key=?",
                    (request['request_key'],)).fetchone()
    if old:
        if old['request_hash'] != digest:
            fail(409, 'IDEMPOTENCY_CONFLICT', '同一幂等键对应不同订单')
        return {'order_id': old['id'], 'status': old['status'],
                'total_cents': old['total_cents'], 'replayed': True}
    order_id = new_id()
    c.execute("INSERT INTO orders(id,request_key,request_hash,status,total_cents) VALUES(?,?,?,'PLACED',0)",
              (order_id, request['request_key'], digest))
    total = 0
    for item in request['items']:
        pid, qty = item['product_id'], item['quantity']
        product = c.execute(
            "SELECT p.*,i.available FROM products p JOIN inventory i ON i.product_id=p.id "
            "WHERE p.id=? AND p.active=1", (pid,)).fetchone()
        if product is None:
            fail(404, 'PRODUCT_NOT_FOUND', '商品不存在或已下架')
        # 条件扣减：把库存判断放进 UPDATE 的 WHERE，而不是依赖先查到的值
        updated = c.execute(
            "UPDATE inventory SET available=available-:qty WHERE product_id=:pid AND available>=:qty",
            {'qty': qty, 'pid': pid})
        if updated.rowcount != 1:
            fail(409, 'INSUFFICIENT_STOCK', '库存不足，整单未创建')
        total += product['price_cents'] * qty
        c.execute("INSERT INTO order_items VALUES(?,?,?,?)",
                  (order_id, pid, qty, product['price_cents']))
        c.execute("INSERT INTO stock_movements(product_id,delta,kind,reference,reason) "
                  "VALUES(?,?,'SALE',?,'订单占用可售库存')", (pid, -qty, order_id))
    c.execute("UPDATE orders SET total_cents=? WHERE id=?", (total, order_id))
    return {'order_id': order_id, 'status': 'PLACED', 'total_cents': total, 'replayed': False}
'''

WRONG_READ_THEN_WRITE = '''# 错误解：先在事务外查库存，再无条件扣减，也不检查影响行数。
# 想一想：两笔请求都读到 available=1 之后会发生什么？
digest = digest_of(request['items'])
for item in request['items']:
    row = db.query("SELECT available FROM inventory WHERE product_id=?",
                   (item['product_id'],)).fetchone()
    if row is None or row['available'] < item['quantity']:
        fail(409, 'INSUFFICIENT_STOCK', '库存不足')
with db.transaction() as c:
    old = c.execute("SELECT id,request_hash,status,total_cents FROM orders WHERE request_key=?",
                    (request['request_key'],)).fetchone()
    if old:
        if old['request_hash'] != digest:
            fail(409, 'IDEMPOTENCY_CONFLICT', '同一幂等键对应不同订单')
        return {'order_id': old['id'], 'status': old['status'],
                'total_cents': old['total_cents'], 'replayed': True}
    order_id = new_id()
    c.execute("INSERT INTO orders(id,request_key,request_hash,status,total_cents) VALUES(?,?,?,'PLACED',0)",
              (order_id, request['request_key'], digest))
    total = 0
    for item in request['items']:
        pid, qty = item['product_id'], item['quantity']
        product = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        # 错误：无条件扣减，没有 available>=qty 条件，也不看 rowcount
        c.execute("UPDATE inventory SET available=available-:qty WHERE product_id=:pid",
                  {'qty': qty, 'pid': pid})
        total += product['price_cents'] * qty
        c.execute("INSERT INTO order_items VALUES(?,?,?,?)",
                  (order_id, pid, qty, product['price_cents']))
        c.execute("INSERT INTO stock_movements(product_id,delta,kind,reference,reason) "
                  "VALUES(?,?,'SALE',?,'订单占用可售库存')", (pid, -qty, order_id))
    c.execute("UPDATE orders SET total_cents=? WHERE id=?", (total, order_id))
    return {'order_id': order_id, 'status': 'PLACED', 'total_cents': total, 'replayed': False}
'''

WRONG_NO_IDEMPOTENCY = '''# 错误解：条件扣减是对的，但漏了幂等检查。
# 前两笔看起来都正常；同键重试时会发生什么？
with db.transaction() as c:
    order_id = new_id()
    c.execute("INSERT INTO orders(id,request_key,request_hash,status,total_cents) VALUES(?,?,?,'PLACED',0)",
              (order_id, request['request_key'], digest_of(request['items'])))
    total = 0
    for item in request['items']:
        pid, qty = item['product_id'], item['quantity']
        product = c.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
        if product is None:
            fail(404, 'PRODUCT_NOT_FOUND', '商品不存在或已下架')
        updated = c.execute(
            "UPDATE inventory SET available=available-:qty WHERE product_id=:pid AND available>=:qty",
            {'qty': qty, 'pid': pid})
        if updated.rowcount != 1:
            fail(409, 'INSUFFICIENT_STOCK', '库存不足，整单未创建')
        total += product['price_cents'] * qty
        c.execute("INSERT INTO order_items VALUES(?,?,?,?)",
                  (order_id, pid, qty, product['price_cents']))
        c.execute("INSERT INTO stock_movements(product_id,delta,kind,reference,reason) "
                  "VALUES(?,?,'SALE',?,'订单占用可售库存')", (pid, -qty, order_id))
    c.execute("UPDATE orders SET total_cents=? WHERE id=?", (total, order_id))
    return {'order_id': order_id, 'status': 'PLACED', 'total_cents': total, 'replayed': False}
'''

SQL_REFERENCE = ("UPDATE inventory SET available=available-:qty "
                 "WHERE product_id=:pid AND available>=:qty")
SQL_WRONG = "UPDATE inventory SET available=available-:qty WHERE product_id=:pid"


def _sql_solution(update_sql):
    def fn(db, request):
        digest = digest_of(request['items'])
        with db.transaction() as c:
            old = c.execute("SELECT id,request_hash,status,total_cents FROM orders WHERE request_key=?",
                            (request['request_key'],)).fetchone()
            if old:
                if old['request_hash'] != digest:
                    raise BusinessError(409, 'IDEMPOTENCY_CONFLICT', '同一幂等键对应不同订单')
                return {'order_id': old['id'], 'status': old['status'],
                        'total_cents': old['total_cents'], 'replayed': True}
            order_id = uuid.uuid4().hex
            c.execute("INSERT INTO orders(id,request_key,request_hash,status,total_cents) VALUES(?,?,?,'PLACED',0)",
                      (order_id, request['request_key'], digest))
            total = 0
            for item in request['items']:
                pid, qty = item['product_id'], item['quantity']
                product = c.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
                if product is None:
                    raise BusinessError(404, 'PRODUCT_NOT_FOUND', '商品不存在或已下架')
                updated = c.execute(update_sql, {'qty': qty, 'pid': pid})
                if updated.rowcount != 1:
                    raise BusinessError(409, 'INSUFFICIENT_STOCK', '库存不足，整单未创建')
                total += product['price_cents'] * qty
                c.execute("INSERT INTO order_items VALUES(?,?,?,?)",
                          (order_id, pid, qty, product['price_cents']))
                c.execute("INSERT INTO stock_movements(product_id,delta,kind,reference,reason) "
                          "VALUES(?,?,'SALE',?,'订单占用可售库存')", (pid, -qty, order_id))
            c.execute("UPDATE orders SET total_cents=? WHERE id=?", (total, order_id))
            return {'order_id': order_id, 'status': 'PLACED', 'total_cents': total, 'replayed': False}
    return fn


def validate(request):
    key = request.get('request_key')
    if not isinstance(key, str) or not 1 <= len(key) <= 80:
        raise BusinessError(422, 'VALIDATION_ERROR', 'request_key 必须是 1-80 字符的字符串')
    items = request.get('items')
    if not isinstance(items, list) or not items:
        raise BusinessError(422, 'VALIDATION_ERROR', 'items 必须是非空列表')
    for item in items:
        if type(item.get('product_id')) is not int or type(item.get('quantity')) is not int \
                or not 1 <= item['quantity'] <= 10000:
            raise BusinessError(422, 'VALIDATION_ERROR', 'product_id/quantity 必须是合法整数')


def display(body, status):
    if status == 200:
        if body.get('replayed'):
            return f"幂等回放：返回原订单 {body['order_id']}（未新建、未扣库存）"
        return f"下单成功：订单 {body['order_id']}，共 {body['total_cents'] / 100:.2f} 元"
    code = body.get('error', {}).get('code', '')
    return {
        'INSUFFICIENT_STOCK': '库存不足，整单未创建',
        'CONSTRAINT_CONFLICT': '约束冲突：数据库兜底拦截了非法写入（语义不是“库存不足”）',
        'DATABASE_BUSY': '数据库繁忙：未写入任何数据，请用原请求键重试',
        'IDEMPOTENCY_CONFLICT': '同一请求键对应不同内容，已拒绝',
    }.get(code, f"请求失败：{body.get('error', {}).get('message', status)}")


def execute(ctx):
    rec, db_path = ctx['rec'], ctx['db_path']
    raw = connect_raw(db_path)
    raw.executescript(SCHEMA)
    raw.execute("INSERT INTO products(id,sku,name,price_cents) VALUES(1,'SKU-LAST','最后一件商品',9900)")
    raw.execute("INSERT INTO inventory(product_id,available) VALUES(1,1)")
    rec.snapshot(raw, TABLES, 'system', 'initial', '初始数据（库存=1）')
    raw.close()

    def _fail(status, code, message):
        raise BusinessError(status, code, message)

    helpers = {'fail': _fail, 'digest_of': digest_of, 'canonical': canonical,
               'new_id': lambda: uuid.uuid4().hex}
    if ctx['mode'] == 'sql':
        update_sql = validate_sql(ctx['code'], 'inventory', ['qty', 'pid'])
        fn = _sql_solution(update_sql)
    else:
        fn = compile_solution(ctx['code'], helpers,
                              lambda *a: rec.emit('stdout', 'learner', ' '.join(str(x) for x in a)))

    scenario = ctx['scenario']
    if scenario == 'race':
        rec.emit('illustrative', 'system', '教学示意：两笔请求同时到达（真实执行用屏障同步两个线程、各自独立连接）')
        barrier = threading.Barrier(2)
        query_barrier = threading.Barrier(2)
        results = {}

        def run_actor(actor, request):
            db = TracedDatabase(db_path, rec, actor, TABLES, query_barrier=query_barrier)
            barrier.wait(timeout=10)
            results[actor] = call_user(rec, actor, request, fn, db, validate, display)

        threads = [threading.Thread(target=run_actor, args=('A', REQUEST_A)),
                   threading.Thread(target=run_actor, args=('B', REQUEST_B))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        winner = next((a for a in ('A', 'B') if results.get(a, {}).get('status') == 200), 'A')
        retry_request = REQUEST_A if winner == 'A' else REQUEST_B
        db = TracedDatabase(db_path, rec, 'retry', TABLES)
        rec.emit('note', 'retry', f'用成功那笔（{winner}）的请求键、同内容重试（验证幂等）')
        call_user(rec, 'retry', retry_request, fn, db, validate, display)
    elif scenario == 'busy':
        holder = connect_raw(db_path)
        holder.execute('BEGIN IMMEDIATE')
        rec.emit('note', 'holder', '外部连接持有写锁 · BEGIN IMMEDIATE（真实观测，模拟另一事务占锁）')
        db = TracedDatabase(db_path, rec, 'A', TABLES, busy_ms=100)
        call_user(rec, 'A', REQUEST_A, fn, db, validate, display)
        holder.rollback()
        holder.close()
        rec.emit('note', 'holder', '外部连接释放写锁 · ROLLBACK')
        db = TracedDatabase(db_path, rec, 'A-retry', TABLES)
        call_user(rec, 'A-retry', REQUEST_A, fn, db, validate, display)
    else:
        raise BusinessError(422, 'UNKNOWN_SCENARIO', scenario)

    raw = connect_raw(db_path)
    rec.snapshot(raw, TABLES, 'system', 'final', '最终状态')
    raw.close()
    return {'scenario': scenario}


def _check(name, expected, actual, passed):
    return {'name': name, 'expected': expected, 'actual': actual, 'passed': bool(passed)}


def judge(scenario, events, snapshots, responses):
    final = snapshots[-1]['tables']
    inv = final['inventory'][0]['available']
    orders = final['orders']
    sales = [m for m in final['stock_movements'] if m['kind'] == 'SALE']
    items = final['order_items']
    checks, notes = [], []
    if scenario == 'race':
        main = [r for r in responses if r['actor'] in ('A', 'B')]
        ok = [r for r in main if r['status'] == 200]
        insufficient = [r for r in main if r['status'] == 409
                        and r['body'].get('error', {}).get('code') == 'INSUFFICIENT_STOCK']
        retry = next((r for r in responses if r['actor'] == 'retry'), None)
        retry_writes = [e for e in events if e['actor'] == 'retry'
                        and e['kind'] == 'sql' and e.get('write')]
        checks.append(_check('最终库存为 0', 0, inv, inv == 0))
        checks.append(_check('成功订单恰 1 笔且 PLACED', '1 笔 PLACED',
                             f"{len(orders)} 笔 {[o['status'] for o in orders]}",
                             len(orders) == 1 and orders[0]['status'] == 'PLACED'))
        checks.append(_check('有效 SALE 流水恰 1 条且 delta=-1', '1 条 delta=-1',
                             f"{len(sales)} 条 {[m['delta'] for m in sales]}",
                             len(sales) == 1 and sales[0]['delta'] == -1))
        checks.append(_check('订单明细恰 1 行', 1, len(items), len(items) == 1))
        checks.append(_check('两笔主响应恰一笔 200', 1, len(ok), len(ok) == 1))
        checks.append(_check('另一笔为 409 INSUFFICIENT_STOCK', '409 INSUFFICIENT_STOCK',
                             '; '.join(f"{r['actor']}:{r['status']} {r['body'].get('error', {}).get('code', r['body'].get('status', ''))}" for r in main),
                             len(insufficient) == 1))
        if retry and ok:
            same = retry['body'].get('order_id') == ok[0]['body'].get('order_id')
            checks.append(_check('同键重试返回原订单', '200 且 replayed=True 且 order_id 相同',
                                 f"{retry['status']} replayed={retry['body'].get('replayed')} same_id={same}",
                                 retry['status'] == 200 and retry['body'].get('replayed') is True and same))
        else:
            checks.append(_check('同键重试返回原订单', '200 且 replayed=True', '缺少重试响应', False))
        checks.append(_check('重试过程无写入 SQL', 0, len(retry_writes), len(retry_writes) == 0))
        db_ok = all(c['passed'] for c in checks[:4])
        if db_ok and not checks[5]['passed']:
            notes.append('数据库结果正确但失败语义不对：SQLite BEGIN IMMEDIATE 已把写入串行化，'
                         '且 inventory 的 CHECK(available>=0) 兜底拦下了非法扣减，所以本场景没有超卖。'
                         '问题暴露为 CONSTRAINT_CONFLICT 这类错误语义，而不是干净的 409 INSUFFICIENT_STOCK；'
                         '若表没有 CHECK 约束，同样的代码就会把库存扣成负数。条件扣减 + rowcount 检查才是业务层该做的事。')
        if db_ok and not checks[6]['passed'] and retry \
                and retry['body'].get('error', {}).get('code') == 'CONSTRAINT_CONFLICT':
            notes.append('重试撞上了 orders.request_key 的 UNIQUE 约束：约束兜底防止了重复建单，'
                         '但客户端收到的是约束冲突而不是原订单。幂等检查应在插入之前按 request_key 查旧单并直接返回。')
        if db_ok and not checks[6]['passed'] and retry \
                and retry['body'].get('error', {}).get('code') == 'INSUFFICIENT_STOCK':
            notes.append('重试先查库存、后查幂等：售罄之后同键重试永远先被判“库存不足”，找不回原订单。'
                         '幂等检查必须放在库存检查之前——重试的目的是找回上一次的结果，不是再买一次。')
    elif scenario == 'busy':
        first = next((r for r in responses if r['actor'] == 'A'), None)
        retry = next((r for r in responses if r['actor'] == 'A-retry'), None)
        a_writes = [e for e in events if e['actor'] == 'A' and e['kind'] == 'sql' and e.get('write')]
        busy_code = first['body'].get('error', {}).get('code') if first else None
        checks.append(_check('占锁期间返回 503 DATABASE_BUSY', '503 DATABASE_BUSY',
                             f"{first['status'] if first else '无'} {busy_code}",
                             bool(first) and first['status'] == 503 and busy_code == 'DATABASE_BUSY'))
        checks.append(_check('锁忙期间未写入任何行', 0, len(a_writes), len(a_writes) == 0))
        checks.append(_check('释放锁后同键重试成功', '200',
                             f"{retry['status'] if retry else '无'}",
                             bool(retry) and retry['status'] == 200))
        checks.append(_check('最终库存为 0', 0, inv, inv == 0))
        checks.append(_check('成功订单恰 1 笔', 1, len(orders), len(orders) == 1))
        notes.append('锁忙（DATABASE_BUSY）是暂时状态：事务没开始、什么都没写，用原请求键重试即可；'
                     '库存不足（INSUFFICIENT_STOCK）是业务结果：事务开始了但条件扣减影响 0 行，整单回滚。两者页面展示不同。')
    return {'checks': checks, 'passed': all(c['passed'] for c in checks), 'notes': notes}


META = {
    'id': 'sales-last-unit',
    'title': '销售 · 两笔请求抢最后一件库存',
    'subtitle': '条件扣减、失败语义、同键幂等重试',
    'source': {
        'repo': 'https://github.com/simonshi-stu/commerce-ledger',
        'commit': '0659bd591d3b23b05f21e07609acd3dbe8f18c8b',
        'files': [
            {'path': 'app/service.py', 'symbol': 'place_order', 'lines': '76-103'},
            {'path': 'app/db.py', 'symbol': 'Database.transaction', 'lines': '74-83'},
            {'path': 'app/schema.sql', 'symbol': 'inventory / orders / stock_movements', 'lines': '1-33'},
            {'path': 'app/main.py', 'symbol': '异常→HTTP 映射', 'lines': '65-88'},
        ],
        'tests': ['test_twenty_connections_compete_for_last_unit',
                  'test_order_idempotency_and_conflict',
                  'test_database_busy_returns_retryable_response'],
    },
    'simplifications': [
        '单商品、固定两笔并发请求 + 一次同键重试（原题测试用 20 个连接）',
        '去掉认证、迁移、FastAPI 路由层；异常→HTTP 映射与原 main.py 一致',
        '表结构与 CHECK 约束照搬原 schema.sql，保留兜底语义',
        '“先查后写”错误解用教学调度屏障保证两笔都先读到库存=1；读取值本身来自真实数据库',
    ],
    'statement': (
        '商品 SKU-LAST 只剩 1 件。请求 A（buy-A）和请求 B（buy-B）同时各买 1 件，'
        '谁先拿到写锁由 SQLite 真实调度决定；随后用成功那笔的请求键、同样内容重试一次。\n\n'
        '请实现 place_order：\n'
        '1. 同请求键同内容 → 直接返回原订单，不新建、不扣库存；同键不同内容 → 409。\n'
        '2. 用带库存条件的 UPDATE 扣减（把判断放进 WHERE），检查影响行数；不足 → 409 INSUFFICIENT_STOCK，整单回滚。\n'
        '3. 成功时写 orders / order_items / SALE 流水并更新总额。\n\n'
        '判题同时检查响应和数据库：最终库存=0、成功单 1 笔、SALE 流水 1 条、重试返回原订单且无写入。'
    ),
    'hints': [
        '先查到的库存值不能当并发安全保证：两笔请求可能读到同一个值。把 available>=qty 放进 UPDATE 的 WHERE。',
        'UPDATE 的 rowcount==0 说明条件不满足（库存不足），此时整单回滚并返回 409 INSUFFICIENT_STOCK。',
        '幂等检查要在事务内按 request_key 查旧订单，并比对内容摘要 request_hash。',
        'BEGIN IMMEDIATE 让 SQLite 写入串行化；锁忙是另一回事（见“锁忙”场景），不要和库存不足混淆。',
    ],
    'scenarios': [
        {'id': 'race', 'name': '抢最后一件 + 幂等重试',
         'description': 'A/B 两笔不同请求键并发购买，随后用成功那笔的请求键同内容重试。'},
        {'id': 'busy', 'name': '锁忙 vs 库存不足',
         'description': '外部连接先持有写锁：第一笔应得 503 DATABASE_BUSY 且零写入；释放后同键重试成功。'},
    ],
    'presets': [
        {'id': 'starter', 'name': '起始代码（待补全）', 'code': STARTER},
        {'id': 'reference', 'name': '参考解（条件扣减+幂等）', 'code': REFERENCE},
        {'id': 'wrong_read', 'name': '错误解①：先查后写、无条件扣减', 'code': WRONG_READ_THEN_WRITE},
        {'id': 'wrong_idem', 'name': '错误解②：漏掉幂等检查', 'code': WRONG_NO_IDEMPOTENCY},
    ],
    'sql_presets': [
        {'id': 'sql_reference', 'name': '参考 SQL（条件扣减）', 'code': SQL_REFERENCE},
        {'id': 'sql_wrong', 'name': '错误 SQL（无条件扣减）', 'code': SQL_WRONG},
    ],
    'sql_help': 'SQL 模式：只编辑扣减库存的那条 UPDATE，其余逻辑由参考解框架执行。可用命名参数 :qty（数量）、:pid（商品ID）。',
    'initial_description': 'products 1 行（SKU-LAST，99.00 元）；inventory available=1；其余表为空。',
}
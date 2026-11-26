"""课程关卡引擎：每关一个动作，独立隔离文件库，真实连接与真实数据库状态。

批次 1：observe / ddl / insert / persist / fk / join。
所有 steps 为真实执行产生的快照或事件；extra 携带连接、字段结构、文件复核等观测。
"""
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

from .server import ROOT

COURSE_RUNS = ROOT / 'runs' / 'course'


def new_run_dir():
    path = COURSE_RUNS / uuid4().hex[:12]
    path.mkdir(parents=True, exist_ok=True)
    return path


def connect(path, busy_ms=5000):
    c = sqlite3.connect(str(path), isolation_level=None, timeout=busy_ms / 1000)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute(f'PRAGMA busy_timeout={busy_ms}')
    return c


def snap(c, names):
    return {n: [dict(r) for r in c.execute(f'SELECT * FROM {n} ORDER BY rowid')] for n in names}


def step(label, phase, tables, sql=None, note=None, actor='连接 1', ms=None):
    return dict(label=label, phase=phase, tables=tables, sql=sql, note=note, actor=actor, ms=ms)


def case(name, passed, expected=None, actual=None, error=None, steps=None, extra=None):
    return dict(name=name, passed=passed, expected=expected, actual=actual,
                error=error, steps=steps or [], extra=extra or {})


def single_statement(code, verb):
    text = code.strip().rstrip(';').strip()
    if not text or ';' in text:
        return None, '本关只接受一条 SQL 语句。'
    if not re.match(r'(?is)^' + verb + r'\b', text):
        return None, f'本关只接受一条 {verb} 语句。'
    if re.search(r'(?i)\b(DROP|ATTACH|PRAGMA|DELETE|ALTER)\b|--|/\*', text):
        return None, '语句包含本关不允许的关键字。'
    return text, None


INVENTORY_DDL = ("CREATE TABLE inventory (sku TEXT PRIMARY KEY, "
                 "qty INTEGER NOT NULL CHECK (typeof(qty)='integer' AND qty >= 0))")


def _observe(lesson, code, run_dir, choice="", scenario=""):
    path = run_dir / 'lab.db'
    text, err = single_statement(code, 'SELECT')
    steps, extra = [], {}
    c = connect(path)
    info = dict(path=str(path), sqlite_version=sqlite3.sqlite_version,
                journal_mode=c.execute('PRAGMA journal_mode').fetchone()[0])
    steps.append(step('打开连接 · 空库', 'open', {}, None,
                      f'数据库文件：{path}（本次练习专用，运行结束仍留在磁盘）', '连接 1'))
    if err:
        c.close()
        return [case('查空库的表列表', False, error=err, steps=steps, extra=dict(connection=info))]
    rows = [dict(r) for r in c.execute(text)]
    steps.append(step('查询表列表', 'read', {'sqlite_master': rows}, text,
                      'sqlite_master 是 SQLite 自带目录表：库里每个对象占一行。', '连接 1'))
    steps.append(step('空库确认', 'committed', {'sqlite_master': rows}, None,
                      '空库 = 文件存在、连接成功、目录表 0 行。', '连接 1'))
    c.close()
    intent_ok = bool(re.search(r"(?i)type\s*=\s*'table'", text))
    passed = intent_ok and rows == []
    return [case('查空库的表列表', passed, expected=[], actual=rows,
                 error=None if passed else ('应查询 type=\'table\'；空库目录表应为 0 行。' if not intent_ok else '空库不应查到对象。'),
                 steps=steps, extra=dict(connection=info, tables_list=[r['name'] for r in rows]))]


def _ddl(lesson, code, run_dir, choice="", scenario=""):
    text, err = single_statement(code, 'CREATE TABLE')
    cases = []
    for name in ('看见字段与主键', '重复主键会怎样'):
        path = run_dir / (re.sub(r'\W', '_', name) + '.db')
        steps = [step('建表前', 'before', {}, None, f'练习文件：{path.name}', '连接 1')]
        extra = {}
        if err:
            cases.append(case(name, False, error=err, steps=steps))
            continue
        c = connect(path)
        try:
            c.execute(text)
            schema = [dict(r) for r in c.execute('PRAGMA table_info(inventory)')]
            extra['schema'] = schema
            steps.append(step('执行 CREATE TABLE', 'committed', {'inventory': []}, text,
                              '字段、类型、主键来自 PRAGMA table_info 的真实返回。', '连接 1'))
            if name == '看见字段与主键':
                pk = [r['name'] for r in schema if r['pk']]
                types = {r['name']: r['type'] for r in schema}
                passed = pk == ['sku'] and types.get('qty') == 'INTEGER'
                cases.append(case(name, passed, expected='sku 为主键，qty 为 INTEGER',
                                  actual=f'主键 {pk}，类型 {types}',
                                  error=None if passed else '主键或类型不符合题目要求。',
                                  steps=steps, extra=extra))
            else:
                c.execute("INSERT INTO inventory VALUES('A', 1)")
                steps.append(step('第一次插入 A', 'committed', snap(c, ['inventory']),
                                  "INSERT INTO inventory VALUES('A', 1)", '主键第一次出现：允许。', '连接 1'))
                dup_error = None
                try:
                    c.execute("INSERT INTO inventory VALUES('A', 2)")
                except sqlite3.IntegrityError as exc:
                    dup_error = str(exc)
                steps.append(step('第二次插入同主键', 'error', snap(c, ['inventory']),
                                  "INSERT INTO inventory VALUES('A', 2)",
                                  dup_error or '没有报错：主键没有起作用。', '连接 1'))
                passed = dup_error is not None
                cases.append(case(name, passed, expected='第二次插入被主键拒绝',
                                  actual=dup_error or '插入成功（主键缺失）',
                                  error=None if passed else '重复主键没有被拒绝：检查 PRIMARY KEY。',
                                  steps=steps, extra=extra))
        except sqlite3.Error as exc:
            cases.append(case(name, False, error=str(exc), steps=steps, extra=extra))
        finally:
            c.close()
    return cases


def _insert(lesson, code, run_dir, choice="", scenario=""):
    text, err = single_statement(code, 'INSERT')
    cases = []
    for name in ('从空表到一行', '再插同主键'):
        path = run_dir / (re.sub(r'\W', '_', name) + '.db')
        c = connect(path)
        c.execute(INVENTORY_DDL)
        steps = [step('插入前 · 空表', 'before', snap(c, ['inventory']), None,
                      f'练习文件：{path.name}，表已建好、0 行。', '连接 1')]
        if err:
            c.close()
            cases.append(case(name, False, error=err, steps=steps))
            continue
        error = None
        try:
            c.execute(text)
            steps.append(step('执行你的 INSERT', 'committed', snap(c, ['inventory']), text,
                              '对照上一步的空表：新增的行就是这一条语句写进去的。', '连接 1'))
            if name == '再插同主键':
                try:
                    c.execute(text)
                except sqlite3.IntegrityError as exc:
                    error = str(exc)
                steps.append(step('同语句再跑一次', 'error', snap(c, ['inventory']), text,
                                  error or '第二次也成功了：主键没有拦住重复。', '连接 1'))
        except sqlite3.Error as exc:
            error = str(exc)
            steps.append(step('INSERT 报错', 'error', snap(c, ['inventory']), text, error, '连接 1'))
        rows = [[r['sku'], r['qty']] for r in c.execute('SELECT sku, qty FROM inventory ORDER BY sku')]
        c.close()
        if name == '从空表到一行':
            passed = error is None and rows == [['A', 1]]
            cases.append(case(name, passed, expected=[['A', 1]], actual=rows, error=error, steps=steps))
        else:
            passed = error is not None and rows == [['A', 1]]
            cases.append(case(name, passed, expected='第二次被主键拒绝，表里仍 1 行',
                              actual=f'{len(rows)} 行；第二次：{error or "成功"}',
                              error=None if passed else '重复插入未被拒绝或行数不对。', steps=steps))
    return cases


def _persist(lesson, code, run_dir, choice="", scenario=""):
    path = run_dir / 'lab.db'
    seed = connect(path)
    seed.execute(INVENTORY_DDL)
    seed.execute("INSERT INTO inventory VALUES('A', 1)")
    seed.close()
    text, err = single_statement(code, 'SELECT')
    steps, conns = [], []
    results = []
    for label in ('连接 1', '连接 2'):
        c = connect(path)
        steps.append(step(f'{label} 打开同一个文件', 'open', snap(c, ['inventory']), None,
                          f'文件：{path}（00C 插的那行就在这个练习文件里）', label))
        if err:
            conns.append(dict(label=label, path=str(path), state='已关闭', error=err))
            c.close()
            results.append(None)
            continue
        rows = [[r['sku'], r['qty']] for r in c.execute(text)]
        results.append(rows)
        steps.append(step(f'{label} 查询', 'read', {'查询结果': [dict(sku=r[0], qty=r[1]) for r in rows]},
                          text, '读到的行来自磁盘文件，不是内存残留。', label))
        c.close()
        steps.append(step(f'{label} 关闭', 'close', {}, None,
                          '连接关闭 ≠ 数据消失：文件还在磁盘上。', label))
        conns.append(dict(label=label, path=str(path), state='已关闭', rows=rows))
    check = connect(path)
    after = [[r['sku'], r['qty']] for r in check.execute('SELECT sku, qty FROM inventory ORDER BY sku')]
    check.close()
    conns.append(dict(label='连接 3 · 关完再复核', path=str(path), state='已关闭', rows=after))
    steps.append(step('两个连接都关闭后复核', 'committed', {'inventory': [dict(sku=r[0], qty=r[1]) for r in after]},
                      'SELECT sku, qty FROM inventory', '第三个连接重新打开文件：行仍在 → 持久化成立。', '连接 3'))
    passed = (not err) and results[0] == [['A', 1]] and results[1] == [['A', 1]] and after == [['A', 1]]
    return [case('关掉再连接，行还在', passed, expected=[['A', 1]], actual=after,
                 error=err or (None if passed else '两次连接或复核读到的行不一致/为空。'),
                 steps=steps, extra=dict(connections=conns,
                                         file=dict(path=str(path), exists=path.exists(), size=path.stat().st_size)))]


def _fk(lesson, code, run_dir, choice="", scenario=""):
    text, err = single_statement(code, 'CREATE TABLE')
    cases = []
    for name, sku, want_error in [('有效商品编号', 'A', False), ('无效商品编号 Z', 'Z', True)]:
        path = run_dir / (re.sub(r'\W', '_', name) + '.db')
        c = connect(path)
        c.execute('CREATE TABLE products(sku TEXT PRIMARY KEY, name TEXT)')
        c.executemany('INSERT INTO products VALUES(?,?)', [('A', '苹果'), ('B', '香蕉')])
        steps = [step('商品表已备好', 'before', snap(c, ['products']), None,
                      'products 里只有 A、B 两个商品。', '连接 1')]
        if err:
            c.close()
            cases.append(case(name, False, error=err, steps=steps))
            continue
        error = None
        try:
            c.execute(text)
            c.execute("INSERT INTO orders(id, sku, qty) VALUES(1, ?, 1)", (sku,))
            steps.append(step(f'插入订单 sku={sku}', 'committed', snap(c, ['products', 'orders']),
                              f"INSERT INTO orders(id, sku, qty) VALUES(1, '{sku}', 1)",
                              '外键开启（PRAGMA foreign_keys=ON）时的真实结果。', '连接 1'))
        except sqlite3.Error as exc:
            error = str(exc)
            steps.append(step(f'插入订单 sku={sku} 被拒绝', 'error', snap(c, ['products', 'orders']),
                              f"INSERT INTO orders(id, sku, qty) VALUES(1, '{sku}', 1)", error, '连接 1'))
        orders = [dict(r) for r in c.execute('SELECT * FROM orders')]
        c.close()
        passed = (error is not None) if want_error else (error is None and len(orders) == 1)
        cases.append(case(name, passed,
                          expected='外键拒绝' if want_error else '插入成功',
                          actual=error or f'{len(orders)} 行订单',
                          error=None if passed else ('无效编号没有被外键拦住。' if want_error else '有效编号不应报错。'),
                          steps=steps))
    return cases


def _join(lesson, code, run_dir, choice="", scenario=""):
    path = run_dir / 'lab.db'
    c = connect(path)
    c.execute('CREATE TABLE products(sku TEXT PRIMARY KEY, name TEXT)')
    c.execute('CREATE TABLE orders(id INTEGER PRIMARY KEY, sku TEXT, qty INTEGER)')
    c.executemany('INSERT INTO products VALUES(?,?)', [('A', '苹果'), ('B', '香蕉')])
    c.executemany('INSERT INTO orders VALUES(?,?,?)', [(1, 'A', 2), (2, 'B', 1), (3, 'Z', 5)])
    seed_tables = snap(c, ['products', 'orders'])
    c.close()
    text, err = single_statement(code, 'SELECT')
    cases = []
    for name in ('连接查询', '孤儿订单不进结果'):
        steps = [step('两张种子表', 'before', seed_tables, None,
                      'orders 里 3 号订单的 sku=Z 在 products 中不存在（孤儿）。', '连接 1')]
        if err:
            cases.append(case(name, False, error=err, steps=steps))
            continue
        cc = connect(path)
        error = None
        rows = []
        try:
            rows = [list(r) for r in cc.execute(text)]
            steps.append(step('执行 JOIN', 'read',
                              {'查询结果': [dict(id=r[0], name=r[1], qty=r[2]) for r in rows]},
                              text, 'INNER JOIN 只保留两边都匹配上的行。', '连接 1'))
        except sqlite3.Error as exc:
            error = str(exc)
            steps.append(step('JOIN 报错', 'error', {}, text, error, '连接 1'))
        cc.close()
        expected = [[1, '苹果', 2], [2, '香蕉', 1]]
        passed = error is None and rows == expected
        cases.append(case(name, passed, expected=expected, actual=rows, error=error, steps=steps))
    return cases


ENGINES = {
    'observe': _observe,
    'ddl': _ddl,
    'insert': _insert,
    'persist': _persist,
    'fk': _fk,
    'join': _join,
}

# choice / order 需要 choice / scenario 参数，由 run() 特殊分发；注册检查须认得它们。
SPECIAL_ENGINES = {'choice', 'order', 'pg_build_sales', 'pg_build_hotel'}


def supports(name):
    return name in ENGINES or name in SPECIAL_ENGINES# ---------- 批次 2：顺着一笔订单走完 ----------

def _txn(lesson, code, run_dir, mode):
    path = run_dir / 'lab.db'
    c = connect(path)
    c.execute(INVENTORY_DDL)
    c.executemany('INSERT INTO inventory VALUES(?,?)', [('A', 2), ('B', 4)])
    original = snap(c, ['inventory'])
    text, err = single_statement(code, 'UPDATE')
    steps = [step('运行前库存', 'before', original, None, 'A=2 是起点。', '连接 1'),
             step('开始事务', 'before', original, 'BEGIN', '接下来的写入暂时只在本事务内可见。', '连接 1')]
    c.execute('BEGIN')
    extra = {}
    if err:
        c.close()
        return [case('事务结果', False, error=err, steps=steps)]
    error = None
    changed = None
    try:
        cur = c.execute(text)
        changed = cur.rowcount
        pending = snap(c, ['inventory'])
        steps.append(step('执行写入 · 待提交', 'pending', pending, text,
                          f'影响 {changed} 行；事务内暂时可见 A=3，还不是最终库存。', '连接 1'))
        extra['pending'] = [[r['sku'], r['qty']] for r in pending['inventory']]
        if mode == 'rollback':
            steps.append(step('写入后模拟异常', 'error', pending, None,
                              '教学注入：平台在此抛出异常，触发回滚。', '连接 1'))
            c.execute('ROLLBACK')
            final = snap(c, ['inventory'])
            steps.append(step('回滚后库存', 'rolled_back', final, 'ROLLBACK',
                              '回滚后 A=2 才是最终库存；事务内的 A=3 已被撤销。', '连接 1'))
            wanted = original
        else:
            c.execute('COMMIT')
            final = snap(c, ['inventory'])
            steps.append(step('提交后库存', 'committed', final, 'COMMIT',
                              '提交后 A=3 成为持久化状态。', '连接 1'))
            wanted = {'inventory': [dict(sku='A', qty=3), dict(sku='B', qty=4)]}
    except sqlite3.Error as exc:
        error = str(exc)
        if c.in_transaction:
            c.execute('ROLLBACK')
        final = snap(c, ['inventory'])
        steps.append(step('出错回滚', 'rolled_back', final, 'ROLLBACK', error, '连接 1'))
        wanted = original
    check = connect(path)
    final_rows = [[r['sku'], r['qty']] for r in check.execute('SELECT sku, qty FROM inventory ORDER BY sku')]
    check.close()
    c.close()
    want_rows = [[r['sku'], r['qty']] for r in wanted['inventory']]
    wrote = extra.get('pending') != [[r['sku'], r['qty']] for r in original['inventory']]
    passed = error is None and changed == 1 and wrote and final_rows == want_rows
    if mode == 'rollback':
        extra['contrast'] = [
            dict(name='事务内（临时）', result='A=3', note='待提交快照：仅当前事务可见，不是最终库存。'),
            dict(name='回滚后（最终）', result='A=2', note='独立连接复核：这才是判题用的最终库存。')]
    extra['final_check'] = final_rows
    return [case('提交成功' if mode == 'commit' else '写入后出错并回滚', passed,
                 expected=want_rows, actual=final_rows, error=error, steps=steps, extra=extra)]


def _retry(lesson, code, run_dir, choice="", scenario=""):
    path = run_dir / 'lab.db'
    c = connect(path)
    c.execute(INVENTORY_DDL)
    c.execute("INSERT INTO inventory VALUES('A', 2)")
    c.execute('CREATE TABLE orders(request_key TEXT PRIMARY KEY, sku TEXT)')
    text, err = single_statement(code, 'UPDATE')
    steps = [step('运行前', 'before', snap(c, ['inventory', 'orders']), None,
                  '库存 A=2；订单表空。请求键 r1 将用于两次尝试。', '连接 1')]
    if err:
        c.close()
        return [case('同键重试不重复扣减', False, error=err, steps=steps)]
    counts = []
    for attempt in (1, 2):
        actor = f'尝试 {attempt}'
        c.execute('BEGIN')
        steps.append(step(f'{actor} 开始事务', 'before', snap(c, ['inventory', 'orders']), 'BEGIN',
                          '第二次是同一请求键 r1 的重试。' if attempt == 2 else '第一次购买。', actor))
        cur = c.execute(text)
        counts.append(cur.rowcount)
        steps.append(step(f'{actor} 执行扣减', 'pending', snap(c, ['inventory', 'orders']), text,
                          f'影响 {cur.rowcount} 行。' + ('重试不应再碰库存。' if attempt == 2 else ''), actor))
        try:
            c.execute("INSERT INTO orders VALUES('r1', 'A')")
            c.execute('COMMIT')
            steps.append(step(f'{actor} 建单并提交', 'committed', snap(c, ['inventory', 'orders']),
                              "INSERT INTO orders VALUES('r1','A'); COMMIT", '订单落库。', actor))
        except sqlite3.IntegrityError as exc:
            c.execute('ROLLBACK')
            steps.append(step(f'{actor} 建单撞主键 → 回滚', 'rolled_back', snap(c, ['inventory', 'orders']),
                              'ROLLBACK', f'{exc}：重试的整笔写入被撤销。', actor))
    check = connect(path)
    final_rows = [[r['sku'], r['qty']] for r in check.execute('SELECT sku, qty FROM inventory')]
    orders = check.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
    check.close()
    c.close()
    passed = counts[0] == 1 and counts[1] == 0 and final_rows == [['A', 1]] and orders == 1
    return [case('同键重试不重复扣减', passed, expected='尝试2 影响 0 行，最终 A=1、订单 1 笔',
                 actual=f'尝试1 {counts[0]} 行 / 尝试2 {counts[1]} 行，最终 {final_rows}，订单 {orders} 笔',
                 error=None if passed else '重试仍扣了库存（应加同键守卫），或最终状态不对。',
                 steps=steps, extra=dict(contrast=[
                     dict(name='尝试 1 扣减', result=f'{counts[0]} 行', note='第一次购买应扣 1 行。'),
                     dict(name='尝试 2 扣减（重试）', result=f'{counts[1]} 行',
                          note='守卫命中旧订单时应为 0 行；即使随后回滚，也不该先碰库存。')],
                     final_check=final_rows))]


def _order(lesson, code, run_dir, choice="", scenario=""):
    seeds = {'success': [('A', 1)], 'insufficient': [('A', 0)], 'missing': [('B', 2)]}
    seed = seeds.get(scenario, seeds['success'])
    path = run_dir / (scenario + '.db')
    c = connect(path)
    c.execute(INVENTORY_DDL)
    c.executemany('INSERT INTO inventory VALUES(?,?)', seed)
    c.execute('CREATE TABLE orders(request_key TEXT PRIMARY KEY, sku TEXT, qty INTEGER)')
    c.execute('CREATE TABLE movements(id INTEGER PRIMARY KEY, request_key TEXT, delta INTEGER)')
    request = dict(request_key='ord-1', sku='A', qty=1)
    text, err = single_statement(code, 'UPDATE')
    steps = [step('前端输入', 'input', snap(c, ['inventory']), None,
                  f'请求参数：{request}', '前端'),
             step('后端判断 · 查商品', 'validate', snap(c, ['inventory']),
                  "SELECT sku, qty FROM inventory WHERE sku='A'", '先确认商品存在。', '后端')]
    product = c.execute("SELECT sku, qty FROM inventory WHERE sku='A'").fetchone()
    if err:
        c.close()
        return [case(f'下单 · {scenario}', False, error=err, steps=steps)]
    if product is None:
        status, biz, front = 404, 'PRODUCT_NOT_FOUND', '商品不存在，请求被拒绝'
        steps.append(step('业务响应（回放记录）', 'http', snap(c, ['inventory', 'orders']), None,
                          f'业务侧模拟响应：HTTP {status} · {biz}（不是课程接口本次请求的状态）', '后端'))
        steps.append(step('前端反馈', 'frontend', snap(c, ['inventory', 'orders']), None, front, '前端'))
    else:
        c.execute('BEGIN')
        steps.append(step('开始事务', 'before', snap(c, ['inventory', 'orders']), 'BEGIN', None, '后端'))
        c.execute("INSERT INTO orders VALUES('ord-1', 'A', 1)")
        steps.append(step('先建订单', 'pending', snap(c, ['inventory', 'orders']),
                          "INSERT INTO orders VALUES('ord-1','A',1)", '订单行暂存于事务内。', '后端'))
        cur = None
        rc = None
        try:
            cur = c.execute(text)
            rc = cur.rowcount
        except sqlite3.Error as exc:
            c.execute('ROLLBACK')
            steps.append(step('扣减报错 → 回滚', 'rolled_back', snap(c, ['inventory', 'orders']), text,
                              f'{exc}：整单回滚。', '后端'))
            status, biz, front = 409, 'CONSTRAINT_CONFLICT', '约束冲突：数据库兜底拦截了非法写入'
        if rc is not None:
            steps.append(step('条件扣减', 'pending', snap(c, ['inventory', 'orders']), text,
                              f'影响 {rc} 行。', '后端'))
            if rc != 1:
                c.execute('ROLLBACK')
                status, biz, front = 409, 'INSUFFICIENT_STOCK', '库存不足，整单未创建'
                steps.append(step('回滚', 'rolled_back', snap(c, ['inventory', 'orders']), 'ROLLBACK',
                                  '扣减影响 0 行 → 整单回滚，订单行也被撤销。', '后端'))
            else:
                c.execute("INSERT INTO movements(request_key, delta) VALUES('ord-1', -1)")
                c.execute('COMMIT')
                status, biz, front = 200, 'PLACED', '下单成功：订单 ord-1'
                steps.append(step('写流水并提交', 'committed', snap(c, ['inventory', 'orders', 'movements']),
                                  "INSERT INTO movements...; COMMIT", '订单与流水一起持久化。', '后端'))
        steps.append(step('业务响应（回放记录）', 'http', snap(c, ['inventory', 'orders']), None,
                          f'业务侧模拟响应：HTTP {status} · {biz}（不是课程接口本次请求的状态）', '后端'))
        steps.append(step('前端反馈', 'frontend', snap(c, ['inventory', 'orders']), None, front, '前端'))
    check = connect(path)
    inv = [[r['sku'], r['qty']] for r in check.execute('SELECT sku, qty FROM inventory ORDER BY sku')]
    n_orders = check.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
    check.close()
    c.close()
    want = {'success': (200, 'PLACED', [['A', 0]], 1),
            'insufficient': (409, 'INSUFFICIENT_STOCK', [['A', 0]], 0),
            'missing': (404, 'PRODUCT_NOT_FOUND', [['B', 2]], 0)}[scenario]
    passed = (status, biz, inv, n_orders) == want
    return [case(f'下单 · {scenario}', passed,
                 expected=f'HTTP {want[0]} {want[1]}，库存 {want[2]}，订单 {want[3]} 笔',
                 actual=f'HTTP {status} {biz}，库存 {inv}，订单 {n_orders} 笔',
                 error=None if passed else '响应或最终数据库状态与场景预期不符。',
                 steps=steps, extra=dict(final_check=inv))]


ENGINES.update({
    'txn': lambda lesson, code, rd, choice='', scenario='': _txn(lesson, code, rd, lesson.get('txn_mode', 'commit')),
    'retry': _retry,
})


def _verdict(cases):
    judged = [c['passed'] for c in cases if c['passed'] is not None]
    return all(judged) if judged else None


def run(lesson, code, choice='', scenario='', files=None, stage=None):
    engine = lesson.get('engine')
    if engine in ('pg_build_sales', 'pg_build_hotel', 'pg_guide'):
        fn = ENGINES[engine]
        rd = new_run_dir()
        cases = fn(lesson, code, rd, choice, scenario, files)
        return dict(run_id=rd.name, passed=_verdict(cases), cases=cases)
    if engine == 'choice':
        rd = new_run_dir()
        cases = _txn(lesson, code, rd, 'rollback' if choice == 'rollback' else 'commit')
        return dict(run_id=rd.name, passed=_verdict(cases), cases=cases)
    if engine == 'order':
        rd = new_run_dir()
        cases = _order(lesson, code, rd, choice, scenario or 'success')
        return dict(run_id=rd.name, passed=_verdict(cases), cases=cases)
    fn = ENGINES.get(engine)
    if fn is None:
        raise ValueError('unknown engine: ' + str(engine))
    run_dir = new_run_dir()
    cases = fn(lesson, code, run_dir, choice, scenario, files, stage) if engine == 'lake' else fn(lesson, code, run_dir, choice, scenario)
    judged = [c['passed'] for c in cases if c['passed'] is not None]
    passed = all(judged) if judged else None
    return dict(run_id=run_dir.name, passed=passed, cases=cases)# ---------- 批次 3：四种容易混淆的情况 ----------

import json
import socket
import subprocess
import sys
import threading
import time


def _ms(t0):
    return round((time.monotonic() - t0) * 1000)


def _wait(lesson, code, run_dir, choice="", scenario=""):
    path = run_dir / 'lab.db'
    seed = connect(path)
    seed.execute(INVENTORY_DDL)
    seed.execute("INSERT INTO inventory VALUES('A', 2)")
    seed.close()
    text, err = single_statement(code, 'UPDATE')
    if err:
        return [case('普通等待', False, error=err, steps=[])]
    t0 = time.monotonic()
    timelines = {'连接 A': [], '连接 B': []}
    steps = [step('运行前', 'before', {'inventory': [dict(sku='A', qty=2)]}, None,
                  'A、B 各买 1 件，同时出发。', '系统')]
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    a_locked = threading.Event()
    marks = {}

    def actor(name, hold):
        c = connect(path)
        timelines[name].append(dict(ms=_ms(t0), op='连接打开'))
        barrier.wait(timeout=10)
        if name == '连接 B':
            a_locked.wait(timeout=5)
        timelines[name].append(dict(ms=_ms(t0), op='尝试 BEGIN IMMEDIATE', tone='wait' if name == '连接 B' else ''))
        c.execute('BEGIN IMMEDIATE')
        marks[name + ':begin'] = _ms(t0)
        if name == '连接 A':
            a_locked.set()
        timelines[name].append(dict(ms=_ms(t0), op='拿到写锁 · BEGIN 成功', tone='ok'))
        with lock:
            steps.append(step(f'{name} 开始事务', 'before', snap(c, ['inventory']), 'BEGIN IMMEDIATE',
                              'B 的 BEGIN 等到 A 提交后才返回。' if name == '连接 B' else 'A 先拿到写锁。', name))
        cur = c.execute(text)
        with lock:
            steps.append(step(f'{name} 扣减 · 待提交', 'pending', snap(c, ['inventory']), text,
                              f'影响 {cur.rowcount} 行（本事务内可见）。', name))
        timelines[name].append(dict(ms=_ms(t0), op=f'UPDATE 影响 {cur.rowcount} 行'))
        if hold:
            time.sleep(0.7)
            timelines[name].append(dict(ms=_ms(t0), op='持锁 700ms（模拟业务处理）', tone='wait'))
        c.execute('COMMIT')
        marks[name + ':commit'] = _ms(t0)
        timelines[name].append(dict(ms=_ms(t0), op='COMMIT · 释放写锁', tone='ok'))
        with lock:
            steps.append(step(f'{name} 提交', 'committed', snap(c, ['inventory']), 'COMMIT', None, name))
        c.close()

    ta = threading.Thread(target=actor, args=('连接 A', True))
    tb = threading.Thread(target=actor, args=('连接 B', False))
    ta.start()
    tb.start()
    ta.join(15)
    tb.join(15)
    check = connect(path)
    final = [[r['sku'], r['qty']] for r in check.execute('SELECT sku, qty FROM inventory')]
    check.close()
    waited = marks.get('连接 B:begin', 0) > marks.get('连接 A:commit', 0)
    passed = final == [['A', 0]] and waited
    return [case('普通等待：A 释放后 B 继续', passed, expected='最终 A=0，且 B 的 BEGIN 晚于 A 的 COMMIT',
                 actual=f'最终 {final}，B 等到 A 提交={waited}',
                 error=None if passed else '最终库存不对，或 B 没有真正等待。',
                 steps=steps, extra=dict(timelines=timelines, final_check=final))]


def _busy(lesson, code, run_dir, choice="", scenario=""):
    text, err = single_statement(code, 'UPDATE')
    cases = []
    contrast = []
    for name in ('锁忙', '库存不足'):
        path = run_dir / (name + '.db')
        c = connect(path)
        c.execute(INVENTORY_DDL)
        c.execute("INSERT INTO inventory VALUES('A', ?)", (1 if name == '锁忙' else 0,))
        steps = [step('运行前', 'before', snap(c, ['inventory']), None,
                      '锁忙用例 A=1；库存不足用例 A=0。', '连接 B')]
        if err:
            c.close()
            cases.append(case(name, False, error=err, steps=steps))
            continue
        t0 = time.monotonic()
        timelines = {'连接 A（持锁）': [], '连接 B': []}
        if name == '锁忙':
            holder = connect(path)
            holder.execute('BEGIN IMMEDIATE')
            timelines['连接 A（持锁）'].append(dict(ms=_ms(t0), op='BEGIN IMMEDIATE · 持锁不放', tone='wait'))
            steps.append(step('A 持锁', 'before', snap(c, ['inventory']), 'BEGIN IMMEDIATE',
                              'A 一直持锁，直到 B 超时失败。', '连接 A'))
            b = connect(path, 150)
            busy_error = None
            try:
                b.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError as exc:
                busy_error = str(exc)
            timelines['连接 B'].append(dict(ms=_ms(t0), op='BEGIN 失败 · 锁忙（150ms 超时）', tone='fail'))
            steps.append(step('B 锁忙失败', 'error', snap(c, ['inventory']), 'BEGIN IMMEDIATE',
                              f'{busy_error}：事务没开始，零写入。', '连接 B'))
            writes_during_busy = snap(c, ['inventory'])
            holder.rollback()
            holder.close()
            timelines['连接 A（持锁）'].append(dict(ms=_ms(t0), op='ROLLBACK · 释放锁', tone='ok'))
            steps.append(step('A 释放锁', 'committed', snap(c, ['inventory']), 'ROLLBACK', None, '连接 A'))
            b.close()
            b = connect(path)
            b.execute('BEGIN')
            cur = b.execute(text)
            b.execute('COMMIT')
            timelines['连接 B'].append(dict(ms=_ms(t0), op=f'同键重试成功 · 影响 {cur.rowcount} 行', tone='ok'))
            steps.append(step('B 同键重试', 'committed', snap(b, ['inventory']), text,
                              '锁释放后，同一语句重试成功。', '连接 B'))
            b.close()
            check = connect(path)
            final = [[r['sku'], r['qty']] for r in check.execute('SELECT sku, qty FROM inventory')]
            check.close()
            passed = busy_error is not None and writes_during_busy == {'inventory': [dict(sku='A', qty=1)]} \
                and final == [['A', 0]]
            contrast.append(dict(name='锁忙 DATABASE_BUSY', result='零写入 · 可重试',
                                 note='事务没开始；释放锁后同键重试成功。'))
            cases.append(case('锁忙：B 失败且零写入', passed, expected='B 锁忙报错、期间零写入、重试后 A=0',
                              actual=f'busy={bool(busy_error)}，重试后 {final}',
                              error=None if passed else '锁忙语义或重试结果不对。',
                              steps=steps, extra=dict(timelines=timelines, final_check=final)))
        else:
            c.execute('BEGIN')
            cur = c.execute(text)
            steps.append(step('条件扣减', 'pending', snap(c, ['inventory']), text,
                              f'影响 {cur.rowcount} 行：库存为 0，条件不满足。', '连接 B'))
            c.execute('ROLLBACK')
            steps.append(step('整单回滚', 'rolled_back', snap(c, ['inventory']), 'ROLLBACK',
                              '业务拒绝：INSUFFICIENT_STOCK，重试也不会成功（除非补货）。', '连接 B'))
            check = connect(path)
            final = [[r['sku'], r['qty']] for r in check.execute('SELECT sku, qty FROM inventory')]
            check.close()
            passed = cur.rowcount == 0 and final == [['A', 0]]
            contrast.append(dict(name='库存不足 INSUFFICIENT_STOCK', result='整单回滚 · 业务拒绝',
                                 note='事务开始了但条件不满足；与锁忙的恢复路径不同。'))
            cases.append(case('库存不足：业务拒绝', passed, expected='影响 0 行、回滚、A 仍 0',
                              actual=f'影响 {cur.rowcount} 行，最终 {final}',
                              error=None if passed else '库存不足语义不对。', steps=steps,
                              extra=dict(final_check=final)))
        c.close()
    for cs in cases:
        cs['extra']['contrast'] = contrast
    return cases


def _exit(lesson, code, run_dir, choice="", scenario=""):
    text, err = single_statement(code, 'INSERT')
    cases = []
    for name, exit_at, want_rows in (('提交前退出', 'before', 0), ('提交后退出', 'after', 1)):
        path = run_dir / (exit_at + '.db')
        seed = connect(path)
        seed.execute('CREATE TABLE bookings(request_id TEXT PRIMARY KEY, status TEXT)')
        seed.execute('CREATE TABLE booking_nights(request_id TEXT, room_id TEXT, night TEXT, cots INTEGER, extra_beds INTEGER)')
        seed.close()
        steps = [step('种子空表', 'before', {'bookings': [], 'booking_nights': []}, None,
                      f'练习文件：{path.name}', '父进程')]
        if err:
            cases.append(case(name, False, error=err, steps=steps))
            continue
        payload = run_dir / (exit_at + '.json')
        payload.write_text(json.dumps(dict(db=str(path), code=text, exit_at=exit_at)), encoding='utf-8')
        proc = subprocess.run([sys.executable, '-B', '-m', 'lab.course_exit_child', str(payload)],
                              cwd=str(ROOT), capture_output=True, timeout=20)
        child_events = []
        for line in proc.stdout.decode('utf-8', 'replace').splitlines():
            try:
                child_events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        for ev in child_events:
            phase = 'pending' if ev['op'].startswith('INSERT') else (
                'committed' if ev['op'] == 'COMMIT' else 'rolled_back' if 'EXIT_BEFORE' in ev['op'] else 'before')
            steps.append(step(ev['op'], phase, {}, None, ev.get('note'), '子进程', ev['ms']))
        steps.append(step(f'父进程观测：退出码 {proc.returncode}', 'marker', {}, None,
                          '子进程已退出；连接随之断开。锁由 SQLite 随事务结束释放。', '父进程'))
        check = connect(path)
        nights = [dict(r) for r in check.execute('SELECT * FROM booking_nights')]
        bookings = [dict(r) for r in check.execute('SELECT * FROM bookings')]
        steps.append(step('退出后查实际表', 'committed' if want_rows else 'rolled_back',
                          {'bookings': bookings, 'booking_nights': nights},
                          'SELECT * FROM bookings / booking_nights',
                          '提交前退出 → 0 行；提交后退出 → 记录保留。', '父进程'))
        replayed = False
        if want_rows:
            old = check.execute("SELECT * FROM bookings WHERE request_id='r1'").fetchone()
            replayed = old is not None
            steps.append(step('原请求键重试找回', 'read', {'bookings': [dict(old)] if old else []},
                              "SELECT * FROM bookings WHERE request_id='r1'",
                              '找回原记录，不重复插入。', '父进程'))
        check.close()
        passed = proc.returncode == 73 and len(nights) == want_rows and len(bookings) == want_rows \
            and (replayed if want_rows else True)
        cases.append(case(name + '：留下记录吗', passed,
                          expected=f'{want_rows} 行预订 + {want_rows} 行房晚' + ('，且原键可找回' if want_rows else ''),
                          actual=f'退出码 {proc.returncode}，{len(bookings)} 预订 / {len(nights)} 房晚，找回={replayed}',
                          error=None if passed else '退出时点与留存数据不符。', steps=steps,
                          extra=dict(final_check=nights)))
    return cases


ENGINES.update({
    'wait': _wait,
    'busy': _busy,
    'exit': _exit,
})
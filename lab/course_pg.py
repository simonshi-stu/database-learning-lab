"""第 1 批引擎：真实 PostgreSQL（连接建表 / 隔离级别 / 行锁 / 死锁 / 索引计划）。

所有观测来自本次运行的真实连接；顺序用同步事件安排，判题看事件顺序与最终状态，
不靠写死毫秒数。无数据库或驱动时返回「示意 / 未实测」，不计通过。
"""
import threading
import time

from . import pglab
from .course_engines import case, step, single_statement, ENGINES

LAYERS = ['浏览器（画板）', 'Python 课程服务', 'psycopg 数据库客户端', 'PostgreSQL 服务端']


def _ms(t0):
    return round((time.monotonic() - t0) * 1000)


def _unavailable(why, title):
    return [case(title, None, expected='真实 PostgreSQL 观测', actual='未实测',
                 error=None, steps=[step('环境检查', 'illustrative', {}, None,
                                         f'示意 / 未实测（{why}）', '系统')],
                 extra=dict(not_measured=f'示意 / 未实测（{why}）。复现：启动本项目 PG 集群后重跑。'))]


def _pg_ddl(lesson, code, run_dir, choice="", scenario=""):
    ok, why = pglab.available()
    if not ok:
        return _unavailable(why, '连接与建表')
    schema = pglab.new_schema()
    text, err = single_statement(code, 'CREATE TABLE')
    cases = []
    c = pglab.connect(schema, autocommit=True)
    tables0 = [r[0] for r in c.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (schema,))]
    steps0 = [step('连接成功 · 空 schema', 'open', {}, None,
                   f'schema={schema}；表列表={tables0 or "（空）"}', 'psycopg 客户端')]
    if err:
        c.close()
        return [case('建表插数重连', False, error=err, steps=steps0, extra=dict(layers=LAYERS, schema=schema))]
    c.execute(text)
    c.execute("INSERT INTO inventory VALUES('A', 1)")
    rows = [list(r) for r in c.execute('SELECT sku, qty FROM inventory ORDER BY sku')]
    steps0.append(step('建表 + 插入 + 查询', 'committed', {'inventory': [dict(sku=r[0], qty=r[1]) for r in rows]},
                       text + " ; INSERT INTO inventory VALUES('A',1); SELECT ...",
                       '真实 PostgreSQL 写入并读回。', 'psycopg 客户端'))
    cases.append(case('空库 → 建表插数', True, expected=[['A', 1]], actual=rows, steps=list(steps0),
                      extra=dict(layers=LAYERS, schema=schema, tables_before=tables0)))
    dup = None
    try:
        c.execute("INSERT INTO inventory VALUES('A', 2)")
    except Exception as exc:
        dup = getattr(exc, 'sqlstate', '') or type(exc).__name__
    steps0.append(step('重复主键再插', 'error', {'inventory': [dict(sku=r[0], qty=r[1]) for r in rows]},
                       "INSERT INTO inventory VALUES('A',2)",
                       f'sqlstate={dup}（23505 = unique_violation）' if dup else '没有报错：主键缺失。', 'psycopg 客户端'))
    cases.append(case('重复键被拒', dup == '23505', expected='23505', actual=dup or '插入成功',
                      error=None if dup == '23505' else '重复键未被拒绝。', steps=list(steps0),
                      extra=dict(layers=LAYERS, schema=schema)))
    c.close()
    c2 = pglab.connect(schema, autocommit=True)
    rows2 = [list(r) for r in c2.execute('SELECT sku, qty FROM inventory ORDER BY sku')]
    c2.close()
    steps0.append(step('断开后新连接重读', 'committed', {'inventory': [dict(sku=r[0], qty=r[1]) for r in rows2]},
                       'SELECT sku, qty FROM inventory', '提交过的数据在新连接里仍在。', '新连接'))
    cases.append(case('重连后提交数据仍在', rows2 == [['A', 1]], expected=[['A', 1]], actual=rows2,
                      steps=list(steps0), extra=dict(layers=LAYERS, schema=schema)))
    return cases


def _pg_iso(lesson, code, run_dir, choice="", scenario=""):
    ok, why = pglab.available()
    if not ok:
        return _unavailable(why, '双连接隔离')
    level = 'REPEATABLE READ' if scenario == 'rr' else 'READ COMMITTED'
    schema = pglab.new_schema()
    seed = pglab.connect(schema, autocommit=True)
    seed.execute('CREATE TABLE t(id int PRIMARY KEY, qty int)')
    seed.execute('INSERT INTO t VALUES(1, 2)')
    seed.close()
    text, err = single_statement(code, 'SELECT')
    if err:
        return [case('隔离级别对照', False, error=err, steps=[])]
    t0 = time.monotonic()
    timelines = {'连接 A': [], '连接 B': []}
    obs = {}
    qerr = None
    a = pglab.connect(schema, autocommit=True)
    b = pglab.connect(schema, autocommit=True)
    c = pglab.connect(schema, autocommit=True)

    def q(conn):
        nonlocal qerr
        try:
            row = conn.execute(text).fetchone()
        except Exception as exc:
            qerr = f'{getattr(exc, "sqlstate", "") or type(exc).__name__}: {str(exc).splitlines()[0]}'
            return None
        if row is None:
            if qerr is None:
                qerr = 'EMPTY: 没查到行（WHERE 条件无命中）'
            return None
        return row[0]

    steps = []
    final = None
    try:
        a.execute('BEGIN')
        timelines['连接 A'].append(dict(ms=_ms(t0), op='BEGIN'))
        a.execute('UPDATE t SET qty=3 WHERE id=1')
        timelines['连接 A'].append(dict(ms=_ms(t0), op='UPDATE qty=3（未提交）', tone='wait'))
        b.execute(f'BEGIN ISOLATION LEVEL {level}')
        timelines['连接 B'].append(dict(ms=_ms(t0), op=f'BEGIN · {level}'))
        obs['before_commit'] = q(b)
        timelines['连接 B'].append(dict(ms=_ms(t0), op=f'A 提交前读到 {obs["before_commit"]}'))
        steps.append(step('A 提交前 B 读取', 'read', {'B 的读取': [dict(stage='before_commit', qty=obs['before_commit'])]}, text, f'{level}。', '连接 B'))
        a.execute('COMMIT')
        timelines['连接 A'].append(dict(ms=_ms(t0), op='COMMIT', tone='ok'))
        obs['after_commit'] = q(b)
        timelines['连接 B'].append(dict(ms=_ms(t0), op=f'A 提交后读到 {obs["after_commit"]}'))
        steps.append(step('A 提交后 B 读取', 'read', {'B 的读取': [dict(stage='after_commit', qty=obs['after_commit'])]}, text, f'{level}。', '连接 B'))
        b.execute('COMMIT')
        c.execute(f'BEGIN ISOLATION LEVEL {level}')
        timelines['连接 B'].append(dict(ms=_ms(t0), op='连接 C：只执行 BEGIN（尚未取快照）'))
        a.execute('BEGIN')
        a.execute('UPDATE t SET qty=5 WHERE id=1')
        a.execute('COMMIT')
        timelines['连接 A'].append(dict(ms=_ms(t0), op='另一笔提交 qty=5', tone='ok'))
        obs['begin_only'] = q(c)
        timelines['连接 B'].append(dict(ms=_ms(t0), op=f'C 的第一条 SELECT 读到 {obs["begin_only"]}（BEGIN 不固定快照）'))
        steps.append(step('只 BEGIN 后第一条 SELECT', 'read', {'C 的读取': [dict(stage='begin_only', qty=obs['begin_only'])]}, text, '快照由事务内第一条获取快照的语句建立；单独 BEGIN 不固定快照。', '连接 C'))
        c.execute('COMMIT')
        a.execute('BEGIN')
        a.execute('UPDATE t SET qty=9 WHERE id=1')
        a.execute('ROLLBACK')
        timelines['连接 A'].append(dict(ms=_ms(t0), op='UPDATE qty=9 后 ROLLBACK', tone='fail'))
        b.execute(f'BEGIN ISOLATION LEVEL {level}')
        obs['after_rollback'] = q(b)
        b.execute('COMMIT')
        timelines['连接 B'].append(dict(ms=_ms(t0), op=f'回滚后读到 {obs["after_rollback"]}'))
        steps.append(step('回滚后 B 读取', 'read', {'B 的读取': [dict(stage='after_rollback', qty=obs['after_rollback'])]}, text, '被回滚的 9 不可见。', '连接 B'))
        final = pglab.connect(schema, autocommit=True).execute('SELECT qty FROM t WHERE id=1').fetchone()[0]
    finally:
        for conn in (a, b, c):
            try:
                conn.execute('ROLLBACK')
            except Exception:
                pass
            conn.close()
    want_after = 2 if scenario == 'rr' else 3
    extra = dict(timelines=timelines, level=level, schema=schema, layers=LAYERS,
                 contrast=[dict(name='Read Committed', result='提交后见新值', note='每条语句取新快照。'),
                           dict(name='Repeatable Read', result='首条取快照语句后不变', note='快照由事务内第一条获取快照的语句建立；单独 BEGIN 不固定快照。')])
    if qerr:
        msg = ('没查到行：WHERE 条件无命中（例如 id=2 不存在）。这是「没查到」，不是「查错了」；请改条件或列。'
               if qerr.startswith('EMPTY') else
               '查询报错（' + qerr + '）：检查列名与语法；失败已回滚并关闭连接。')
        steps.append(step('查询失败', 'error', {}, text, msg, '连接 B'))
        return [case(n, False, expected=e, actual='查询未返回有效行', error=msg, steps=steps, extra=extra)
                for n, e in [('未提交不可见', 2), ('A 提交后 B 的可见性', want_after),
                             ('BEGIN 不固定快照', 5), ('回滚无残留', 'B 读 5，最终 5')]]
    checks = [
        case('未提交不可见', obs['before_commit'] == 2, expected=2, actual=obs['before_commit'], steps=steps, extra=extra),
        case('A 提交后 B 的可见性', obs['after_commit'] == want_after, expected=want_after,
             actual=obs['after_commit'],
             error=None if obs['after_commit'] == want_after else
             ('Repeatable Read 快照应保持旧值。' if scenario == 'rr' else 'Read Committed 应看到新值。'),
             steps=steps, extra=extra),
        case('BEGIN 不固定快照', obs['begin_only'] == 5, expected=5, actual=obs['begin_only'],
             error=None if obs['begin_only'] == 5 else '第一条取快照语句应看到已提交的新值。',
             steps=steps, extra=extra),
        case('回滚无残留', obs['after_rollback'] == 5 and final == 5, expected='B 读 5（被回滚的 9 不可见），最终 5',
             actual=f'B 读 {obs["after_rollback"]}，最终 {final}', steps=steps, extra=extra),
    ]
    return checks


def _pg_lock(lesson, code, run_dir, choice="", scenario=""):
    ok, why = pglab.available()
    if not ok:
        return _unavailable(why, '行锁与锁等待')
    schema = pglab.new_schema()
    seed = pglab.connect(schema, autocommit=True)
    seed.execute('CREATE TABLE t(id int PRIMARY KEY, qty int)')
    seed.cursor().executemany('INSERT INTO t VALUES(%s,%s)', [(1, 2), (2, 2)])
    seed.close()
    text, err = single_statement(code, 'UPDATE')
    if err:
        return [case('行锁', False, error=err, steps=[])]
    target_b = 2 if scenario == 'different' else 1
    t0 = time.monotonic()
    timelines = {'连接 A': [], '连接 B': []}
    marks = {}
    locked = threading.Event()
    b_tried = threading.Event()
    release = threading.Event()
    result = {}

    def conn_a():
        a = pglab.connect(schema, autocommit=True)
        a.execute('BEGIN')
        a.execute('UPDATE t SET qty=qty-1 WHERE id=1')
        timelines['连接 A'].append(dict(ms=_ms(t0), op='锁住 id=1', tone='wait'))
        marks['a_locked'] = True
        locked.set()
        b_tried.wait(timeout=10)
        if scenario != 'different':
            release.wait(timeout=10)
        a.execute('COMMIT')
        timelines['连接 A'].append(dict(ms=_ms(t0), op='COMMIT · 释放行锁', tone='ok'))
        a.close()

    def conn_b():
        b = pglab.connect(schema, autocommit=True)
        b.execute('BEGIN')
        if scenario == 'timeout':
            b.execute('SET lock_timeout = \'300ms\'')
        locked.wait(timeout=10)
        timelines['连接 B'].append(dict(ms=_ms(t0), op=f'尝试 UPDATE id={target_b}', tone='wait'))
        b_tried.set()
        try:
            b.execute(text)
            result['b'] = 'ok'
            timelines['连接 B'].append(dict(ms=_ms(t0), op='拿到行锁 · UPDATE 成功', tone='ok'))
            b.execute('COMMIT')
        except Exception as exc:
            result['b'] = getattr(exc, 'sqlstate', '') or type(exc).__name__
            b.execute('ROLLBACK')
            timelines['连接 B'].append(dict(ms=_ms(t0), op=f'失败 {result["b"]} · ROLLBACK', tone='fail'))
        b.close()
        release.set()

    ta = threading.Thread(target=conn_a)
    tb = threading.Thread(target=conn_b)
    ta.start()
    tb.start()
    ta.join(20)
    tb.join(20)
    check = pglab.connect(schema, autocommit=True)
    final = [list(r) for r in check.execute('SELECT id, qty FROM t ORDER BY id')]
    check.close()
    steps = [step('A 锁住 id=1', 'pending', {}, 'UPDATE t ... WHERE id=1', '持锁期间 B 的行为见时间轴。', '连接 A'),
             step(f'B 尝试 id={target_b}', 'wait', {}, text, '等待 / 并行 / 超时由场景决定。', '连接 B'),
             step('最终表', 'committed', {'t': [dict(id=r[0], qty=r[1]) for r in final]},
                  'SELECT id, qty FROM t', '独立连接复核。', '新连接')]
    if scenario == 'same':
        passed = result.get('b') == 'ok' and final == [[1, 0], [2, 2]]
        want = 'B 等待后成功；两笔各扣 1，最终 id1=0'
    elif scenario == 'different':
        passed = result.get('b') == 'ok' and final == [[1, 1], [2, 1]]
        want = '不同行并行；最终两行各 1'
    else:
        passed = result.get('b') == '55P03' and final == [[1, 1], [2, 2]]
        want = 'B 超时 55P03 且无部分写入；A 提交后 id1=1'
    return [case(f'行锁 · {scenario}', passed, expected=want,
                 actual=f'B={result.get("b")}，最终 {final}',
                 error=None if passed else '锁行为或最终状态不符。', steps=steps,
                 extra=dict(timelines=timelines, schema=schema, layers=LAYERS))]


def _pg_deadlock(lesson, code, run_dir, choice="", scenario=""):
    ok, why = pglab.available()
    if not ok:
        return _unavailable(why, '死锁')
    schema = pglab.new_schema()
    seed = pglab.connect(schema, autocommit=True)
    seed.execute('CREATE TABLE res(id int PRIMARY KEY, v text)')
    seed.cursor().executemany('INSERT INTO res VALUES(%s,%s)', [(1, 'r1'), (2, 'r2')])
    seed.close()
    t0 = time.monotonic()
    events = []
    lock = threading.Lock()

    def rec(who, op, tone=''):
        with lock:
            events.append(dict(ms=_ms(t0), who=who, op=op, tone=tone))

    def worker(name, first, second, out, barrier):
        c = pglab.connect(schema, autocommit=True)
        try:
            c.execute('BEGIN')
            c.execute('SELECT v FROM res WHERE id=%s FOR UPDATE', (first,))
            rec(name, f'锁住资源 {first}', 'wait')
            if barrier is not None:
                barrier.wait(timeout=10)
            rec(name, f'请求资源 {second}（进入等待）', 'wait')
            c.execute('SELECT v FROM res WHERE id=%s FOR UPDATE', (second,))
            c.execute('UPDATE res SET v=%s WHERE id=%s', (name, second))
            c.execute('COMMIT')
            out[name] = 'committed'
            rec(name, 'COMMIT', 'ok')
        except Exception as exc:
            state = getattr(exc, 'sqlstate', '') or type(exc).__name__
            c.execute('ROLLBACK')
            out[name] = state
            rec(name, f'被中止 {state} · ROLLBACK', 'fail')
        finally:
            c.close()

    results = {}
    for label, orders in (('cross', [(1, 2), (2, 1)]), ('uniform', [(1, 2), (1, 2)])):
        out = {}
        barrier = threading.Barrier(2) if label == 'cross' else None
        ta = threading.Thread(target=worker, args=('A', *orders[0], out, barrier))
        tb = threading.Thread(target=worker, args=('B', *orders[1], out, barrier))
        ta.start()
        tb.start()
        ta.join(25)
        tb.join(25)
        results[label] = dict(out)
    check = pglab.connect(schema, autocommit=True)
    final = [list(r) for r in check.execute('SELECT id, v FROM res ORDER BY id')]
    check.close()
    graph = [f"{e['who']}：{e['op']}" for e in events if '锁住' in e['op'] or '请求' in e['op']]
    graph.append('交叉顺序等待图成环 → 死锁；统一顺序不成环')
    steps = [step(f"{e['who']} +{e['ms']}ms {e['op']}", 'rolled_back' if e['tone'] == 'fail' else 'committed',
                  {}, None, '本次运行真实事件。', e['who']) for e in events]
    steps.append(step('最终表', 'committed', {'res': [dict(id=r[0], v=r[1]) for r in final]},
                      'SELECT id, v FROM res', '被中止事务的写入不在；另一事务的写入在。', '新连接'))
    cross = results['cross']
    uniform = results['uniform']
    aborted = [k for k, v in cross.items() if v == '40P01']
    survivor = [k for k, v in cross.items() if v == 'committed']
    passed = len(aborted) == 1 and len(survivor) == 1 and all(v == 'committed' for v in uniform.values())
    return [case('死锁与统一锁顺序（实测）', passed,
                 expected='交叉：一笔 40P01 回滚、一笔提交；统一：全提交',
                 actual=f'cross={cross} uniform={uniform} 最终 {final}',
                 error=None if passed else '死锁或统一顺序结果不符。', steps=steps,
                 extra=dict(graph=graph, schema=schema, layers=LAYERS, final_check=final,
                            timelines={k: [e for e in events if e['who'] == k] for k in ('A', 'B')}))]


def _plan_tree(node, depth=0):
    out = [dict(depth=depth, type=node.get('Node Type'), est=node.get('Plan Rows'),
                act=node.get('Actual Rows'),
                hit=node.get('Shared Hit Blocks'), read=node.get('Shared Read Blocks'))]
    for ch in node.get('Plans', []) or []:
        out.extend(_plan_tree(ch, depth + 1))
    return out


def _pg_plan(lesson, code, run_dir, choice="", scenario=""):
    ok, why = pglab.available()
    if not ok:
        return _unavailable(why, '索引与执行计划')
    schema = pglab.new_schema()
    c = pglab.connect(schema, autocommit=True)
    c.execute('CREATE TABLE events(id serial PRIMARY KEY, kind text, payload text)')
    c.execute("INSERT INTO events(kind, payload) SELECT CASE WHEN g<=50 THEN 'rare' ELSE 'common' END, 'p' FROM generate_series(1,20000) g")
    c.execute('CREATE TABLE t_small(id serial PRIMARY KEY, kind text)')
    c.execute("INSERT INTO t_small(kind) SELECT CASE WHEN g<=2 THEN 'rare' ELSE 'common' END FROM generate_series(1,20) g")
    c.execute('ANALYZE events')
    c.execute('ANALYZE t_small')
    value = {'high': 'rare', 'low': 'common', 'small': 'rare'}[scenario]
    table = 't_small' if scenario == 'small' else 'events'
    text, err = single_statement(code, 'SELECT')
    if err:
        c.close()
        return [case('索引与计划', False, error=err, steps=[])]
    query = text.replace('__VALUE__', value).replace('__TABLE__', table)
    steps = []
    try:
        rows_before = sorted(tuple(r) for r in c.execute(query))
        plan_before = c.execute(f'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}').fetchone()[0][0]['Plan']
        steps.append(step('建索引前 · 结果', 'read', {'结果行数': [dict(n=len(rows_before))]}, query,
                          f'{len(rows_before)} 行（行序不计）。', '连接 1'))
        steps.append(step('建索引前 · 计划', 'read', {}, f'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}',
                          '估计/实际行数与缓冲区见计划树。', '连接 1'))
        c.execute(f'CREATE INDEX idx_{table}_kind ON {table}(kind)')
        c.execute(f'ANALYZE {table}')
        steps.append(step('平台建索引 + ANALYZE', 'committed', {}, f'CREATE INDEX idx_{table}_kind ON {table}(kind)',
                          '固定数据，只读查询对照。', '平台'))
        rows_after = sorted(tuple(r) for r in c.execute(query))
        plan_after = c.execute(f'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}').fetchone()[0][0]['Plan']
        steps.append(step('建索引后 · 计划', 'read', {}, f'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}',
                          '不要求必须走索引或固定快多少。', '连接 1'))
        baseline = sorted(tuple(r) for r in c.execute(
            f'SELECT id, kind FROM {table} WHERE kind = %s', (value,)))
    except Exception as exc:
        state = getattr(exc, 'sqlstate', '') or type(exc).__name__
        msg = f'{state}: {str(exc).splitlines()[0]}'
        try:
            c.execute('ROLLBACK')
        except Exception:
            pass
        c.close()
        steps.append(step('查询失败', 'error', {}, query, msg + '（已回滚并关闭连接）', '连接 1'))
        return [case(f'索引对照 · {scenario}', False, expected='查询可执行且结果等于独立基准',
                     actual='查询报错', error=msg, steps=steps,
                     extra=dict(schema=schema, layers=LAYERS))]
    c.close()
    same = rows_before == rows_after
    matches_baseline = rows_after == baseline
    passed = same and matches_baseline
    if not matches_baseline:
        reason = (f'结果与独立基准不符：题目要求 {table}.kind = \'{value}\' 应得 {len(baseline)} 行，'
                  f'实际 {len(rows_after)} 行。检查过滤列与值（0 行对 0 行不算对）。')
    elif not same:
        reason = '索引改变了查询结果：不可接受。'
    else:
        reason = None
    return [case(f'索引对照 · {scenario}', passed,
                 expected=f'独立基准 {len(baseline)} 行，且建索引前后相同（行序不计）',
                 actual=f'前 {len(rows_before)} 行 / 后 {len(rows_after)} 行 / 基准 {len(baseline)} 行',
                 error=reason, steps=steps,
                 extra=dict(schema=schema, layers=LAYERS,
                            plan_before=_plan_tree(plan_before), plan_after=_plan_tree(plan_after),
                            rows_before=len(rows_before), rows_after=len(rows_after),
                            baseline_rows=len(baseline)))]


ENGINES.update({
    'pg_ddl': _pg_ddl,
    'pg_iso': _pg_iso,
    'pg_lock': _pg_lock,
    'pg_deadlock': _pg_deadlock,
    'pg_plan': _pg_plan,
})

def _pg_contrast(lesson, code, run_dir, choice='', scenario=''):
    ok, why = pglab.available()
    if not ok:
        return _unavailable(why, '等待/锁忙/死锁同例对照')
    schema = pglab.new_schema()
    seed = pglab.connect(schema, autocommit=True)
    seed.execute('CREATE TABLE t(id int PRIMARY KEY, qty int)')
    seed.cursor().executemany('INSERT INTO t VALUES(%s,%s)', [(1, 2), (2, 2)])
    seed.close()
    t0 = time.monotonic()
    timelines = {'连接 A': [], '连接 B': []}
    results = {}

    def rec(who, op, tone=''):
        timelines[who].append(dict(ms=_ms(t0), op=op, tone=tone))

    def wait_case():
        locked, tried, release = threading.Event(), threading.Event(), threading.Event()

        def a():
            c = pglab.connect(schema, autocommit=True)
            c.execute('BEGIN')
            c.execute('UPDATE t SET qty=qty-1 WHERE id=1')
            rec('连接 A', '锁住 id=1', 'wait')
            locked.set()
            tried.wait(10)
            release.wait(10)
            c.execute('COMMIT')
            rec('连接 A', 'COMMIT 释放', 'ok')
            c.close()

        def b():
            c = pglab.connect(schema, autocommit=True)
            c.execute('BEGIN')
            locked.wait(10)
            rec('连接 B', '尝试 id=1 → 等待', 'wait')
            tried.set()
            c.execute('UPDATE t SET qty=qty-1 WHERE id=1')
            rec('连接 B', 'A 释放后获锁成功', 'ok')
            c.execute('COMMIT')
            c.close()
            release.set()
        ta, tb = threading.Thread(target=a), threading.Thread(target=b)
        ta.start(); tb.start(); ta.join(15); tb.join(15)
        results['wait'] = 'B 等待后成功'

    def timeout_case():
        locked, tried = threading.Event(), threading.Event()

        def a():
            c = pglab.connect(schema, autocommit=True)
            c.execute('BEGIN')
            c.execute('UPDATE t SET qty=qty-1 WHERE id=1')
            rec('连接 A', '持锁不放', 'wait')
            locked.set()
            tried.wait(10)
            time.sleep(0.4)
            c.execute('COMMIT')
            rec('连接 A', 'COMMIT', 'ok')
            c.close()

        def b():
            c = pglab.connect(schema, autocommit=True)
            c.execute('BEGIN')
            c.execute("SET lock_timeout='300ms'")
            locked.wait(10)
            rec('连接 B', '尝试 id=1', 'wait')
            tried.set()
            try:
                c.execute('UPDATE t SET qty=qty-1 WHERE id=1')
                results['timeout'] = 'B 意外成功'
            except Exception as exc:
                results['timeout'] = getattr(exc, 'sqlstate', '') or type(exc).__name__
                c.execute('ROLLBACK')
                rec('连接 B', f'超时 {results["timeout"]} · 零写入回滚', 'fail')
            c.close()
        ta, tb = threading.Thread(target=a), threading.Thread(target=b)
        ta.start(); tb.start(); ta.join(15); tb.join(15)

    def deadlock_case():
        barrier = threading.Barrier(2)

        def w(name, first, second):
            c = pglab.connect(schema, autocommit=True)
            try:
                c.execute('BEGIN')
                c.execute('UPDATE t SET qty=qty-1 WHERE id=%s', (first,))
                rec(name, f'锁住 id={first}', 'wait')
                barrier.wait(10)
                rec(name, f'请求 id={second} → 等待', 'wait')
                c.execute('UPDATE t SET qty=qty-1 WHERE id=%s', (second,))
                c.execute('COMMIT')
                results['deadlock_' + name] = 'committed'
                rec(name, 'COMMIT', 'ok')
            except Exception as exc:
                results['deadlock_' + name] = getattr(exc, 'sqlstate', '') or type(exc).__name__
                c.execute('ROLLBACK')
                rec(name, f'被中止 {results["deadlock_" + name]} · 回滚', 'fail')
            c.close()
        ta = threading.Thread(target=w, args=('连接 A', 1, 2))
        tb = threading.Thread(target=w, args=('连接 B', 2, 1))
        ta.start(); tb.start(); ta.join(20); tb.join(20)

    wait_case()
    timeout_case()
    deadlock_case()
    check = pglab.connect(schema, autocommit=True)
    final = [list(r) for r in check.execute('SELECT id, qty FROM t ORDER BY id')]
    check.close()
    dl = [v for k, v in results.items() if k.startswith('deadlock_')]
    passed = (results.get('wait') == 'B 等待后成功' and results.get('timeout') == '55P03'
              and sorted(dl) == ['40P01', 'committed'])
    steps = [step(f'{who} +{e["ms"]}ms {e["op"]}', 'error' if e['tone'] == 'fail' else 'committed',
                  {}, None, '同一张两行表上的三种命运。', who)
             for who in ('连接 A', '连接 B') for e in timelines[who]]
    steps.append(step('最终表', 'committed', {'t': [dict(id=r[0], qty=r[1]) for r in final]},
                      'SELECT id, qty FROM t', '独立连接复核。', '新连接'))
    return [case('等待 / 锁忙 / 死锁同例对照', passed,
                 expected='等待→成功；锁忙→55P03 零写入；交叉→40P01 一笔回滚',
                 actual=str(results) + f' 最终 {final}',
                 error=None if passed else '三种锁命运与预期不符。', steps=steps,
                 extra=dict(timelines=timelines, schema=schema, layers=LAYERS,
                            contrast=[dict(name='普通等待', result='B 等到 A 释放后成功', note='等待不是失败。'),
                                      dict(name='锁超时', result='55P03 · 零写入', note='超时回滚，同键重试可恢复。'),
                                      dict(name='死锁', result='40P01 · 一笔被中止', note='等待图成环；统一锁顺序可消除。')]))]


ENGINES.update({'pg_contrast': _pg_contrast})

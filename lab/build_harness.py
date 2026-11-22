"""可信验收 harness：在子进程里加载学生 schema/service/tests，跑基线测试与新连接复核。

用法：python -m lab.build_harness <workspace_dir> <sales|hotel>
环境变量 BUILD_SCHEMA 指定隔离 schema。stdout 输出 JSON 报告。
学生代码只在子进程执行，超时由父进程控制；Web 服务不执行学生代码。
"""
import importlib.util
import json
import os
import sys
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lab import pglab


def load_service(path):
    spec = importlib.util.spec_from_file_location('student_service', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def conn():
    return pglab.connect(os.environ['BUILD_SCHEMA'], autocommit=True)


STAGE_TESTS = {
    'sales': {'schema': ['表结构存在'], 'single': ['单次下单落库'], 'insufficient': ['库存不足整单回滚'], 'retry': ['同键重试不重复扣减'], 'race': ['两笔请求抢最后一件']},
    'hotel': {'schema': ['表结构存在'], 'single': ['单晚预订落库'], 'twonights': ['连续两晚同一间房'], 'conflict': ['第二晚写失败整单撤回'], 'retry': ['同键重试不重复占房']},
}


def sales_tests(svc, extra, stage=None):
    results = []
    want = STAGE_TESTS['sales'].get(stage) if stage else None

    def check(name, fn):
        if want and name not in want:
            return
        try:
            fn()
            results.append(dict(name=name, passed=True))
        except Exception as exc:
            results.append(dict(name=name, passed=False, error=f'{type(exc).__name__}: {exc}'))

    c = conn()
    c.execute("INSERT INTO inventory(sku, qty) VALUES('A', 1)")
    c.close()
    payload = dict(request_key='k1', items=[dict(sku='A', qty=1)])
    out = {}
    state = {}

    def race():
        barrier = threading.Barrier(2)

        def buy(key):
            barrier.wait(timeout=10)
            out[key] = svc.place_order(dict(payload, request_key=key))
        ta = threading.Thread(target=buy, args=('k1',))
        tb = threading.Thread(target=buy, args=('k2',))
        ta.start(); tb.start(); ta.join(15); tb.join(15)
        ok = [k for k, v in out.items() if v.get('status') == 'PLACED']
        bad = [k for k, v in out.items() if v.get('error', {}).get('code') == 'INSUFFICIENT_STOCK']
        assert len(ok) == 1 and len(bad) == 1, f'应一成功一库存不足: {out}'
        state['winner'] = ok[0]
    check('两笔请求抢最后一件', race)
    if stage == 'single':
        check('单次下单落库', lambda: _single(svc))
    if stage == 'insufficient':
        check('库存不足整单回滚', lambda: _insufficient(svc))

    def retry():
        key = state.get('winner', 'k1')
        if stage == 'retry':
            key = 'retry-1'
            first = svc.place_order(dict(payload, request_key=key))
            assert first.get('status') == 'PLACED', f'前置下单应成功: {first}'
        r = svc.place_order(dict(payload, request_key=key))
        assert r.get('status') == 'PLACED' and r.get('replayed') is True, f'同键重试应回放: {r}'
        c = conn()
        assert c.execute('SELECT qty FROM inventory WHERE sku=%s', ('A',)).fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 1
        c.close()
    check('同键重试不重复扣减', retry)

    def conflict():
        r = svc.place_order(dict(request_key=state.get('winner', 'k1'), items=[dict(sku='A', qty=2)]))
        assert r.get('error', {}).get('code') == 'IDEMPOTENCY_CONFLICT', f'同键不同内容应冲突: {r}'
        c = conn()
        assert c.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 1
        c.close()
    check('同键不同内容不复用旧单', conflict)

    def final():
        c = conn()
        assert c.execute('SELECT qty FROM inventory WHERE sku=%s', ('A',)).fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM movements WHERE kind='SALE'").fetchone()[0] == 1
        c.close()
    check('新连接复核库存/订单/流水', final)
    return results


def hotel_tests(svc, extra, stage=None):
    results = []
    want = STAGE_TESTS['hotel'].get(stage) if stage else None

    def check(name, fn):
        if want and name not in want:
            return
        try:
            fn()
            results.append(dict(name=name, passed=True))
        except Exception as exc:
            results.append(dict(name=name, passed=False, error=f'{type(exc).__name__}: {exc}'))

    c = conn()
    c.execute("INSERT INTO rooms(room_id, room_type) VALUES('S101','standard'),('S102','standard')")
    c.execute("INSERT INTO room_nights(room_id, night, blocked) VALUES('S101','2026-12-08',0),('S102','2026-12-08',0),('S101','2026-12-09',0),('S102','2026-12-09',0),('S101','2026-12-10',1),('S102','2026-12-10',1)")
    c.execute("INSERT INTO bookings(request_id, request_hash, status, decision) VALUES('PRE','h','BOOKED','{}')")
    c.execute("INSERT INTO booking_nights(request_id, room_id, night) VALUES('PRE','S101','2026-12-08')")
    c.close()

    def ok():
        r = svc.reserve(dict(request_id='R1', check_in='2026-12-08', check_out='2026-12-10'))
        assert r.get('status') == 'BOOKED', f'应订到: {r}'
        c = conn()
        rows = c.execute("SELECT room_id, night FROM booking_nights WHERE request_id='R1' ORDER BY night").fetchall()
        assert len(rows) == 2 and rows[0][0] == rows[1][0], f'两晚必须同房: {rows}'
        assert [r[1] for r in rows] == ['2026-12-08', '2026-12-09'], f'离店日不占: {rows}'
        c.close()
    check('连续两晚同一间房', ok)
    if stage == 'single':
        check('单晚预订落库', lambda: _single_night(svc))

    def reject():
        r = svc.reserve(dict(request_id='R2', check_in='2026-12-09', check_out='2026-12-11'))
        assert r.get('status') == 'REJECTED', f'第二晚封锁应拒订: {r}'
        c = conn()
        assert c.execute("SELECT COUNT(*) FROM booking_nights WHERE request_id='R2'").fetchone()[0] == 0
        c.close()
    check('第二晚不可用整单拒订', reject)

    def rollback():
        try:
            svc.reserve(dict(request_id='R3', check_in='2026-12-08', check_out='2026-12-10', inject_failure=True))
        except Exception:
            pass
        c = conn()
        assert c.execute("SELECT COUNT(*) FROM booking_nights WHERE request_id='R3'").fetchone()[0] == 0, '第二晚写失败后整单撤回应无残留'
        assert c.execute("SELECT COUNT(*) FROM bookings WHERE request_id='R3' AND status='BOOKED'").fetchone()[0] == 0
        c.close()
    check('第二晚写失败整单撤回', rollback)

    def retry():
        if stage == 'retry':
            first = svc.reserve(dict(request_id='R1', check_in='2026-12-08', check_out='2026-12-10'))
            assert first.get('status') == 'BOOKED', f'前置预订应成功: {first}'
        r = svc.reserve(dict(request_id='R1', check_in='2026-12-08', check_out='2026-12-10'))
        assert r.get('replayed') is True, f'同键重试应回放: {r}'
        c = conn()
        assert c.execute("SELECT COUNT(*) FROM booking_nights WHERE request_id='R1'").fetchone()[0] == 2
        c.close()
    check('同键重试不重复占房', retry)
    return results


def _single(svc):
    c = conn()
    c.execute("INSERT INTO inventory(sku, qty) VALUES('S', 5)")
    c.close()
    r = svc.place_order(dict(request_key='single-1', items=[dict(sku='S', qty=1)]))
    assert r.get('status') == 'PLACED', f'单次下单应成功: {r}'
    c = conn()
    assert c.execute("SELECT qty FROM inventory WHERE sku='S'").fetchone()[0] == 4
    assert c.execute("SELECT COUNT(*) FROM orders WHERE request_key='single-1'").fetchone()[0] == 1, '订单行应落库'
    c.close()


def _insufficient(svc):
    c = conn()
    c.execute("INSERT INTO inventory(sku, qty) VALUES('Z', 0)")
    c.close()
    r = svc.place_order(dict(request_key='ins-1', items=[dict(sku='Z', qty=1)]))
    assert r.get('error', {}).get('code') == 'INSUFFICIENT_STOCK', f'库存不足应拒绝: {r}'
    c = conn()
    assert c.execute("SELECT COUNT(*) FROM orders WHERE request_key='ins-1'").fetchone()[0] == 0
    assert c.execute("SELECT qty FROM inventory WHERE sku='Z'").fetchone()[0] == 0
    c.close()


def _single_night(svc):
    r = svc.reserve(dict(request_id='N1', check_in='2026-12-09', check_out='2026-12-10'))
    assert r.get('status') == 'BOOKED', f'单晚应订到: {r}'
    c = conn()
    rows = c.execute("SELECT room_id, night FROM booking_nights WHERE request_id='N1'").fetchall()
    assert len(rows) == 1 and rows[0][1] == '2026-12-09', f'单晚占用应恰一行: {rows}'
    c.close()


def _schema_check(kind):
    c = conn()
    tables = {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")}
    need = {'inventory', 'orders', 'order_items', 'movements'} if kind == 'sales' else {'rooms', 'room_nights', 'bookings', 'booking_nights'}
    assert need <= tables, f'缺表: {need - tables}'
    pk = {r[0] for r in c.execute("SELECT kcu.column_name FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = current_schema()")}
    c.close()
    if kind == 'sales':
        assert 'sku' in pk, 'inventory.sku 应为主键'
    else:
        assert {'request_id', 'night'} <= pk, 'booking_nights(request_id,night) 应为主键'


def main():
    workspace, kind = Path(sys.argv[1]), sys.argv[2]
    stage = sys.argv[3] if len(sys.argv) > 3 else None
    schema = os.environ['BUILD_SCHEMA']
    report = dict(kind=kind, schema=schema, tests=[], error=None)
    try:
        c = pglab.connect(schema, autocommit=True)
        c.execute((workspace / 'schema.sql').read_text(encoding='utf-8'))
        c.close()
        svc = load_service(workspace / 'service.py')
        extra_path = workspace / 'tests_extra.py'
        extra = load_service(extra_path) if extra_path.exists() and extra_path.read_text(encoding='utf-8').strip() else None
        base = sales_tests if kind == 'sales' else hotel_tests
        report['tests'] = base(svc, extra, stage)
        if stage == 'schema':
            try:
                _schema_check(kind)
                report['tests'].insert(0, dict(name='表结构存在', passed=True))
            except Exception as exc:
                report['tests'].insert(0, dict(name='表结构存在', passed=False, error=str(exc)))
        elif stage:
            want = STAGE_TESTS[kind][stage]
            report['tests'] = [t for t in report['tests'] if t['name'] in want]
        if extra:
            for name in dir(extra):
                if name.startswith('test_'):
                    try:
                        getattr(extra, name)(svc, conn)
                        report['tests'].append(dict(name='学生测试 ' + name, passed=True))
                    except Exception as exc:
                        report['tests'].append(dict(name='学生测试 ' + name, passed=False, error=str(exc)))
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        report['traceback'] = traceback.format_exc(limit=5)
    report['passed'] = report['error'] is None and all(t['passed'] for t in report['tests'])
    print(json.dumps(report, ensure_ascii=False))
    sys.exit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
"""课程关无头验证：走页面同款 HTTP 路由 /api/course/run（不直接调内部函数）。

每关一条正确 + 一条典型错误；持久化/回滚/并发查实际数据库状态。
用法：先启动课程服务（start-course.cmd），再
      .venv/Scripts/python.exe -X utf8 scripts/verify_course.py [1|2|3|all]
报告写入 runs/course-verify-report.json（含 HTTP 状态与最终表复核）。
"""
import json
import os
import sqlite3
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from lab import course_server as cs
from lab import course_engines as ce
from lab import course_build as cb

BASE = os.environ.get('COURSE_BASE', 'http://127.0.0.1:8022')

INC = "UPDATE inventory SET qty = qty + 1 WHERE sku = 'A';"
DEC = "UPDATE inventory SET qty = qty - 1 WHERE sku = 'A' AND qty >= 1;"
GUARD = DEC.rstrip(';') + " AND NOT EXISTS (SELECT 1 FROM orders WHERE request_key = 'r1');"

CASES = [
    dict(lesson='00a', batch=1, good="SELECT name FROM sqlite_master WHERE type = 'table';",
         wrong="SELECT name FROM sqlite_master WHERE type = 'index';"),
    dict(lesson='00b', batch=1,
         good="CREATE TABLE inventory (sku TEXT PRIMARY KEY, qty INTEGER NOT NULL CHECK (typeof(qty)='integer' AND qty >= 0));",
         wrong="CREATE TABLE inventory (sku TEXT, qty INTEGER NOT NULL CHECK (typeof(qty)='integer' AND qty >= 0));"),
    dict(lesson='00c', batch=1, good="INSERT INTO inventory(sku, qty) VALUES ('A', 1);",
         wrong="INSERT INTO inventory(qty, sku) VALUES ('A', 1);"),
    dict(lesson='00d', batch=1, good="SELECT sku, qty FROM inventory WHERE sku = 'A';",
         wrong="SELECT sku, qty FROM inventory WHERE sku = 'B';"),
    dict(lesson='select', batch=1, good='SELECT sku, qty FROM inventory ORDER BY sku;',
         wrong='SELECT sku FROM inventory ORDER BY sku;'),
    dict(lesson='where', batch=1, good='SELECT sku, qty FROM inventory WHERE qty >= 2 ORDER BY sku;',
         wrong='SELECT sku, qty FROM inventory WHERE qty > 2 ORDER BY sku;'),
    dict(lesson='fk', batch=1,
         good='CREATE TABLE orders (id INTEGER PRIMARY KEY, sku TEXT NOT NULL REFERENCES products(sku), qty INTEGER NOT NULL CHECK (qty >= 1));',
         wrong='CREATE TABLE orders (id INTEGER PRIMARY KEY, sku TEXT NOT NULL, qty INTEGER NOT NULL CHECK (qty >= 1));'),
    dict(lesson='join', batch=1,
         good='SELECT o.id, p.name, o.qty FROM orders o JOIN products p ON p.sku = o.sku ORDER BY o.id;',
         wrong='SELECT o.id, p.name, o.qty FROM orders o JOIN products p ON p.sku = o.id ORDER BY o.id;'),
    dict(lesson='stock', batch=2, good="SELECT sku, qty FROM inventory WHERE sku = 'A';",
         wrong="SELECT sku, qty FROM inventory WHERE sku = 'A' AND qty > 0;"),
    dict(lesson='decrement', batch=2, good=DEC, wrong="UPDATE inventory SET qty = qty - 1 WHERE sku = 'A';"),
    dict(lesson='commit', batch=2, good=INC, wrong="UPDATE inventory SET qty = qty WHERE sku = 'A';"),
    dict(lesson='rollback', batch=2, good=INC, wrong="UPDATE inventory SET qty = qty WHERE sku = 'A';"),
    dict(lesson='retry', batch=2, good=GUARD, wrong=DEC),
    dict(lesson='choice', batch=2, good=INC, wrong="UPDATE inventory SET qty = qty WHERE sku = 'A';",
         choice='rollback'),
    dict(lesson='choice', batch=2, good=INC, wrong="UPDATE inventory SET qty = qty WHERE sku = 'A';",
         choice='commit', tag='choice-commit'),
    dict(lesson='order', batch=2, good=DEC, wrong="UPDATE inventory SET qty = qty - 2 WHERE sku = 'A';",
         scenario='success'),
    dict(lesson='order', batch=2, good=DEC, wrong="UPDATE inventory SET qty = qty - 1 WHERE sku = 'A';",
         scenario='insufficient'),
    dict(lesson='order', batch=2, good=DEC, wrong='SELECT 1;',
         scenario='missing'),
    dict(lesson='wait', batch=3, good=DEC, wrong="UPDATE inventory SET qty = qty WHERE sku = 'A';"),
    dict(lesson='busy', batch=3, good=DEC, wrong="UPDATE inventory SET qty = qty WHERE sku = 'A';"),
    dict(lesson='exit', batch=3, good="INSERT INTO booking_nights VALUES ('r1', 'S101', '2026-12-09', 0, 0);",
         wrong="INSERT INTO bookings VALUES ('r1', 'BOOKED');"),
    dict(lesson='pg-connect', batch=4,
         good='CREATE TABLE inventory (sku TEXT PRIMARY KEY, qty INTEGER NOT NULL CHECK (qty >= 0));',
         wrong='CREATE TABLE inventory (sku TEXT, qty INTEGER NOT NULL CHECK (qty >= 0));'),
    dict(lesson='pg-iso', batch=4, good='SELECT qty FROM t WHERE id = 1;',
         wrong='SELECT qty FROM t WHERE id = 2;', scenario='rc'),
    dict(lesson='pg-iso', batch=4, good='SELECT qty FROM t WHERE id = 1;',
         wrong='SELECT qty FROM t WHERE id = 2;', scenario='rr'),
    dict(lesson='pg-lock', batch=4, good='UPDATE t SET qty = qty - 1 WHERE id = 1 AND qty >= 1;',
         wrong='UPDATE t SET qty = qty - 1;', scenario='same'),
    dict(lesson='pg-lock', batch=4, good='UPDATE t SET qty = qty - 1 WHERE id = 2 AND qty >= 1;',
         wrong='UPDATE t SET qty = qty - 1;', scenario='different'),
    dict(lesson='pg-lock', batch=4, good='UPDATE t SET qty = qty - 1 WHERE id = 1 AND qty >= 1;',
         wrong='UPDATE t SET qty = qty - 1 WHERE id = 2 AND qty >= 1;', scenario='timeout'),
    dict(lesson='deadlock', batch=4, good='-- demo', wrong='-- demo', deadlock_check=True),
    dict(lesson='pg-plan', batch=4, good="SELECT id, kind FROM __TABLE__ WHERE kind = 'rare';",
         wrong='SELECT id, kind FROM __TABLE__ WHERE kind = __VALUE__;', scenario='high'),
    dict(lesson='pg-plan', batch=4, good="SELECT id, kind FROM __TABLE__ WHERE kind = 'rare';",
         wrong="SELECT id, kind FROM __TABLE__ WHERE kind = 'absent';", scenario='high'),
    dict(lesson='pg-iso', batch=4, good='SELECT qty FROM t WHERE id = 1;',
         wrong='SELECT qty FROM t WHERE id = 2;', scenario='rc'),
    dict(lesson='pg-iso', batch=4, good='SELECT qty FROM t WHERE id = 1;',
         wrong='SELECT nope FROM t WHERE id = 1;', scenario='rr'),
    dict(lesson='pg-plan', batch=4, good="SELECT id, kind FROM __TABLE__ WHERE kind = 'common';",
         wrong='SELECT id, kind FROM __TABLE__ WHERE kind = __VALUE__;', scenario='low'),
    dict(lesson='pg-plan', batch=4, good="SELECT id, kind FROM __TABLE__ WHERE kind = 'rare';",
         wrong='SELECT id, kind FROM __TABLE__ WHERE kind = __VALUE__;', scenario='small'),
    dict(lesson='pg-build-sales', batch=4, good='reference', wrong='starter',
         files_good=dict(schema=cb.REF_SCHEMA_SALES, service=cb.REF_SERVICE_SALES, tests=''),
         files_wrong=dict(schema=cb.STARTER_SCHEMA_SALES, service=cb.STARTER_SERVICE_SALES, tests='')),
    dict(lesson='pg-build-hotel', batch=4, good='reference', wrong='starter',
         files_good=dict(schema=cb.REF_SCHEMA_HOTEL, service=cb.REF_SERVICE_HOTEL, tests=''),
         files_wrong=dict(schema=cb.STARTER_SCHEMA_HOTEL, service=cb.STARTER_SERVICE_HOTEL, tests='')),
    dict(lesson='cpp-page', batch=5, good='insert(3, "abcd");', wrong='read(99);', scenario='empty'),
    dict(lesson='cpp-page', batch=5, good='insert(3, "abcdefghijklmnopqrstuvwx");', wrong='insert(3, "ab");', scenario='exact'),
    dict(lesson='cpp-page', batch=5, good='insert(3, "ab");', wrong='read(3);', scenario='overflow'),
    dict(lesson='cpp-buffer', batch=5, good='pin(1);', wrong='pin(1);', scenario='cap1', both_pass=True),
    dict(lesson='cpp-buffer', batch=5, good='pin(1);', wrong='unpin(1);', scenario='pinned'),
    dict(lesson='cpp-buffer', batch=5, good='pin(1);', wrong='pin(1);', scenario='writeback', both_pass=True),
    dict(lesson='cpp-btree', batch=5, good='search(5);', wrong='search(5);', scenario='empty', both_pass=True),
    dict(lesson='cpp-btree', batch=5, good='search(2);', wrong='search(1);', scenario='missing'),
    dict(lesson='cpp-btree', batch=5, good='search(1);', wrong='search(1);', scenario='dup', both_pass=True),
    dict(lesson='cpp-btree', batch=5, good='search(3);', wrong='search(3);', scenario='split', both_pass=True),
    dict(lesson='cpp-btree', batch=5, good='search(3);', wrong='search(3);', scenario='scan', both_pass=True),
    dict(lesson='cpp-exec', batch=5, good='qty >= 2', wrong='qty >= 2', scenario='empty', both_pass=True),
    dict(lesson='cpp-exec', batch=5, good='qty >= 2', wrong='qty >= 3', scenario='boundary'),
    dict(lesson='cpp-exec', batch=5, good='qty >= 2', wrong='qty >= 2', scenario='project', both_pass=True),
    dict(lesson='cpp-locks', batch=5, good='-- reference', wrong='-- reference', scenario='conflict', both_pass=True),
    dict(lesson='cpp-locks', batch=5, good='-- reference', wrong='-- reference', scenario='rollback', both_pass=True),
    dict(lesson='cpp-locks', batch=5, good='-- reference', wrong='-- reference', scenario='shared', both_pass=True),
    dict(lesson='pg-contrast', batch=6, good='-- demo', wrong='-- demo', both_pass=True),
    dict(lesson='pg-guide-sales-schema', batch=6, good='reference', wrong='starter',
         files_good=dict(schema=cb.REF_SCHEMA_SALES, service=cb.REF_SERVICE_SALES, tests=''),
         files_wrong=dict(schema=cb.GUIDE_STARTERS[('sales', 'schema')] if 'schema'=='schema' else cb.REF_SCHEMA_SALES, service=cb.REF_SERVICE_SALES if 'schema'=='schema' else cb.GUIDE_STARTERS[('sales', 'schema')], tests='')),
    dict(lesson='pg-guide-sales-single', batch=6, good='reference', wrong='starter',
         files_good=dict(schema=cb.REF_SCHEMA_SALES, service=cb.REF_SERVICE_SALES, tests=''),
         files_wrong=dict(schema=cb.GUIDE_STARTERS[('sales', 'schema')] if 'single'=='schema' else cb.REF_SCHEMA_SALES, service=cb.REF_SERVICE_SALES if 'single'=='schema' else cb.GUIDE_STARTERS[('sales', 'single')], tests='')),
    dict(lesson='pg-guide-sales-insufficient', batch=6, good='reference', wrong='starter',
         files_good=dict(schema=cb.REF_SCHEMA_SALES, service=cb.REF_SERVICE_SALES, tests=''),
         files_wrong=dict(schema=cb.GUIDE_STARTERS[('sales', 'schema')] if 'insufficient'=='schema' else cb.REF_SCHEMA_SALES, service=cb.REF_SERVICE_SALES if 'insufficient'=='schema' else cb.GUIDE_STARTERS[('sales', 'insufficient')], tests='')),
    dict(lesson='pg-guide-sales-retry', batch=6, good='reference', wrong='starter',
         files_good=dict(schema=cb.REF_SCHEMA_SALES, service=cb.REF_SERVICE_SALES, tests=''),
         files_wrong=dict(schema=cb.GUIDE_STARTERS[('sales', 'schema')] if 'retry'=='schema' else cb.REF_SCHEMA_SALES, service=cb.REF_SERVICE_SALES if 'retry'=='schema' else cb.GUIDE_STARTERS[('sales', 'retry')], tests='')),
    dict(lesson='pg-guide-sales-race', batch=6, good='reference', wrong='starter',
         files_good=dict(schema=cb.REF_SCHEMA_SALES, service=cb.REF_SERVICE_SALES, tests=''),
         files_wrong=dict(schema=cb.GUIDE_STARTERS[('sales', 'schema')] if 'race'=='schema' else cb.REF_SCHEMA_SALES, service=cb.REF_SERVICE_SALES if 'race'=='schema' else cb.GUIDE_STARTERS[('sales', 'race')], tests='')),
    dict(lesson='pg-guide-hotel-schema', batch=6, good='reference', wrong='starter',
         files_good=dict(schema=cb.REF_SCHEMA_HOTEL, service=cb.REF_SERVICE_HOTEL, tests=''),
         files_wrong=dict(schema=cb.GUIDE_STARTERS[('hotel', 'schema')] if 'schema'=='schema' else cb.REF_SCHEMA_HOTEL, service=cb.REF_SERVICE_HOTEL if 'schema'=='schema' else cb.GUIDE_STARTERS[('hotel', 'schema')], tests='')),
    dict(lesson='pg-guide-hotel-single', batch=6, good='reference', wrong='starter',
         files_good=dict(schema=cb.REF_SCHEMA_HOTEL, service=cb.REF_SERVICE_HOTEL, tests=''),
         files_wrong=dict(schema=cb.GUIDE_STARTERS[('hotel', 'schema')] if 'single'=='schema' else cb.REF_SCHEMA_HOTEL, service=cb.REF_SERVICE_HOTEL if 'single'=='schema' else cb.GUIDE_STARTERS[('hotel', 'single')], tests='')),
    dict(lesson='pg-guide-hotel-twonights', batch=6, good='reference', wrong='starter',
         files_good=dict(schema=cb.REF_SCHEMA_HOTEL, service=cb.REF_SERVICE_HOTEL, tests=''),
         files_wrong=dict(schema=cb.GUIDE_STARTERS[('hotel', 'schema')] if 'twonights'=='schema' else cb.REF_SCHEMA_HOTEL, service=cb.REF_SERVICE_HOTEL if 'twonights'=='schema' else cb.GUIDE_STARTERS[('hotel', 'twonights')], tests='')),
    dict(lesson='pg-guide-hotel-conflict', batch=6, good='reference', wrong='starter',
         files_good=dict(schema=cb.REF_SCHEMA_HOTEL, service=cb.REF_SERVICE_HOTEL, tests=''),
         files_wrong=dict(schema=cb.GUIDE_STARTERS[('hotel', 'schema')] if 'conflict'=='schema' else cb.REF_SCHEMA_HOTEL, service=cb.REF_SERVICE_HOTEL if 'conflict'=='schema' else cb.GUIDE_STARTERS[('hotel', 'conflict')], tests='')),
    dict(lesson='pg-guide-hotel-retry', batch=6, good='reference', wrong='starter',
         files_good=dict(schema=cb.REF_SCHEMA_HOTEL, service=cb.REF_SERVICE_HOTEL, tests=''),
         files_wrong=dict(schema=cb.GUIDE_STARTERS[('hotel', 'schema')] if 'retry'=='schema' else cb.REF_SCHEMA_HOTEL, service=cb.REF_SERVICE_HOTEL if 'retry'=='schema' else cb.GUIDE_STARTERS[('hotel', 'retry')], tests='')),
    dict(lesson='pg-final-sales', batch=6, good='reference', wrong='starter',
         files_good=dict(schema=cb.REF_SCHEMA_SALES, service=cb.REF_SERVICE_SALES, tests=''),
         files_wrong=dict(schema=cb.STARTER_SCHEMA_SALES, service=cb.STARTER_SERVICE_SALES, tests='')),
    dict(lesson='pg-final-hotel', batch=6, good='reference', wrong='starter',
         files_good=dict(schema=cb.REF_SCHEMA_HOTEL, service=cb.REF_SERVICE_HOTEL, tests=''),
         files_wrong=dict(schema=cb.STARTER_SCHEMA_HOTEL, service=cb.STARTER_SERVICE_HOTEL, tests='')),
    dict(lesson='d2', batch=7, good='SELECT request_key FROM orders WHERE request_key=%s',
         wrong='SELECT 1 WHERE false'),
    dict(lesson='l4', batch=7, good='rn = 1', wrong='rn >= 1'),
    dict(lesson='l3', batch=7, good='SELECT biz_date, SUM(net_cents) AS net_cents FROM dedup GROUP BY biz_date',
         wrong="SELECT biz_date, SUM(net_cents) AS net_cents FROM dedup WHERE biz_date='1999-01-01' GROUP BY biz_date"),
    dict(lesson='l3', batch=7, scenario='date',
         good="SELECT biz_date, SUM(net_cents) AS net_cents FROM dedup WHERE biz_date=DATE '2026-12-08' GROUP BY biz_date",
         wrong='SELECT biz_date, SUM(net_cents) AS net_cents FROM dedup GROUP BY biz_date'),
    dict(lesson='l5', batch=7, good='SELECT biz_date, SUM(net_cents) AS net_cents, SUM(room_nights) AS room_nights FROM dedup GROUP BY biz_date',
         wrong='SELECT biz_date, COUNT(net_cents) AS net_cents, SUM(room_nights) AS room_nights FROM dedup GROUP BY biz_date'),
    dict(lesson='c1', batch=7, good='SELECT sku, qty FROM products', wrong='SELECT sku FROM products'),
    dict(lesson='d5', batch=7, good='max_attempts: 4', wrong='max_attempts: 1', scenario='recover'),
    dict(lesson='f1', batch=7, good='rn = 1', wrong='rn >= 1'),
    dict(lesson='l2', batch=7, good='', wrong='', scenario='', both_pass=True),
    dict(lesson='l2', batch=7, good='', wrong='', scenario='all-bad', both_pass=True),
    dict(lesson='l2', batch=7, good='', wrong='', scenario='no-bad', both_pass=True),
    dict(lesson='d3', batch=7, good='', wrong='', both_pass=True),
    dict(lesson='d4', batch=7, good='', wrong='', both_pass=True),
    dict(lesson='k1', batch=7, good='', wrong='', both_pass=True),
    dict(lesson='k3', batch=7, good='', wrong='', unmeasured=True),
]


def run_case(spec, code):
    payload = dict(lesson_id=spec['lesson'], code=code,
                   choice=spec.get('choice', ''), scenario=spec.get('scenario', ''))
    fkey = 'files_good' if code == spec['good'] else 'files_wrong'
    if spec.get(fkey):
        payload['files'] = spec[fkey]
    req = urllib.request.Request(BASE + '/api/course/run',
                                 data=json.dumps(payload).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status = r.status
            body = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return dict(run_id='http-error', passed=False, http_status=e.code,
                    cases=[dict(name='HTTP 错误', passed=False, error=f'HTTP {e.code}: {e.read().decode("utf-8", "replace")[:200]}', steps=[])])
    body['http_status'] = status
    return body


def db_facts(res):
    facts = []
    run_dir = ce.COURSE_RUNS / res['run_id']
    if run_dir.is_dir():
        for db in sorted(run_dir.glob('*.db')):
            c = sqlite3.connect(str(db))
            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for t in tables:
                rows = [list(r) for r in c.execute(f'SELECT * FROM {t} ORDER BY rowid')]
                facts.append({'file': db.name, 'table': t, 'rows': rows})
            c.close()
    return facts


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else 'all'
    wants = set(want.split(','))
    try:
        with urllib.request.urlopen(BASE + '/api/course', timeout=5) as r:
            assert r.status == 200
    except Exception as exc:
        print(f'课程服务不可用（{BASE}）：{exc}。先运行 start-course.cmd 再验证。')
        sys.exit(2)
    report, bad = [], 0
    for spec in CASES:
        if want == 'skip':
            if spec['lesson'] not in ('pg-build-sales', 'pg-guide-sales-single', 'pg-final-hotel', 'cpp-page', 'cpp-locks'):
                continue
        elif want != 'all' and str(spec['batch']) not in wants:
            continue
        rg, rw = run_case(spec, spec['good']), run_case(spec, spec['wrong'])
        if want == 'skip':
            ok = rg['passed'] is None and str(rg['cases'][0]['extra'].get('not_measured', '')).startswith('跳过')
        elif spec.get('unmeasured'):
            ok = rg['passed'] is None and '未实测' in str(rg['cases'][0]['extra'].get('not_measured', ''))
        elif spec.get('both_pass'):
            ok = rg['passed'] is True and rw['passed'] is True
        elif spec.get('deadlock_check'):
            ok = rg['passed'] is True and rw['passed'] is True and \
                any('40P01' in str(c.get('actual', '')) for c in rg['cases'])
        else:
            ok = rg['passed'] is True and rw['passed'] is False
        facts = db_facts(rg)
        pg_facts = [c.get('extra', {}).get('final_check') for c in rg['cases']
                    if c.get('extra', {}).get('final_check') is not None]
        label = spec['lesson'] + (':' + spec['tag'] if spec.get('tag') else '') + \
            (':' + spec['scenario'] if spec.get('scenario') else '')
        report.append(dict(lesson=label, batch=spec['batch'], correct_passed=rg['passed'],
                           good_code=spec['good'], scenario=spec.get('scenario', ''),
                           files_good=spec.get('files_good'),
                           wrong_passed=rw['passed'], verdict_ok=ok, run_id=rg['run_id'],
                           http_status=rg.get('http_status'), final_db=facts, pg_final=pg_facts,
                           wrong_errors=[c.get('error') for c in rw['cases'] if c.get('error')]))
        print(('OK  ' if ok else 'BAD '), label,
              f'HTTP={rg.get("http_status")} 正确={rg["passed"]} 错误={rw["passed"]}',
              f'文件复核{len(facts)}表' if facts else '')
        if not ok:
            bad += 1
    out = ROOT / 'runs' / 'course-verify-report.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n{len(report) - bad}/{len(report)} 用例符合预期；报告 {out}')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
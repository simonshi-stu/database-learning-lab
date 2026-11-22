"""无头验证矩阵：两题 × 参考解/错误解/起始代码/SQL 模式，断言判题结果符合预期。

用法：.venv/Scripts/python.exe -X utf8 scripts/verify.py
结果写入 runs/verify-report.json（每次运行覆盖）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from lab import runner
from lab.problems import sales, hotel

CASES = [
    ('sales-last-unit', 'python', sales.REFERENCE, 'race', True, '销售参考解'),
    ('sales-last-unit', 'python', sales.WRONG_READ_THEN_WRITE, 'race', False, '销售错误解①先查后写'),
    ('sales-last-unit', 'python', sales.WRONG_NO_IDEMPOTENCY, 'race', False, '销售错误解②漏幂等'),
    ('sales-last-unit', 'python', sales.STARTER, 'race', False, '销售起始代码'),
    ('sales-last-unit', 'python', sales.REFERENCE, 'busy', True, '销售参考解·锁忙场景'),
    ('sales-last-unit', 'python', sales.REFERENCE, 'conflict', True, '销售参考解·同键不同内容'),
    ('sales-last-unit', 'python', sales.WRONG_NO_IDEMPOTENCY, 'conflict', False, '销售漏幂等·同键不同内容'),
    ('sales-last-unit', 'sql', sales.SQL_REFERENCE, 'race', True, '销售参考SQL'),
    ('sales-last-unit', 'sql', sales.SQL_WRONG, 'race', False, '销售错误SQL'),
    ('hotel-two-nights', 'python', hotel.REFERENCE, 'full', True, '酒店参考解'),
    ('hotel-two-nights', 'python', hotel.WRONG_PER_NIGHT, 'full', False, '酒店错误解①逐晚挑房'),
    ('hotel-two-nights', 'python', hotel.WRONG_NO_REJECT_RECORD, 'full', False, '酒店错误解②拒订不落库'),
    ('hotel-two-nights', 'python', hotel.STARTER, 'full', False, '酒店起始代码'),
    ('hotel-two-nights', 'sql', hotel.SQL_REFERENCE, 'full', True, '酒店参考SQL'),
    ('hotel-two-nights', 'sql', hotel.SQL_WRONG, 'full', False, '酒店错误SQL'),
]


def main():
    report = []
    failures = 0
    for problem, mode, code, scenario, expect_pass, name in CASES:
        result = runner.run_problem(problem, mode, code, scenario)
        judge = result['judge']
        if result['error']:
            ok = False
            detail = result['error']
            failed_checks = []
        else:
            ok = judge['passed'] == expect_pass
            detail = f"passed={judge['passed']} (期望 {expect_pass})"
            failed_checks = [c['name'] for c in judge['checks'] if not c['passed']]
        report.append({'case': name, 'problem': problem, 'mode': mode, 'scenario': scenario,
                       'run_id': result['run_id'], 'expected_pass': expect_pass,
                       'verdict_ok': ok, 'detail': detail, 'failed_checks': failed_checks,
                       'notes': judge['notes'] if judge else []})
        print(('OK  ' if ok else 'BAD '), name, '|', detail, '|', ', '.join(failed_checks))
        if not ok:
            failures += 1
    out = ROOT / 'runs' / 'verify-report.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n{len(CASES) - failures}/{len(CASES)} 个用例符合预期；报告：{out}')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
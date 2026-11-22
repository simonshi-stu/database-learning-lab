"""63 关逐关验收：参考解 + 反例 + 实际引擎 + 通过/失败/跳过/未覆盖。

复用 verify_course.CASES 与 verify.CASES 的正反用例；其余关用参考解/起始代码。
结果写 docs/acceptance-63.md 与 runs/acceptance-63.json。
"""
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from lab import course_server as cs
sys.path.insert(0, str(ROOT / 'scripts'))
import verify_course as vc
import verify as vo

BASE = 'http://127.0.0.1:8022'


def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, {'error': e.read().decode('utf-8', 'replace')[:120]}
    except Exception as e:
        return 0, {'error': str(e)}


def engine_of(l):
    e = l.get('engine') or ''
    if l.get('mode') == 'capstone':
        return 'SQLite(原实验室)'
    if e == 'cloud':
        return 'PostgreSQL+DuckDB/数据湖'
    if e == 'k8s':
        return 'Docker/K8s'
    if e.startswith('pg_'):
        return 'PostgreSQL'
    if e.startswith('cpp_'):
        return '教学C++'
    return 'SQLite'


def main():
    spec_by_lesson = {}
    for c in vc.CASES:
        spec_by_lesson.setdefault(c['lesson'], c)
    cap_by_problem = {}
    for row in vo.CASES:
        cap_by_problem.setdefault(row[0], row)
    rows = []
    for l in cs.LESSONS:
        lid = l['id']
        eng = engine_of(l)
        if l.get('mode') == 'capstone':
            cr = cap_by_problem.get(l['problem'])
            good = cr[2] if cr else ''
            scen = cr[3] if cr else ''
            wrong = (vo.sales.WRONG_READ_THEN_WRITE if l['problem'] == 'sales-last-unit' else vo.hotel.WRONG_PER_NIGHT) if cr else ''
            path, payload_g = '/api/run', dict(problem=l['problem'], mode='python', scenario=scen, code=good)
            payload_w = dict(problem=l['problem'], mode='python', scenario=scen, code=wrong)
        else:
            sp = spec_by_lesson.get(lid)
            if sp:
                good, wrong = sp['good'], sp['wrong']
                scen = sp.get('scenario', '')
                fg, fw = sp.get('files_good'), sp.get('files_wrong')
            else:
                presets = l.get('build_presets') or {}
                ref = None
                for k, v in presets.items():
                    ref = next((p['code'] for p in v if p['id'] in ('reference', 'solution')), None) or ref
                good = ref or l.get('solution') or ''
                wrong = l.get('starter') or ''
                scen = ''
                fg = fw = None
            path = '/api/course/run'
            payload_g = dict(lesson_id=lid, code=good, scenario=scen)
            payload_w = dict(lesson_id=lid, code=wrong, scenario=scen)
            if fg:
                payload_g['files'] = fg
            if fw:
                payload_w['files'] = fw
        sg, dg = post(path, payload_g)
        demo = (not spec_by_lesson.get(lid)) and (l.get('mode') != 'capstone') and (payload_w.get('code') == payload_g.get('code')) and not payload_g.get('files')
        sw, dw = (0, {'passed': None, 'cases': []}) if demo else post(path, payload_w)
        pg = dg.get('passed')
        if pg is None: pg = (dg.get('judge') or {}).get('passed')
        pw = dw.get('passed')
        if pw is None: pw = (dw.get('judge') or {}).get('passed')
        skip_g = pg is None
        status_g = '跳过' if skip_g else ('通过' if pg else '失败')
        status_w = '无编辑反例' if demo else ('跳过' if pw is None else ('通过' if pw else '失败'))
        reason = ''
        if skip_g:
            reason = str((dg.get('cases') or [{}])[0].get('extra', {}).get('not_measured', ''))[:60]
        rows.append(dict(id=lid, title=l['title'], engine=eng, ref_status=status_g, ref_http=sg,
                         counter_status=status_w, counter_http=sw, reason=reason,
                         ref_actual=str((dg.get('cases') or [{}])[0].get('actual', ''))[:50]))
        print(f"{lid:16s} {eng:22s} 参考={status_g} 反例={status_w} {reason[:30]}")
    out = ROOT / 'runs' / 'acceptance-63.json'
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    md = ['# 63 关逐关验收', '', '| 题号 | 引擎 | 参考解 | 反例 | 跳过原因/备注 |', '| --- | --- | --- | --- | --- |']
    for r in rows:
        md.append(f"| {r['id']} | {r['engine']} | {r['ref_status']} | {r['counter_status']} | {r['reason'] or r['ref_actual']} |")
    npass = sum(1 for r in rows if r['ref_status'] == '通过')
    nskip = sum(1 for r in rows if r['ref_status'] == '跳过')
    nfail = sum(1 for r in rows if r['ref_status'] == '失败')
    md.append('')
    md.append(f'参考解：通过 {npass} / 失败 {nfail} / 跳过 {nskip}（共 {len(rows)} 关）。')
    (ROOT / 'docs' / 'acceptance-63.md').write_text('\n'.join(md), encoding='utf-8')
    print(f'\n参考解 通过{npass} 失败{nfail} 跳过{nskip} / {len(rows)}')


if __name__ == '__main__':
    main()
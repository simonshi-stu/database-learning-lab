"""第 2 批引擎：C++ 教学内核回放。Web 只运行预编译的教学二进制（固定 argv、超时），
或读取本地命令行编译学生草稿生成的回放；不在 Web 服务里编译/执行学生代码。
"""
import json
import os
import re
import subprocess

from .course_engines import case, step, ENGINES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, 'bin', 'teach.exe')


def _run_binary(module, scenario, value, run_dir):
    if not os.path.exists(BIN):
        return None, '教学二进制未构建：运行 scripts\\cpp_build.cmd 后重跑。'
    proc = subprocess.run([BIN, module, scenario, value, str(run_dir)],
                          cwd=str(run_dir), capture_output=True, timeout=20)
    events = []
    for line in proc.stdout.decode('utf-8', 'replace').splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    if proc.returncode != 0:
        return None, f'教学引擎退出码 {proc.returncode}: {proc.stderr.decode("utf-8", "replace")[:200]}'
    return events, None


def _steps(events, table_key):
    out = []
    for e in events:
        tables = {}
        if table_key in e:
            tables[table_key] = e[table_key]
        out.append(step(e.get('op', '?'), 'committed' if e.get('ok', True) else 'error',
                        tables, None, e.get('reason') or e.get('note') or '', '教学引擎', None))
    return out


def _value(code):
    m = re.search(r'"([^"]*)"', code or '')
    return m.group(1) if m else None


def _cpp_page(lesson, code, run_dir, choice='', scenario=''):
    value = _value(code)
    if value is None:
        return [case('存储页', False, error='编辑器需要一条 insert(3, "..."); 语句。', steps=[])]
    events, err = _run_binary('page', scenario, value, run_dir)
    if err:
        return [case('存储页 · ' + scenario, None, error=None, steps=[],
                     extra=dict(not_measured='示意 / 未实测（' + err + '）'))]
    ins = next((e for e in events if e['op'] == 'insert'), {})
    reopen = next((e for e in events if e['op'] == 'reopen_read'), {})
    missing = next((e for e in events if e['op'] == 'read_missing'), None)
    keys = [r['key'] for r in reopen.get('records', [])]
    if scenario == 'empty':
        passed = ins.get('ok') and missing is not None and not missing.get('ok') and 3 in keys
        want = '空页插入成功、读不存在键失败、关后重读含新记录'
    elif scenario == 'exact':
        passed = ins.get('ok') and ins.get('free') == 0 and 3 in keys
        want = '恰好装满：free=0 且记录在'
    else:
        passed = ins.get('ok') is False and 'page_full' in ins.get('reason', '') and 3 not in keys
        want = '满页拒绝整条记录：重读后无 key3'
    return [case('存储页 · ' + scenario, passed, expected=want,
                 actual=f"insert ok={ins.get('ok')} free={ins.get('free')} 重读 keys={keys}",
                 error=None if passed else '页行为与场景预期不符。',
                 steps=_steps(events, 'records'), extra=dict(module='page'))]


def _cpp_buffer(lesson, code, run_dir, choice='', scenario=''):
    events, err = _run_binary('buffer', scenario, 'unpin' if 'unpin' in (code or '') else 'pin', run_dir)
    if err:
        return [case('缓冲池 · ' + scenario, None, error=None, steps=[],
                     extra=dict(not_measured='示意 / 未实测（' + err + '）'))]
    ops = [e['op'] for e in events]
    pinned_cmd = 'unpin' not in (code or '')
    if scenario == 'cap1':
        passed = 'evict' in ops and 'evict_blocked' not in ops and ops.count('miss') >= 2
        want = '容量 1：命中/未命中/淘汰交替，无全固定阻塞'
    elif scenario == 'pinned':
        passed = 'evict_blocked' in ops
        want = '固定页不可淘汰（evict_blocked）；unpin 是错误动作'
    else:
        wb = next((e for e in events if e['op'] == 'writeback'), None)
        rr = next((e for e in events if e['op'] == 'reread'), None)
        passed = wb is not None and rr is not None and '99' in rr.get('note', '')
        want = '脏页写回后重新读到新值 99'
    return [case('缓冲池 · ' + scenario, passed, expected=want, actual='ops=' + ','.join(ops),
                 error=None if passed else '缓冲池行为与场景预期不符。',
                 steps=_steps(events, 'frames'), extra=dict(module='buffer'))]


def _cpp_btree(lesson, code, run_dir, choice='', scenario=''):
    m = re.search(r'search\((\d+)\)', code or '')
    key = int(m.group(1)) if m else 3
    events, err = _run_binary('btree', scenario, str(key), run_dir)
    if err:
        return [case('B+ 树 · ' + scenario, None, error=None, steps=[],
                     extra=dict(not_measured='示意 / 未实测（' + err + '）'))]
    ops = [e['op'] for e in events]
    search = next((e for e in events if e['op'] == 'search'), None)
    scan = next((e for e in events if e['op'] == 'range_scan'), None)
    if scenario == 'empty':
        passed = search is not None and search.get('found') is False
        want = '空树点查不存在'
    elif scenario == 'missing':
        passed = search is not None and search.get('found') is False
        want = '不存在的键点查失败'
    elif scenario == 'dup':
        passed = 'duplicate_reject' in ops
        want = '重复键策略：拒绝'
    elif scenario == 'split':
        passed = ('split' in ops or 'split_root' in ops)
        want = '插入触发节点分裂'
    else:
        passed = scan is not None and scan.get('keys') == [2, 3, 4]
        want = '跨叶范围扫描 2..4'
    return [case('B+ 树 · ' + scenario, passed, expected=want, actual='ops=' + ','.join(ops),
                 error=None if passed else 'B+ 树行为与场景预期不符。',
                 steps=_steps(events, 'nodes'), extra=dict(module='btree'))]


def _cpp_exec(lesson, code, run_dir, choice='', scenario=''):
    m = re.search(r'>=\s*(\d+)', code or '')
    threshold = int(m.group(1)) if m else 2
    events, err = _run_binary('exec', scenario, str(threshold), run_dir)
    if err:
        return [case('查询执行 · ' + scenario, None, error=None, steps=[],
                     extra=dict(not_measured='示意 / 未实测（' + err + '）'))]
    filt = next((e for e in events if e['op'] == 'Filter'), {})
    proj = next((e for e in events if e['op'] == 'Project'), {})
    scan = next((e for e in events if e['op'] == 'Scan'), {})
    if scenario == 'empty':
        passed = filt.get('emitted') == 0 and proj.get('emitted') == 0
        want = '空结果：Filter/Project 交出 0 行'
    elif scenario == 'boundary':
        ids = [r['id'] for r in filt.get('rows', [])]
        passed = ids == [1, 5]
        want = '边界 qty>=2 包含恰好 2：ids [1,5]'
    else:
        cols = set(proj.get('rows', [{}])[0].keys()) if proj.get('rows') else set()
        passed = cols == {'id', 'qty'}
        want = 'Project 只输出 id/qty 列'
    return [case('查询执行 · ' + scenario, passed, expected=want,
                 actual=f"Scan {scan.get('emitted')} → Filter {filt.get('emitted')} → Project {proj.get('emitted')}",
                 error=None if passed else '算子输入输出与场景预期不符。',
                 steps=_steps(events, 'rows'), extra=dict(module='exec'))]


def _cpp_locks(lesson, code, run_dir, choice='', scenario=''):
    events, err = _run_binary('locks', scenario, '', run_dir)
    if err:
        return [case('并发控制 · ' + scenario, None, error=None, steps=[],
                     extra=dict(not_measured='示意 / 未实测（' + err + '）'))]
    ops = [e['op'] for e in events]
    end = next((e for e in events if e['op'] == 'end'), {})
    if scenario == 'conflict':
        passed = 'wait' in ops and ops.count('grant') >= 2 and end.get('locks') == []
        want = '读写冲突等待 → 提交释放 → 等待者获锁 → 结束锁表空'
    elif scenario == 'rollback':
        passed = 'wait' in ops and 'rollback' in ops and ops.count('grant') >= 2 and end.get('locks') == []
        want = '回滚释放锁 → 等待者获锁 → 无残留'
    else:
        passed = 'wait' not in ops and ops.count('grant') == 2 and end.get('locks') == []
        want = '读读兼容：两笔都获锁，无等待'
    return [case('并发控制 · ' + scenario, passed, expected=want, actual='ops=' + ','.join(ops),
                 error=None if passed else '锁管理器行为与场景预期不符。',
                 steps=_steps(events, 'locks'), extra=dict(module='locks'))]


ENGINES.update({
    'cpp_page': _cpp_page,
    'cpp_buffer': _cpp_buffer,
    'cpp_btree': _cpp_btree,
    'cpp_exec': _cpp_exec,
    'cpp_locks': _cpp_locks,
})
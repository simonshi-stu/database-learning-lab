"""运行编排：每次运行独立临时数据库；子进程超时强杀；酒店 C 场景两段式。"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from .problems import sales, hotel
from .trace import read_events

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / 'runs'
MODULES = {'sales-last-unit': sales, 'hotel-two-nights': hotel}
PHASE_TIMEOUT = 25


def _spawn(run_dir, payload):
    (run_dir / 'payload.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    env = dict(os.environ)
    tmp = str(ROOT / 'tmp')
    env.update({'TEMP': tmp, 'TMP': tmp, 'TMPDIR': tmp, 'PYTHONDONTWRITEBYTECODE': '1'})
    proc = subprocess.Popen(
        [sys.executable, '-m', 'lab.worker', str(run_dir)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = proc.communicate(timeout=PHASE_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return {'timeout': True, 'exit_code': None}
    return {'timeout': False, 'exit_code': proc.returncode,
            'stderr': err.decode('utf-8', 'replace')[-2000:]}


def run_problem(problem_id, mode, code, scenario):
    if problem_id not in MODULES:
        raise ValueError('unknown problem')
    module = MODULES[problem_id]
    if not scenario:
        scenario = module.META['scenarios'][0]['id']
    run_id = uuid.uuid4().hex[:12]
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {'problem': problem_id, 'mode': mode, 'code': code, 'scenario': scenario}

    phase1 = _spawn(run_dir, dict(payload, phase=1))
    phases = [phase1]
    if phase1['timeout']:
        return _finish(run_id, problem_id, scenario, phases, error='运行超时（25 秒），子进程已终止。检查是否有死循环。')
    if problem_id == 'hotel-two-nights' and phase1['exit_code'] == 73:
        phase2 = _spawn(run_dir, dict(payload, phase=2))
        phases.append(phase2)
        if phase2['timeout']:
            return _finish(run_id, problem_id, scenario, phases, error='C 场景重试阶段超时。')
    elif problem_id == 'hotel-two-nights' and phase1['exit_code'] != 0:
        return _finish(run_id, problem_id, scenario, phases,
                       error='运行失败（未到达预期的提交后退出点）。查看轨迹末尾的错误事件。')
    elif phase1['exit_code'] != 0:
        return _finish(run_id, problem_id, scenario, phases, error='运行失败，查看轨迹末尾的错误事件。')
    return _finish(run_id, problem_id, scenario, phases)


def _finish(run_id, problem_id, scenario, phases, error=None):
    run_dir = RUNS / run_id
    events, snapshots = read_events(run_dir / 'events.jsonl')
    if phases[-1].get('exit_code') == 73 or any(p.get('exit_code') == 73 for p in phases):
        events.append({'type': 'event', 'seq': (events[-1]['seq'] if events else 0) + 1,
                       'ms': None, 'kind': 'process_exit_observed', 'actor': 'parent',
                       'label': '父进程真实观测：子进程退出码 73（提交后退出，响应未送达）',
                       'exit_code': 73})
    responses = [{'actor': e['actor'], 'status': e['status'], 'body': e['body']}
                 for e in events if e['kind'] == 'http_response']
    result_file = run_dir / 'result.json'
    worker_result = json.loads(result_file.read_text(encoding='utf-8')) if result_file.exists() else {}
    judge = None
    if error is None and snapshots and not worker_result.get('error'):
        judge = MODULES[problem_id].judge(scenario, events, snapshots, responses)
    elif worker_result.get('error'):
        error = worker_result['error']
    full = {'run_id': run_id, 'problem': problem_id, 'scenario': scenario,
            'events': events, 'snapshots': snapshots, 'responses': responses,
            'judge': judge, 'error': error,
            'phases': [{'exit_code': p.get('exit_code'), 'timeout': p.get('timeout')} for p in phases]}
    (run_dir / 'result_full.json').write_text(json.dumps(full, ensure_ascii=False), encoding='utf-8')
    return full


def load_run(run_id):
    path = RUNS / run_id / 'result_full.json'
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def reset_runs():
    import shutil
    count = 0
    if RUNS.exists():
        for child in RUNS.iterdir():
            if child.is_dir() and (child / 'payload.json').exists():
                shutil.rmtree(child)
                count += 1
    return count
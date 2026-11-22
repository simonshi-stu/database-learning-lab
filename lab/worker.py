"""独立子进程入口：用户代码只在这里执行，不在 Web 服务进程内。

用法：python -m lab.worker <run_dir>
run_dir 内应有 payload.json；轨迹追加写入 events.jsonl（崩溃也不丢已提交部分）。
"""
import json
import sys
import traceback
from pathlib import Path


def main():
    run_dir = Path(sys.argv[1])
    payload = json.loads((run_dir / 'payload.json').read_text(encoding='utf-8'))
    sys.dont_write_bytecode = True
    from .trace import Recorder
    from .problems import sales, hotel
    module = {'sales-last-unit': sales, 'hotel-two-nights': hotel}[payload['problem']]
    rec = Recorder(run_dir / 'events.jsonl')
    ctx = {'rec': rec, 'db_path': run_dir / 'lab.db', 'mode': payload['mode'],
           'code': payload['code'], 'scenario': payload['scenario'],
           'phase': payload.get('phase', 1)}
    try:
        result = module.execute(ctx)
    except BaseException as exc:
        rec.emit('runner_error', 'system', f'执行失败：{type(exc).__name__}: {exc}',
                 traceback=traceback.format_exc(limit=6))
        rec.close()
        (run_dir / 'result.json').write_text(
            json.dumps({'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False), encoding='utf-8')
        sys.exit(1)
    rec.close()
    (run_dir / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
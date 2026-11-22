"""云路线后台消费者（独立进程）：读 outbox → 幂等写结果 + 原始事件 → 确认。

可控下游：fail_first_n 次失败；max_attempts 上限；backoff_ms 退避（毫秒，真实 sleep）。
次数用尽写永久失败记录。事件 JSON 行含 request_id/event_id/pid/attempt。
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lab import pglab


def emit(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def main():
    p = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    schema = p['schema']
    lake = Path(p['lake_dir'])
    lake.mkdir(parents=True, exist_ok=True)
    c = pglab.connect(schema, autocommit=True)
    c.execute("SET statement_timeout='15s'")
    emit(op='worker_start', pid=os.getpid(), schema=schema)
    fail_left = p.get('fail_first_n', 0)
    max_attempts = p.get('max_attempts', 4)
    backoff_ms = p.get('backoff_ms', 50)
    rows = c.execute('SELECT event_id, request_key, kind, payload FROM outbox WHERE published=false ORDER BY event_id').fetchall()
    for event_id, request_key, kind, payload in rows:
        data = json.loads(payload)
        emit(op='deliver', event_id=event_id, request_key=request_key, kind=kind, pid=os.getpid())
        succeeded = False
        for attempt in range(1, max_attempts + 1):
            if fail_left > 0:
                fail_left -= 1
                c.execute('INSERT INTO failures(event_id, reason, attempts) VALUES(%s,%s,%s)',
                          (str(event_id), 'downstream_unavailable', attempt))
                emit(op='attempt_fail', event_id=event_id, request_key=request_key, attempt=attempt,
                     reason='downstream_unavailable', backoff_ms=backoff_ms * attempt)
                if attempt < max_attempts:
                    time.sleep(backoff_ms * attempt / 1000)
                    emit(op='backoff_wait', event_id=event_id, attempt=attempt, ms=backoff_ms * attempt)
                continue
            cur = c.execute(
                'INSERT INTO consumer_results(event_id, request_key, net_cents, room_nights, biz_date) '
                'VALUES(%s,%s,%s,%s,%s) ON CONFLICT (event_id) DO NOTHING',
                (str(event_id), request_key, data.get('net_cents', 0), data.get('room_nights', 0), data.get('biz_date', '')))
            wrote = cur.rowcount
            raw = lake / 'raw' / f'event_{event_id}.json'
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text(json.dumps(dict(event_id=event_id, request_key=request_key, kind=kind, **data), ensure_ascii=False), encoding='utf-8')
            emit(op='consume', event_id=event_id, request_key=request_key, wrote_result=wrote, raw_file=str(raw), attempt=attempt)
            succeeded = True
            break
        if not succeeded:
            c.execute('INSERT INTO failures(event_id, reason, attempts) VALUES(%s,%s,%s)',
                      (str(event_id), 'exhausted_permanent_failure', max_attempts))
            emit(op='exhausted', event_id=event_id, request_key=request_key, max_attempts=max_attempts)
            continue
        if p.get('stop_before_ack'):
            emit(op='exit_before_ack', event_id=event_id, request_key=request_key, pid=os.getpid())
            os._exit(73)
        c.execute('UPDATE outbox SET published=true WHERE event_id=%s', (event_id,))
        emit(op='ack', event_id=event_id, request_key=request_key)
    emit(op='worker_end', pid=os.getpid())


if __name__ == '__main__':
    main()
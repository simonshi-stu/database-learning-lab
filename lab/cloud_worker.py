"""云路线后台消费者（独立进程）：读 outbox → 写业务结果 + 原始事件 → 确认。

用法：python -m lab.cloud_worker <payload.json>
payload: {schema, lake_dir, stop_before_ack, fail_first_n, mode}
事件以 JSON 行打印到 stdout（含 request_id / event_id / pid）。
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
    emit(op='worker_start', pid=os.getpid(), schema=schema)
    fail_left = p.get('fail_first_n', 0)
    rows = c.execute('SELECT event_id, request_key, kind, payload FROM outbox WHERE published=false ORDER BY event_id').fetchall()
    for event_id, request_key, kind, payload in rows:
        data = json.loads(payload)
        emit(op='deliver', event_id=event_id, request_key=request_key, kind=kind, pid=os.getpid())
        if fail_left > 0:
            fail_left -= 1
            c.execute('INSERT INTO failures(event_id, reason, attempts) VALUES(%s,%s,1)',
                      (str(event_id), 'downstream_unavailable'))
            emit(op='downstream_fail', event_id=event_id, request_key=request_key, reason='downstream_unavailable')
            continue
        # 幂等消费：唯一键 event_id 保证业务结果只写一次（消息本身可重复）
        cur = c.execute(
            'INSERT INTO consumer_results(event_id, request_key, net_cents, room_nights, biz_date) '
            'VALUES(%s,%s,%s,%s,%s) ON CONFLICT (event_id) DO NOTHING',
            (str(event_id), request_key, data.get('net_cents', 0), data.get('room_nights', 0), data.get('biz_date', '')))
        wrote = cur.rowcount
        raw = lake / 'raw' / f'event_{event_id}.json'
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(json.dumps(dict(event_id=event_id, request_key=request_key, kind=kind, **data), ensure_ascii=False), encoding='utf-8')
        emit(op='consume', event_id=event_id, request_key=request_key, wrote_result=wrote, raw_file=str(raw))
        if p.get('stop_before_ack'):
            emit(op='exit_before_ack', event_id=event_id, request_key=request_key, pid=os.getpid())
            os._exit(73)
        c.execute('UPDATE outbox SET published=true WHERE event_id=%s', (event_id,))
        emit(op='ack', event_id=event_id, request_key=request_key)
    emit(op='worker_end', pid=os.getpid())


if __name__ == '__main__':
    main()
"""批次 3 子进程：酒店式预订写入，在提交前或提交后 os._exit(73)。

用法：python -m lab.course_exit_child <payload.json>
payload: {db, code, exit_at: 'before'|'after'}；事件以 JSON 行打印到 stdout。
"""
import json
import os
import sqlite3
import sys
import time


def main():
    payload = json.loads(open(sys.argv[1], encoding='utf-8').read())
    t0 = time.monotonic()

    def emit(op, **kw):
        print(json.dumps(dict(op=op, ms=round((time.monotonic() - t0) * 1000), **kw), ensure_ascii=False), flush=True)

    c = sqlite3.connect(payload['db'], isolation_level=None)
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('BEGIN')
    emit('BEGIN IMMEDIATE')
    c.execute("INSERT INTO bookings VALUES('r1', 'BOOKED')")
    emit('INSERT bookings r1')
    c.execute(payload['code'])
    emit('INSERT booking_nights（学员语句）')
    if payload['exit_at'] == 'before':
        emit('EXIT_BEFORE_COMMIT', note='提交前退出：事务未提交')
        os._exit(73)
    c.execute('COMMIT')
    emit('COMMIT')
    emit('EXIT_AFTER_COMMIT', note='提交后退出：数据已持久化')
    os._exit(73)


if __name__ == '__main__':
    main()
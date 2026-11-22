"""酒店题：连续两晚必须同一间房，离店日不占库存。

来源：hotel-reservation-lab @ 7151ff8ca479b2887706ce3f2186611a06454c1b
  hotel_sim/sqlite_store.py reserve (L223-254)、hotel_sim/sqlite_schema.py、
  hotel_demo/server.py TracedHotel (L39-62)、scripts/audit_sqlite_crashes.py ExitAt (L7-14)
教学简化：2 间 standard 房、无升级/床型/价格/保护房；分配规则简化为
“所有晚都空闲且未封锁的同一间房”；婴儿床/加床需求用 auxiliary_needs 简化计算；
保留半开区间、REJECTED 持久化、关键触发器、_checkpoint 式故障注入与提交后子进程退出。
"""
import json
import os
import sqlite3

from ..harness import call_user, validate_sql
from ..sandbox import (BusinessError, compile_solution, canonical, digest_of,
                       nights_of, auxiliary_needs, JsonShim)
from ..trace import TracedDatabase, connect_raw, dump_tables

TABLES = ['room_calendar', 'auxiliary_inventory', 'bookings',
          'booking_nights', 'active_room_nights', 'reservation_events']

SCHEMA = """
CREATE TABLE room_calendar(room_id TEXT NOT NULL, night TEXT NOT NULL,
    room_type TEXT NOT NULL, blocked INTEGER NOT NULL CHECK(blocked IN (0,1)),
    PRIMARY KEY(room_id,night));
CREATE TABLE auxiliary_inventory(night TEXT PRIMARY KEY,
    cot_capacity INTEGER NOT NULL, extra_capacity INTEGER NOT NULL,
    cots_used INTEGER NOT NULL DEFAULT 0 CHECK(cots_used BETWEEN 0 AND cot_capacity),
    extras_used INTEGER NOT NULL DEFAULT 0 CHECK(extras_used BETWEEN 0 AND extra_capacity));
CREATE TABLE bookings(request_id TEXT PRIMARY KEY, request_json TEXT NOT NULL,
    request_sha256 TEXT NOT NULL, decision_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('BOOKED','CANCELLED','REJECTED')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    cancelled_at TEXT);
CREATE TABLE booking_nights(request_id TEXT NOT NULL REFERENCES bookings(request_id),
    room_id TEXT NOT NULL, night TEXT NOT NULL,
    cots INTEGER NOT NULL, extra_beds INTEGER NOT NULL,
    PRIMARY KEY(request_id,night), UNIQUE(request_id,room_id,night),
    FOREIGN KEY(room_id,night) REFERENCES room_calendar(room_id,night));
CREATE TABLE active_room_nights(room_id TEXT NOT NULL, night TEXT NOT NULL, request_id TEXT NOT NULL,
    PRIMARY KEY(room_id,night), UNIQUE(request_id,night),
    FOREIGN KEY(request_id,room_id,night) REFERENCES booking_nights(request_id,room_id,night));
CREATE TABLE reservation_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES bookings(request_id),
    event_type TEXT NOT NULL CHECK(event_type IN ('BOOKED','CANCELLED','REJECTED')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(request_id,event_type));
CREATE TRIGGER active_room_insert_guard BEFORE INSERT ON active_room_nights
 WHEN (SELECT status FROM bookings WHERE request_id=NEW.request_id)<>'BOOKED'
 OR (SELECT blocked FROM room_calendar WHERE room_id=NEW.room_id AND night=NEW.night)<>0
 BEGIN SELECT RAISE(ABORT,'room night is not bookable'); END;
CREATE TRIGGER history_insert_guard BEFORE INSERT ON booking_nights
 WHEN (SELECT status FROM bookings WHERE request_id=NEW.request_id)<>'BOOKED'
 BEGIN SELECT RAISE(ABORT,'only booked requests may receive history'); END;
CREATE TRIGGER auxiliary_capacity_guard BEFORE UPDATE ON auxiliary_inventory
 WHEN NEW.night<>OLD.night OR NEW.cot_capacity<>OLD.cot_capacity OR NEW.extra_capacity<>OLD.extra_capacity
 BEGIN SELECT RAISE(ABORT,'inventory configuration is immutable'); END;
CREATE TRIGGER booking_nights_update_guard BEFORE UPDATE ON booking_nights
 BEGIN SELECT RAISE(ABORT,'immutable history'); END;
CREATE TRIGGER booking_nights_delete_guard BEFORE DELETE ON booking_nights
 BEGIN SELECT RAISE(ABORT,'immutable history'); END;
CREATE TRIGGER reservation_events_update_guard BEFORE UPDATE ON reservation_events
 BEGIN SELECT RAISE(ABORT,'immutable history'); END;
CREATE TRIGGER reservation_events_delete_guard BEFORE DELETE ON reservation_events
 BEGIN SELECT RAISE(ABORT,'immutable history'); END;
CREATE TRIGGER bookings_delete_guard BEFORE DELETE ON bookings
 BEGIN SELECT RAISE(ABORT,'booking history is retained'); END;
"""

NIGHTS = ['2026-12-07', '2026-12-08', '2026-12-09', '2026-12-10', '2026-12-11', '2026-12-12']
AUX = {'2026-12-07': (2, 3), '2026-12-08': (2, 3), '2026-12-09': (2, 3),
       '2026-12-10': (1, 3), '2026-12-11': (1, 2), '2026-12-12': (2, 3)}

R_OK = {'request_id': 'R-ok', 'check_in': '2026-12-07', 'check_out': '2026-12-09',
        'adults': 2, 'child_ages': []}
R_REJ = {'request_id': 'R-rej', 'check_in': '2026-12-11', 'check_out': '2026-12-13',
         'adults': 2, 'child_ages': []}
R_ROLL = {'request_id': 'R-roll', 'check_in': '2026-12-09', 'check_out': '2026-12-11',
          'adults': 2, 'child_ages': [1]}
R_CRASH = {'request_id': 'R-crash', 'check_in': '2026-12-09', 'check_out': '2026-12-11',
           'adults': 2, 'child_ages': []}
R_AUX = {'request_id': 'R-aux', 'check_in': '2026-12-10', 'check_out': '2026-12-11',
         'adults': 2, 'child_ages': [1, 1]}

STARTER = '''# 起始代码：补全“两晚同一间房”的预订逻辑。
# 可用 API：
#   with db.transaction() as c:       BEGIN IMMEDIATE 写事务
#   c.execute(sql, params)            写看 .rowcount，读用 .fetchone()/.fetchall()
#   fail(status, code, message)       业务错误
#   nights_of(request)                半开区间 [入住, 离店) 的晚列表（离店日不占房）
#   auxiliary_needs(request)          (婴儿床数, 加床数)
#   digest_of(obj) / json.dumps/loads 内容摘要 / JSON
with db.transaction() as c:
    # TODO 1: 按 request_id 查旧预订；同键同内容直接返回原记录（replayed=True）
    # TODO 2: 求“每一晚都可用”的房间交集；交集为空 → 写入 REJECTED 预订 + REJECTED 事件后返回
    # TODO 3: 有房 → 写 BOOKED 预订，逐晚条件更新辅助库存、写 booking_nights 与 active_room_nights
    # TODO 4: 写 BOOKED 事件并返回决定
    return {'status': 'TODO', 'request_id': request['request_id'], 'replayed': False}
'''

REFERENCE = '''# 参考解：改编自 hotel_sim/sqlite_store.py reserve（简化分配与库存）
nights = nights_of(request)
cots, extras = auxiliary_needs(request)
ident = digest_of(request)
doc = json.dumps(request)
with db.transaction() as c:
    old = c.execute("SELECT * FROM bookings WHERE request_id=?",
                    (request['request_id'],)).fetchone()
    if old:
        if old['request_sha256'] != ident:
            fail(409, 'IDEMPOTENCY_CONFLICT', '同一请求键不能用于不同内容')
        return {'status': old['status'], 'request_id': old['request_id'],
                'decision': json.loads(old['decision_json']), 'replayed': True}
    # 两晚（多晚）必须是同一间房：求每晚可用房间的交集
    candidates = None
    for night in nights:
        rows = c.execute(
            "SELECT room_id FROM room_calendar WHERE night=? AND blocked=0 "
            "AND room_id NOT IN (SELECT room_id FROM active_room_nights WHERE night=?)",
            (night, night)).fetchall()
        ids = {r['room_id'] for r in rows}
        candidates = ids if candidates is None else candidates & ids
    if not candidates:
        decision = {'accepted': False, 'reason': 'NO_SAME_ROOM_FOR_ALL_NIGHTS'}
        c.execute("INSERT INTO bookings(request_id,request_json,request_sha256,decision_json,status) "
                  "VALUES(?,?,?,?,'REJECTED')",
                  (request['request_id'], doc, ident, json.dumps(decision)))
        c.execute("INSERT INTO reservation_events(request_id,event_type) VALUES(?,'REJECTED')",
                  (request['request_id'],))
        return {'status': 'REJECTED', 'request_id': request['request_id'],
                'decision': decision, 'replayed': False}
    room = sorted(candidates)[0]
    decision = {'accepted': True, 'room_id': room, 'nights': nights,
                'cots': cots, 'extra_beds': extras}
    c.execute("INSERT INTO bookings(request_id,request_json,request_sha256,decision_json,status) "
              "VALUES(?,?,?,?,'BOOKED')",
              (request['request_id'], doc, ident, json.dumps(decision)))
    for night in nights:
        cur = c.execute(
            "UPDATE auxiliary_inventory SET cots_used=cots_used+:cots, extras_used=extras_used+:extras "
            "WHERE night=:night AND cots_used+:cots<=cot_capacity AND extras_used+:extras<=extra_capacity",
            {'cots': cots, 'extras': extras, 'night': night})
        if cur.rowcount != 1:
            fail(409, 'AUXILIARY_UNAVAILABLE', '婴儿床或加床库存不足')
        c.execute("INSERT INTO booking_nights VALUES(?,?,?,?,?)",
                  (request['request_id'], room, night, cots, extras))
        c.execute("INSERT INTO active_room_nights VALUES(?,?,?)",
                  (room, night, request['request_id']))
    c.execute("INSERT INTO reservation_events(request_id,event_type) VALUES(?,'BOOKED')",
              (request['request_id'],))
    return {'status': 'BOOKED', 'request_id': request['request_id'],
            'decision': decision, 'replayed': False}
'''

WRONG_PER_NIGHT = '''# 错误解：逐晚各自挑第一间可用房，不保证两晚是同一间房。
# 场景里 12-08 的 S101 已被占用：第一晚会挑 S101，第二晚只能挑 S102。
nights = nights_of(request)
cots, extras = auxiliary_needs(request)
ident = digest_of(request)
doc = json.dumps(request)
with db.transaction() as c:
    old = c.execute("SELECT * FROM bookings WHERE request_id=?",
                    (request['request_id'],)).fetchone()
    if old:
        if old['request_sha256'] != ident:
            fail(409, 'IDEMPOTENCY_CONFLICT', '同一请求键不能用于不同内容')
        return {'status': old['status'], 'request_id': old['request_id'],
                'decision': json.loads(old['decision_json']), 'replayed': True}
    picks = []
    for night in nights:
        rows = c.execute(
            "SELECT room_id FROM room_calendar WHERE night=? AND blocked=0 "
            "AND room_id NOT IN (SELECT room_id FROM active_room_nights WHERE night=?) "
            "ORDER BY room_id", (night, night)).fetchall()
        if not rows:
            picks = []
            break
        picks.append(rows[0]['room_id'])
    if not picks:
        decision = {'accepted': False, 'reason': 'NO_ROOM'}
        c.execute("INSERT INTO bookings(request_id,request_json,request_sha256,decision_json,status) "
                  "VALUES(?,?,?,?,'REJECTED')",
                  (request['request_id'], doc, ident, json.dumps(decision)))
        c.execute("INSERT INTO reservation_events(request_id,event_type) VALUES(?,'REJECTED')",
                  (request['request_id'],))
        return {'status': 'REJECTED', 'request_id': request['request_id'],
                'decision': decision, 'replayed': False}
    decision = {'accepted': True, 'room_id': picks[0], 'nights': nights,
                'cots': cots, 'extra_beds': extras}
    c.execute("INSERT INTO bookings(request_id,request_json,request_sha256,decision_json,status) "
              "VALUES(?,?,?,?,'BOOKED')",
              (request['request_id'], doc, ident, json.dumps(decision)))
    for night, room in zip(nights, picks):
        cur = c.execute(
            "UPDATE auxiliary_inventory SET cots_used=cots_used+:cots, extras_used=extras_used+:extras "
            "WHERE night=:night AND cots_used+:cots<=cot_capacity AND extras_used+:extras<=extra_capacity",
            {'cots': cots, 'extras': extras, 'night': night})
        if cur.rowcount != 1:
            fail(409, 'AUXILIARY_UNAVAILABLE', '婴儿床或加床库存不足')
        c.execute("INSERT INTO booking_nights VALUES(?,?,?,?,?)",
                  (request['request_id'], room, night, cots, extras))
        c.execute("INSERT INTO active_room_nights VALUES(?,?,?)",
                  (room, night, request['request_id']))
    c.execute("INSERT INTO reservation_events(request_id,event_type) VALUES(?,'BOOKED')",
              (request['request_id'],))
    return {'status': 'BOOKED', 'request_id': request['request_id'],
            'decision': decision, 'replayed': False}
'''

WRONG_NO_REJECT_RECORD = '''# 错误解：拒订只返回一个字符串，不写 REJECTED 预订和事件。
# 看起来“拒订成功”，但数据库里没有任何记录，重试也找不回这次拒订。
nights = nights_of(request)
cots, extras = auxiliary_needs(request)
ident = digest_of(request)
doc = json.dumps(request)
with db.transaction() as c:
    old = c.execute("SELECT * FROM bookings WHERE request_id=?",
                    (request['request_id'],)).fetchone()
    if old:
        if old['request_sha256'] != ident:
            fail(409, 'IDEMPOTENCY_CONFLICT', '同一请求键不能用于不同内容')
        return {'status': old['status'], 'request_id': old['request_id'],
                'decision': json.loads(old['decision_json']), 'replayed': True}
    candidates = None
    for night in nights:
        rows = c.execute(
            "SELECT room_id FROM room_calendar WHERE night=? AND blocked=0 "
            "AND room_id NOT IN (SELECT room_id FROM active_room_nights WHERE night=?)",
            (night, night)).fetchall()
        ids = {r['room_id'] for r in rows}
        candidates = ids if candidates is None else candidates & ids
    if not candidates:
        # 错误：直接返回，不落库
        return {'status': 'REJECTED', 'request_id': request['request_id'], 'replayed': False}
    room = sorted(candidates)[0]
    decision = {'accepted': True, 'room_id': room, 'nights': nights,
                'cots': cots, 'extra_beds': extras}
    c.execute("INSERT INTO bookings(request_id,request_json,request_sha256,decision_json,status) "
              "VALUES(?,?,?,?,'BOOKED')",
              (request['request_id'], doc, ident, json.dumps(decision)))
    for night in nights:
        cur = c.execute(
            "UPDATE auxiliary_inventory SET cots_used=cots_used+:cots, extras_used=extras_used+:extras "
            "WHERE night=:night AND cots_used+:cots<=cot_capacity AND extras_used+:extras<=extra_capacity",
            {'cots': cots, 'extras': extras, 'night': night})
        if cur.rowcount != 1:
            fail(409, 'AUXILIARY_UNAVAILABLE', '婴儿床或加床库存不足')
        c.execute("INSERT INTO booking_nights VALUES(?,?,?,?,?)",
                  (request['request_id'], room, night, cots, extras))
        c.execute("INSERT INTO active_room_nights VALUES(?,?,?)",
                  (room, night, request['request_id']))
    c.execute("INSERT INTO reservation_events(request_id,event_type) VALUES(?,'BOOKED')",
              (request['request_id'],))
    return {'status': 'BOOKED', 'request_id': request['request_id'],
            'decision': decision, 'replayed': False}
'''

SQL_REFERENCE = ("UPDATE auxiliary_inventory SET cots_used=cots_used+:cots, extras_used=extras_used+:extras "
                 "WHERE night=:night AND cots_used+:cots<=cot_capacity AND extras_used+:extras<=extra_capacity")
SQL_WRONG = ("UPDATE auxiliary_inventory SET cots_used=cots_used+:cots, extras_used=extras_used+:extras "
             "WHERE night=:night")


def _sql_solution(update_sql):
    def fn(db, request):
        nights = nights_of(request)
        cots, extras = auxiliary_needs(request)
        ident = digest_of(request)
        doc = canonical(request)
        with db.transaction() as c:
            old = c.execute("SELECT * FROM bookings WHERE request_id=?",
                            (request['request_id'],)).fetchone()
            if old:
                if old['request_sha256'] != ident:
                    raise BusinessError(409, 'IDEMPOTENCY_CONFLICT', '同一请求键不能用于不同内容')
                return {'status': old['status'], 'request_id': old['request_id'],
                        'decision': json.loads(old['decision_json']), 'replayed': True}
            candidates = None
            for night in nights:
                rows = c.execute(
                    "SELECT room_id FROM room_calendar WHERE night=? AND blocked=0 "
                    "AND room_id NOT IN (SELECT room_id FROM active_room_nights WHERE night=?)",
                    (night, night)).fetchall()
                ids = {r['room_id'] for r in rows}
                candidates = ids if candidates is None else candidates & ids
            if not candidates:
                decision = {'accepted': False, 'reason': 'NO_SAME_ROOM_FOR_ALL_NIGHTS'}
                c.execute("INSERT INTO bookings(request_id,request_json,request_sha256,decision_json,status) "
                          "VALUES(?,?,?,?,'REJECTED')",
                          (request['request_id'], doc, ident, json.dumps(decision)))
                c.execute("INSERT INTO reservation_events(request_id,event_type) VALUES(?,'REJECTED')",
                          (request['request_id'],))
                return {'status': 'REJECTED', 'request_id': request['request_id'],
                        'decision': decision, 'replayed': False}
            room = sorted(candidates)[0]
            decision = {'accepted': True, 'room_id': room, 'nights': nights,
                        'cots': cots, 'extra_beds': extras}
            c.execute("INSERT INTO bookings(request_id,request_json,request_sha256,decision_json,status) "
                      "VALUES(?,?,?,?,'BOOKED')",
                      (request['request_id'], doc, ident, json.dumps(decision)))
            for night in nights:
                cur = c.execute(update_sql, {'cots': cots, 'extras': extras, 'night': night})
                if cur.rowcount != 1:
                    raise BusinessError(409, 'AUXILIARY_UNAVAILABLE', '婴儿床或加床库存不足')
                c.execute("INSERT INTO booking_nights VALUES(?,?,?,?,?)",
                          (request['request_id'], room, night, cots, extras))
                c.execute("INSERT INTO active_room_nights VALUES(?,?,?)",
                          (room, night, request['request_id']))
            c.execute("INSERT INTO reservation_events(request_id,event_type) VALUES(?,'BOOKED')",
                      (request['request_id'],))
            return {'status': 'BOOKED', 'request_id': request['request_id'],
                    'decision': decision, 'replayed': False}
    return fn


def validate(request):
    rid = request.get('request_id')
    if not isinstance(rid, str) or not 1 <= len(rid) <= 80:
        raise BusinessError(422, 'VALIDATION_ERROR', 'request_id 必须是 1-80 字符的字符串')
    try:
        nights_of(request)
    except (KeyError, ValueError, TypeError):
        raise BusinessError(422, 'VALIDATION_ERROR', '入住/离店日期格式非法') from None
    if type(request.get('adults')) is not int or not 1 <= request['adults'] <= 4:
        raise BusinessError(422, 'VALIDATION_ERROR', 'adults 必须是 1-4 的整数')
    ages = request.get('child_ages')
    if not isinstance(ages, list) or len(ages) > 3 or any(type(a) is not int or not 0 <= a <= 17 for a in ages):
        raise BusinessError(422, 'VALIDATION_ERROR', 'child_ages 必须是 0-17 的整数列表（最多 3 个）')
    request['child_ages'] = sorted(ages)


def display(body, status):
    if status == 200:
        st = body.get('status')
        if st == 'BOOKED':
            d = body.get('decision', {})
            tag = '（幂等回放，未重复占房）' if body.get('replayed') else ''
            return f"预订成功：{d.get('room_id')} 房，{len(d.get('nights', []))} 晚{tag}"
        if st == 'REJECTED':
            tag = '（幂等回放，未新增记录）' if body.get('replayed') else ''
            return f'拒订已记录，未占房{tag}'
        if st == 'ROLLED_BACK':
            return '已回滚：事务里曾写入第一晚，但所有临时写入已被撤销'
        return f'结果：{st}'
    code = body.get('error', {}).get('code', '')
    return {
        'IDEMPOTENCY_CONFLICT': '同一请求键对应不同内容，已拒绝',
        'AUXILIARY_UNAVAILABLE': '婴儿床或加床库存不足',
        'CONSTRAINT_CONFLICT': '约束冲突：数据库触发器/约束拦截了非法写入',
        'DATABASE_BUSY': '数据库繁忙：请用原请求键重试',
    }.get(code, f"请求失败：{body.get('error', {}).get('message', status)}")


def _seed(db_path, rec):
    raw = connect_raw(db_path)
    raw.executescript(SCHEMA)
    for room in ('S101', 'S102'):
        for night in NIGHTS:
            blocked = 1 if night == '2026-12-12' else 0
            raw.execute("INSERT INTO room_calendar VALUES(?,?, 'standard', ?)", (room, night, blocked))
    for night, (cots, extra) in AUX.items():
        raw.execute("INSERT INTO auxiliary_inventory(night,cot_capacity,extra_capacity) VALUES(?,?,?)",
                    (night, cots, extra))
    pre = {'request_id': 'PRE-1', 'check_in': '2026-12-08', 'check_out': '2026-12-09',
           'adults': 2, 'child_ages': []}
    decision = {'accepted': True, 'room_id': 'S101', 'nights': ['2026-12-08'], 'cots': 0, 'extra_beds': 0}
    raw.execute("INSERT INTO bookings(request_id,request_json,request_sha256,decision_json,status) "
                "VALUES(?,?,?,?,'BOOKED')",
                ('PRE-1', canonical(pre), digest_of(pre), json.dumps(decision)))
    raw.execute("INSERT INTO booking_nights VALUES('PRE-1','S101','2026-12-08',0,0)")
    raw.execute("INSERT INTO active_room_nights VALUES('S101','2026-12-08','PRE-1')")
    raw.execute("INSERT INTO reservation_events(request_id,event_type) VALUES('PRE-1','BOOKED')")
    rec.snapshot(raw, TABLES, 'system', 'initial', '初始数据（S101 在 12-08 已被 PRE-1 占用；12-12 两间均封锁）')
    raw.close()


def _marker(db_path, rec, label):
    raw = connect_raw(db_path)
    rec.snapshot(raw, TABLES, 'system', 'marker', label)
    raw.close()


def _make_fn(ctx):
    rec = ctx['rec']
    helpers = {'fail': _fail, 'digest_of': digest_of, 'canonical': canonical,
               'nights_of': nights_of, 'auxiliary_needs': auxiliary_needs, 'json': JsonShim}
    if ctx['mode'] == 'sql':
        update_sql = validate_sql(ctx['code'], 'auxiliary_inventory', ['cots', 'extras', 'night'])
        return _sql_solution(update_sql)
    return compile_solution(ctx['code'], helpers,
                            lambda *a: rec.emit('stdout', 'learner', ' '.join(str(x) for x in a)))


def _fail(status, code, message):
    raise BusinessError(status, code, message)


def execute(ctx):
    rec, db_path = ctx['rec'], ctx['db_path']
    phase = ctx.get('phase', 1)
    if phase == 1:
        _seed(db_path, rec)
        fn = _make_fn(ctx)

        def call(actor, request, fault=None, crash=False):
            db = TracedDatabase(db_path, rec, actor, TABLES, fault=fault)
            return call_user(rec, actor, request, fn, db, validate, display,
                             crash_after_commit=crash)

        rec.emit('note', 'system', '场景顺序：成功对照 → A 拒订+重试 → B 注入异常回滚 → C 提交后进程退出')
        call('ok', R_OK)

        _marker(db_path, rec, 'A 场景前')
        call('rej', R_REJ)
        rec.emit('note', 'rej-retry', '同请求键、同内容重试（应找回拒订记录，不新增）')
        call('rej-retry', R_REJ)
        _marker(db_path, rec, 'A 重试后')

        _marker(db_path, rec, 'B 场景前')
        fired = []

        def fault(sql, params):
            if sql.strip().upper().startswith('INSERT INTO BOOKING_NIGHTS') and not fired:
                fired.append(True)
                return True
            return False

        call('roll', R_ROLL, fault=fault)
        _marker(db_path, rec, 'B 回滚后')

        rec.emit('note', 'crash', 'C 场景：本来可订的两晚；提交成功后子进程立即退出，客户端收不到响应')
        call('crash', R_CRASH, crash=True)
        return {}
    else:
        fn = _make_fn(ctx)
        rec.emit('note', 'system', '父进程观测到子进程退出码 73；用原请求键在新进程重试找回结果')
        db = TracedDatabase(db_path, rec, 'crash-retry', TABLES)
        call_user(rec, 'crash-retry', R_CRASH, fn, db, validate, display)
        _marker(db_path, rec, 'C 重试后')
        rec.emit('note', 'aux', '辅助库存探针：12-10 婴儿床容量只有 1，这个请求需要 2 张')
        db = TracedDatabase(db_path, rec, 'aux', TABLES)
        call_user(rec, 'aux', R_AUX, fn, db, validate, display)
        raw = connect_raw(db_path)
        rec.snapshot(raw, TABLES, 'system', 'final', '最终状态')
        raw.close()
        return {}


def _check(name, expected, actual, passed):
    return {'name': name, 'expected': expected, 'actual': actual, 'passed': bool(passed)}


def _snap(snapshots, label):
    return next((s['tables'] for s in snapshots if s.get('label') == label), None)


def judge(scenario, events, snapshots, responses):
    checks, notes = [], []
    final = snapshots[-1]['tables']
    by_actor = {r['actor']: r for r in responses}

    ok = by_actor.get('ok')
    ok_nights = [n for n in final['booking_nights'] if n['request_id'] == 'R-ok']
    ok_active = [n for n in final['active_room_nights'] if n['request_id'] == 'R-ok']
    rooms = {n['room_id'] for n in ok_nights}
    nights_set = {n['night'] for n in ok_nights}
    checks.append(_check('成功对照：响应 BOOKED', 'BOOKED',
                         ok['body'].get('status') if ok else '无响应',
                         bool(ok) and ok['status'] == 200 and ok['body'].get('status') == 'BOOKED'))
    checks.append(_check('成功对照：两晚同一间房', '1 个房间 × 2 晚',
                         f"房间 {sorted(rooms)}，晚 {sorted(nights_set)}",
                         len(rooms) == 1 and nights_set == {'2026-12-07', '2026-12-08'}))
    checks.append(_check('成功对照：active 占用与预订一致且离店日不占房',
                         '2 行 active，无 12-09',
                         f"{len(ok_active)} 行， nights={sorted(n['night'] for n in ok_active)}",
                         len(ok_active) == 2 and all(n['night'] != '2026-12-09' for n in ok_active)
                         and {(n['room_id'], n['night']) for n in ok_active}
                         == {(n['room_id'], n['night']) for n in ok_nights}))

    rej = by_actor.get('rej')
    rej_retry = by_actor.get('rej-retry')
    rej_rows = [b for b in final['bookings'] if b['request_id'] == 'R-rej']
    rej_events = [e for e in final['reservation_events'] if e['request_id'] == 'R-rej']
    pre_a, post_a = _snap(snapshots, 'A 场景前'), _snap(snapshots, 'A 重试后')
    unchanged = pre_a is not None and post_a is not None and all(
        pre_a[t] == post_a[t] for t in ('active_room_nights', 'auxiliary_inventory', 'booking_nights'))
    checks.append(_check('A：响应 REJECTED（HTTP 200 也要看响应体）', '200 + REJECTED',
                         f"{rej['status'] if rej else '无'} {rej['body'].get('status') if rej else ''}",
                         bool(rej) and rej['status'] == 200 and rej['body'].get('status') == 'REJECTED'))
    checks.append(_check('A：落库 1 条 REJECTED 预订 + 1 条拒订事件', '1 + 1',
                         f"{len(rej_rows)} 条预订（{[b['status'] for b in rej_rows]}），{len(rej_events)} 条事件",
                         len(rej_rows) == 1 and rej_rows[0]['status'] == 'REJECTED'
                         and len(rej_events) == 1 and rej_events[0]['event_type'] == 'REJECTED'))
    checks.append(_check('A：占用/房晚/辅助库存全部不变', '三表与场景前一致',
                         '一致' if unchanged else '有变化', unchanged))
    checks.append(_check('A：同键重试不新增记录且 replayed=True', 'replayed=True，记录数不变',
                         f"replayed={rej_retry['body'].get('replayed') if rej_retry else '无'}",
                         bool(rej_retry) and rej_retry['body'].get('replayed') is True
                         and len(rej_rows) == 1 and len(rej_events) == 1))

    roll = by_actor.get('roll')
    pre_b, post_b = _snap(snapshots, 'B 场景前'), _snap(snapshots, 'B 回滚后')
    b_same = pre_b is not None and post_b is not None and pre_b == post_b
    tentative = [s for s in snapshots if s.get('actor') == 'roll' and s['phase'] == 'in_txn'
                 and any(n['request_id'] == 'R-roll' for n in s['tables']['booking_nights'])]
    rollback_events = [e for e in events if e['actor'] == 'roll' and e['kind'] == 'txn_rollback']
    checks.append(_check('B：响应 ROLLED_BACK（HTTP 200 也要看响应体）', '200 + ROLLED_BACK',
                         f"{roll['status'] if roll else '无'} {roll['body'].get('status') if roll else ''}",
                         bool(roll) and roll['status'] == 200 and roll['body'].get('status') == 'ROLLED_BACK'))
    checks.append(_check('B：事务内曾写入第一晚（临时可见）', '≥1 个含 R-roll 房晚的事务内快照',
                         f"{len(tentative)} 个快照", len(tentative) >= 1))
    checks.append(_check('B：发生真实回滚', '≥1 次回滚事件', len(rollback_events), len(rollback_events) >= 1))
    checks.append(_check('B：最终所有相关表与运行前一致', '6 张表逐行一致',
                         '一致' if b_same else '有差异', b_same))

    exit_events = [e for e in events if e['kind'] == 'process_exit']
    crash_rows = [b for b in final['bookings'] if b['request_id'] == 'R-crash']
    crash_nights = [n for n in final['booking_nights'] if n['request_id'] == 'R-crash']
    crash_active = [n for n in final['active_room_nights'] if n['request_id'] == 'R-crash']
    crash_retry = by_actor.get('crash-retry')
    crash_rooms = {n['room_id'] for n in crash_nights}
    checks.append(_check('C：子进程在提交后退出（退出码 73）', '≥1 次 process_exit',
                         f"{len(exit_events)} 次", len(exit_events) >= 1))
    checks.append(_check('C：订单与两晚占用都保留', '1 条 BOOKED + 2 晚同房 + 2 行 active',
                         f"{[b['status'] for b in crash_rows]}，{len(crash_nights)} 晚 {sorted(crash_rooms)}，{len(crash_active)} 行 active",
                         len(crash_rows) == 1 and crash_rows[0]['status'] == 'BOOKED'
                         and len(crash_nights) == 2 and len(crash_rooms) == 1 and len(crash_active) == 2))
    checks.append(_check('C：同键重试找回原订单，不重复占用', '200 BOOKED replayed=True，active 仍 2 行',
                         f"{crash_retry['status'] if crash_retry else '无'} "
                         f"{crash_retry['body'].get('status') if crash_retry else ''} "
                         f"replayed={crash_retry['body'].get('replayed') if crash_retry else '无'}",
                         bool(crash_retry) and crash_retry['status'] == 200
                         and crash_retry['body'].get('status') == 'BOOKED'
                         and crash_retry['body'].get('replayed') is True and len(crash_active) == 2))

    aux = by_actor.get('aux')
    aux_rows = [b for b in final['bookings'] if b['request_id'] == 'R-aux']
    aux_active = [n for n in final['active_room_nights'] if n['request_id'] == 'R-aux']
    aux_code = aux['body'].get('error', {}).get('code') if aux else None
    checks.append(_check('辅助探针：409 AUXILIARY_UNAVAILABLE 且零占用',
                         '409 AUXILIARY_UNAVAILABLE，0 预订 / 0 active',
                         f"{aux['status'] if aux else '无'} {aux_code}，{len(aux_rows)} 预订 / {len(aux_active)} active",
                         bool(aux) and aux['status'] == 409 and aux_code == 'AUXILIARY_UNAVAILABLE'
                         and not aux_rows and not aux_active))

    notes.append('A 和 B 的 HTTP 状态都是 200（沿用原演示）：页面按响应体 REJECTED / ROLLED_BACK 加上真实表差异区分，'
                 '不能只看 HTTP 状态。REJECTED 是持久化的业务记录；ROLLED_BACK 什么都没留下。')
    if aux_code == 'CONSTRAINT_CONFLICT':
        notes.append('辅助探针暴露了无条件 UPDATE：cots_used 超过容量被 CHECK 约束拦下，整单回滚，'
                     '但返回的是 CONSTRAINT_CONFLICT 而不是干净的 AUXILIARY_UNAVAILABLE。'
                     '把容量条件放进 WHERE 并检查 rowcount，才能得到正确的业务语义。')
    if not checks[1]['passed'] and len(rooms) > 1:
        notes.append('逐晚挑房让两晚住进了不同房间：客人中途要换房。正确做法是求“每一晚都可用”的房间交集，'
                     '交集为空就整单拒订，而不是拼凑不同房间。')
    return {'checks': checks, 'passed': all(c['passed'] for c in checks), 'notes': notes}


META = {
    'id': 'hotel-two-nights',
    'title': '酒店 · 连续两晚必须订到同一间房',
    'subtitle': '半开区间、拒订持久化、真回滚、提交后进程退出',
    'source': {
        'repo': 'https://github.com/simonshi-stu/hotel-reservation-lab',
        'commit': '7151ff8ca479b2887706ce3f2186611a06454c1b',
        'files': [
            {'path': 'hotel_sim/sqlite_store.py', 'symbol': 'SQLiteHotel.reserve', 'lines': '223-254'},
            {'path': 'hotel_sim/sqlite_schema.py', 'symbol': 'STATEMENTS（表与触发器）', 'lines': '1-65'},
            {'path': 'hotel_demo/server.py', 'symbol': 'TracedHotel / InjectedFailure', 'lines': '35-62'},
            {'path': 'scripts/audit_sqlite_crashes.py', 'symbol': 'ExitAt / crash_worker', 'lines': '7-14'},
            {'path': 'data/week.json', 'symbol': '房间/封锁日/每日资源（12-07 起一周）', 'lines': ''},
        ],
        'tests': ['test_sqlite.py', 'scripts/audit_sqlite.py::booking_and_cancel_failure_atomicity'],
    },
    'simplifications': [
        '2 间 standard 房（S101/S102），无升级、床型偏好、价格、保护房；日期取自 week.json 的 12-07~12-12',
        '分配规则简化为“所有晚都空闲且未封锁的同一间房”；婴儿床/加床需求用 auxiliary_needs 简化；不做单间人数上限校验',
        '保留半开区间 [入住, 离店)、REJECTED 持久化、关键触发器、故障注入点与提交后 os._exit(73)',
        '12-12 两间房均封锁（对应原题 blocked_dates 思路）；12-08 的 S101 由预置订单 PRE-1 占用',
    ],
    'statement': (
        '客人订连续两晚，必须全程同一间房；离店日不占库存（半开区间）。\n\n'
        '请实现 reserve，并观察同一题下四个场景的不同结果：\n'
        '· 成功对照（R-ok，12-07→12-09）：S101 在 12-08 已被占用，应订到 S102 两晚。\n'
        '· A 正常拒订（R-rej，12-11→12-13）：第一晚有房、第二晚 12-12 全部封锁。按原实现要提交一条 REJECTED 预订和一条拒订事件；'
        '占用、房晚、婴儿床/加床库存都不变；同键重试不新增记录。页面显示“拒订已记录，未占房”。\n'
        '· B 真正回滚（R-roll，12-09→12-11，带 1 个婴儿）：本来可订，但在写入第一晚后注入异常。'
        '回放能看到事务里曾写第一晚，最终所有表与运行前一致。页面显示“已回滚”。\n'
        '· C 提交后进程退出（R-crash，12-09→12-11）：提交成功后子进程 os._exit(73)，客户端没收到回复。'
        '订单和两晚占用都保留；同键重试找回原订单，不重复占用。\n'
        '· 辅助库存探针（R-aux，12-10 一晚，需要 2 张婴儿床）：当晚容量只有 1，应得 409 AUXILIARY_UNAVAILABLE 且零占用。\n\n'
        '注意：A 和 B 都返回 HTTP 200，必须看响应体 REJECTED / ROLLED_BACK 和真实表差异来区分。'
    ),
    'hints': [
        'nights_of 返回 [入住, 离店) 的晚列表：12-07→12-09 只占 07、08 两晚，离店日 09 不占房。',
        '“同一间房”= 每晚可用房间集合的交集。逐晚各挑一间会让客人中途换房。',
        '拒订也是业务记录：INSERT bookings(REJECTED) + reservation_events(REJECTED) 后照常提交，重试才能找回。',
        '回滚由事务边界保证：异常抛出后 with db.transaction() 自动 ROLLBACK，事务内写过的行全部撤销。',
        '提交后进程退出 ≠ 回滚：COMMIT 之后的数据已持久化，同键重试靠 request_sha256 找回原记录。',
    ],
    'scenarios': [
        {'id': 'full', 'name': '四场景完整运行',
         'description': '成功对照 → A 拒订+重试 → B 注入异常回滚 → C 提交后退出+重试，共用一个临时数据库。'},
    ],
    'presets': [
        {'id': 'starter', 'name': '起始代码（待补全）', 'code': STARTER},
        {'id': 'reference', 'name': '参考解（同房交集+完整记录）', 'code': REFERENCE},
        {'id': 'wrong_per_night', 'name': '错误解①：逐晚各自挑房', 'code': WRONG_PER_NIGHT},
        {'id': 'wrong_no_reject', 'name': '错误解②：拒订不落库', 'code': WRONG_NO_REJECT_RECORD},
    ],
    'sql_presets': [
        {'id': 'sql_reference', 'name': '参考 SQL（条件更新辅助库存）', 'code': SQL_REFERENCE},
        {'id': 'sql_wrong', 'name': '错误 SQL（无条件更新）', 'code': SQL_WRONG},
    ],
    'sql_help': 'SQL 模式：只编辑逐晚更新辅助库存的那条 UPDATE，其余逻辑由参考解框架执行。可用命名参数 :cots、:extras、:night。',
    'initial_description': 'S101/S102 两间房 × 6 晚；12-12 均封锁；S101 在 12-08 被 PRE-1 占用；每日婴儿床/加床容量取自 week.json。',
}
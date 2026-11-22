"""真实轨迹记录：run_id 串联 输入→校验→事务→SQL→影响行数→提交/回滚→响应→前端显示。

所有 SQL 事件来自实际执行的连接；触发器等内部 SQL 通过 sqlite3 trace 回调观测。
快照分四种相位：initial / in_txn（事务内暂时可见）/ committed / rolled_back。
"""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from .sandbox import BusinessError, DatabaseBusy, InjectedFailure

WRITE_VERBS = {'INSERT', 'UPDATE', 'DELETE', 'REPLACE'}


def connect_raw(path, busy_ms=5000):
    raw = sqlite3.connect(str(path), timeout=busy_ms / 1000, isolation_level=None)
    raw.row_factory = sqlite3.Row
    raw.execute('PRAGMA foreign_keys=ON')
    raw.execute(f'PRAGMA busy_timeout={busy_ms}')
    return raw


def dump_tables(raw, tables):
    return {t: [dict(r) for r in raw.execute(f'SELECT * FROM {t} ORDER BY rowid')] for t in tables}


class Recorder:
    def __init__(self, jsonl_path):
        self._seq = 0
        try:
            with open(jsonl_path, encoding='utf-8') as fh:
                for line in fh:
                    try:
                        self._seq = max(self._seq, json.loads(line).get('seq', 0))
                    except json.JSONDecodeError:
                        pass
        except FileNotFoundError:
            pass
        self._fh = open(jsonl_path, 'a', encoding='utf-8')
        self._lock = threading.Lock()
        self._t0 = time.monotonic()

    def _write(self, line):
        self._fh.write(json.dumps(line, ensure_ascii=False) + '\n')
        self._fh.flush()

    def emit(self, kind, actor, label, **fields):
        with self._lock:
            self._seq += 1
            ev = {'type': 'event', 'seq': self._seq,
                  'ms': round((time.monotonic() - self._t0) * 1000),
                  'kind': kind, 'actor': actor, 'label': label}
            ev.update(fields)
            self._write(ev)
            return self._seq

    def snapshot(self, raw, tables, actor, phase, label):
        data = dump_tables(raw, tables)
        with self._lock:
            self._seq += 1
            line = {'type': 'snapshot', 'seq': self._seq,
                    'ms': round((time.monotonic() - self._t0) * 1000),
                    'actor': actor, 'phase': phase, 'label': label, 'tables': data}
            self._write(line)
            return self._seq

    def close(self):
        self._fh.close()


class FakeCursor:
    def __init__(self, rows, rowcount):
        self._rows = rows
        self._index = 0
        self.rowcount = rowcount

    def fetchone(self):
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self):
        rows = self._rows[self._index:]
        self._index = len(self._rows)
        return rows


class TracedConnection:
    def __init__(self, raw, rec, actor, tables, fault=None):
        self.raw = raw
        self.rec = rec
        self.actor = actor
        self.tables = tables
        self.fault = fault
        self.in_txn = False
        self._trace_lines = []
        raw.set_trace_callback(self._trace_lines.append)

    def execute(self, sql, params=()):
        self._trace_lines = []
        verb = sql.strip().split(None, 1)[0].upper()
        shown_params = params if isinstance(params, dict) else list(params)
        try:
            cur = self.raw.execute(sql, params)
        except sqlite3.Error as exc:
            self.rec.emit('sql', self.actor, f'{verb} · 执行失败（语句本身报错）',
                          sql=sql, params=shown_params, rowcount=None,
                          internal_sql=self._trace_lines[1:],
                          write=verb in WRITE_VERBS, error=str(exc),
                          visibility='in_txn' if self.in_txn else 'autocommit')
            raise
        internal = self._trace_lines[1:]
        if verb in WRITE_VERBS:
            rowcount = cur.rowcount
            self.rec.emit('sql', self.actor, f'{verb} · 影响 {rowcount} 行',
                          sql=sql, params=shown_params, rowcount=rowcount,
                          internal_sql=internal, write=True,
                          visibility='in_txn' if self.in_txn else 'autocommit')
            self.rec.snapshot(self.raw, self.tables, self.actor,
                              'in_txn' if self.in_txn else 'autocommit',
                              '事务内暂时可见（未提交）' if self.in_txn else '自动提交写入')
            result = FakeCursor([], rowcount)
        else:
            rows = [dict(r) for r in cur.fetchall()]
            self.rec.emit('sql', self.actor, f'{verb} · 读到 {len(rows)} 行',
                          sql=sql, params=shown_params, rowcount=len(rows),
                          rows_read=rows[:50], internal_sql=internal, write=False,
                          visibility='in_txn' if self.in_txn else 'autocommit')
            result = FakeCursor(rows, len(rows))
        if self.fault is not None and self.fault(sql, params):
            self.rec.emit('fault', self.actor, '教学故障注入：首晚写入后主动中断（真实抛出异常）')
            raise InjectedFailure('synthetic first-night failure')
        return result

    def control(self, sql, kind, label):
        self._trace_lines = []
        self.raw.execute(sql)
        self.rec.emit(kind, self.actor, label, sql=sql,
                      internal_sql=self._trace_lines[1:])


class TracedDatabase:
    """每题一个 db 句柄；transaction() 与原题一致用 BEGIN IMMEDIATE。"""

    def __init__(self, path, rec, actor, tables, busy_ms=5000, fault=None,
                 query_barrier=None):
        self.path = path
        self.rec = rec
        self.actor = actor
        self.tables = tables
        self.busy_ms = busy_ms
        self.fault = fault
        self.query_barrier = query_barrier
        self._query_used = False

    @contextmanager
    def transaction(self):
        raw = connect_raw(self.path, self.busy_ms)
        tc = TracedConnection(raw, self.rec, self.actor, self.tables, self.fault)
        begun = False
        try:
            try:
                tc.control('BEGIN IMMEDIATE', 'txn_begin', '开始写事务 · BEGIN IMMEDIATE')
                begun = True
                tc.in_txn = True
            except sqlite3.OperationalError as exc:
                if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
                    self.rec.emit('txn_busy', self.actor,
                                  'BEGIN IMMEDIATE 拿不到写锁 · 数据库忙（真实观测）',
                                  sql='BEGIN IMMEDIATE', error=str(exc))
                    raise DatabaseBusy('database busy; retry the same request key and body') from None
                raise
            yield tc
            tc.control('COMMIT', 'txn_commit', '提交成功 · COMMIT（写入持久化）')
            tc.in_txn = False
            self.rec.snapshot(raw, self.tables, self.actor, 'committed', '已提交持久化')
        except BaseException as exc:
            if begun:
                raw.rollback()
                self.rec.emit('txn_rollback', self.actor, '事务未提交，已执行回滚',
                              reason=f'{type(exc).__name__}: {exc}')
                self.rec.snapshot(raw, self.tables, self.actor, 'rolled_back',
                                  '回滚后（事务内临时写入已撤销）')
            raise
        finally:
            raw.close()

    def query(self, sql, params=()):
        """事务外独立连接读取（错误解演示用；参考解不需要）。"""
        if self.query_barrier is not None and not self._query_used:
            self._query_used = True
            self.rec.emit('illustrative', self.actor,
                          '教学示意：调度屏障让两笔请求都先完成事务外读取（读取值本身来自真实数据库）')
            try:
                self.query_barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass
        raw = connect_raw(self.path, self.busy_ms)
        try:
            tc = TracedConnection(raw, self.rec, self.actor, self.tables)
            self.rec.emit('note', self.actor, '事务外独立连接读取（未进入写事务）')
            return tc.execute(sql, params)
        finally:
            raw.close()


def read_events(jsonl_path):
    events, snapshots = [], []
    with open(jsonl_path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if item['type'] == 'snapshot':
                snapshots.append(item)
            else:
                events.append(item)
    return events, snapshots
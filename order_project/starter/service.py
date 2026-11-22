"""Persist one inventory deduction and its receipt in the same transaction."""
from contextlib import closing
from pathlib import Path
import sqlite3


def connect(db):
    conn = sqlite3.connect(db, timeout=2, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db):
    with closing(connect(db)) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))


def place_order(db, request_key, sku, qty):
    """Return (HTTP status, receipt); API validates input first."""
    if not isinstance(request_key, str) or not request_key or not isinstance(sku, str) or not sku or type(qty) is not int or qty <= 0:
        return 400, {"error": "invalid_order"}
    with closing(connect(db)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # TODO 1: 查 request_key。相同 sku/qty 时返回原小票（HTTP 200，
            # replayed=True）；同键不同请求返回 409 key_conflict。
            # TODO 2: 带 qty >= ? 条件扣库存；未命中时区分未知 SKU（404
            # unknown_sku）和库存不足（409 insufficient_stock）。
            # TODO 3: 读取剩余库存、写入小票，最后提交，返回 201 和
            # {request_key, sku, qty, stock_left, replayed: False}。
            raise NotImplementedError("补齐查历史、扣库存、写小票三步")
        except BaseException:
            conn.rollback()
            raise

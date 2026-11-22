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
        # Take the write lock before reading the idempotency key, so two
        # concurrent requests cannot both decide the key is unused.
        conn.execute("BEGIN IMMEDIATE")
        try:
            prior = conn.execute("SELECT * FROM orders WHERE request_key=?", (request_key,)).fetchone()
            if prior is not None:
                conn.rollback()
                if prior["sku"] != sku or prior["qty"] != qty:
                    return 409, {"error": "key_conflict"}
                return 200, {**dict(prior), "replayed": True}
            updated = conn.execute("UPDATE inventory SET qty=qty-? WHERE sku=? AND qty>=?", (qty, sku, qty))
            if updated.rowcount != 1:
                found = conn.execute("SELECT 1 FROM inventory WHERE sku=?", (sku,)).fetchone()
                conn.rollback()
                return (409, {"error": "insufficient_stock"}) if found else (404, {"error": "unknown_sku"})
            remaining = conn.execute("SELECT qty FROM inventory WHERE sku=?", (sku,)).fetchone()["qty"]
            receipt = dict(request_key=request_key, sku=sku, qty=qty, stock_left=remaining)
            conn.execute("INSERT INTO orders(request_key,sku,qty,stock_left) VALUES (?, ?, ?, ?)", tuple(receipt.values()))
            conn.commit()
            return 201, {**receipt, "replayed": False}
        except BaseException:
            conn.rollback()
            raise

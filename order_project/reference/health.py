"""Separate a living process from a process ready to serve orders."""
from contextlib import closing
from pathlib import Path
import sqlite3


def health_result(path, db):
    if path == "/live":
        return 200, {"status": "alive"}
    if path != "/ready":
        return 404, {"error": "not_found"}
    try:
        # Read-only mode avoids silently creating an empty database as a probe.
        uri = Path(db).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.25)) as conn:
            conn.execute("SELECT sku, qty FROM inventory LIMIT 1").fetchone()
            conn.execute("SELECT request_key FROM orders LIMIT 1").fetchone()
    except (sqlite3.Error, OSError):
        return 503, {"status": "not_ready"}
    return 200, {"status": "ready"}

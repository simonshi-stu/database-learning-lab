"""Small HTTP adapter. No cluster credentials are available to this service."""
import json
import os
import sqlite3
from pathlib import Path

from service import init_db, place_order

MAX_BODY = 4096


def dispatch(method, path, headers, body, db):
    if method == "GET" and path in {"/live", "/ready"}:
        # The previous chapters are testable before health.py exists.
        from health import health_result
        return health_result(path, db)
    if path != "/orders":
        return 404, {"error": "not_found"}
    if method != "POST":
        return 405, {"error": "method_not_allowed"}
    if len(body) > MAX_BODY:
        return 413, {"error": "body_too_large"}
    key = headers.get("Idempotency-Key", "")
    if not isinstance(key, str) or not (1 <= len(key) <= 80) or not key.isascii() or not all(c.isalnum() or c in "-_." for c in key):
        return 400, {"error": "invalid_key"}
    try:
        value = json.loads(body)
    except (UnicodeError, ValueError, RecursionError):
        return 400, {"error": "invalid_json"}
    if not isinstance(value, dict):
        return 400, {"error": "invalid_order"}
    sku, qty = value.get("sku"), value.get("qty")
    if not isinstance(sku, str) or not 1 <= len(sku) <= 64 or type(qty) is not int or not 1 <= qty <= 10000:
        return 400, {"error": "invalid_order"}
    try:
        return place_order(db, key, sku, qty)
    except sqlite3.OperationalError:
        return 503, {"error": "temporarily_unavailable"}


def serve(db, host="127.0.0.1", port=8080):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    init_db(db)

    class Handler(BaseHTTPRequestHandler):
        def handle_order(self):
            self.connection.settimeout(5)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or self.headers.get("Transfer-Encoding"):
                self.send_error(400)
                return
            if length > MAX_BODY:
                self.send_error(413)
                return
            try:
                data = self.rfile.read(length)
            except TimeoutError:
                self.send_error(408)
                return
            if len(data) != length:
                self.send_error(400)
                return
            status, payload = dispatch(self.command, self.path, self.headers, data, db)
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        do_GET = do_POST = handle_order

    with ThreadingHTTPServer((host, port), Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    db = Path(os.environ.get("ORDER_DB", "orders.db"))
    serve(db, os.environ.get("ORDER_HOST", "127.0.0.1"), int(os.environ.get("ORDER_PORT", "8080")))

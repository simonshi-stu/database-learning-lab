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
    # TODO 1: 仅允许 POST /orders；未知路由返回 404，方法不支持返回 405。
    # TODO 2: 请求体超过 MAX_BODY 返回 413。请求键限 1~80 个 ASCII
    # 字母、数字或 -_.；否则返回 400 invalid_key。坏 JSON 返回 400 invalid_json；
    # sku 必须是 1~64 字符的文本，qty 必须是 1~10000 的整数（布尔值不算）。
    # TODO 3: 调用 place_order 并原样返回状态和结果；数据库暂时不可用返回 503。
    return 501, {"error": "not_implemented"}


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

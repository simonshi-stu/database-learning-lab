"""两个探针回答不同的问题。"""
from contextlib import closing
from pathlib import Path
import sqlite3


def health_result(path, db):
    if path == "/live":
        return 200, {"status": "alive"}
    if path != "/ready":
        return 404, {"error": "not_found"}
    # TODO: 使用 SQLite 的只读连接（URI 参数 ?mode=ro）。检查两张
    # 订单表是否能查询，成功返回 200 ready，失败返回 503 not_ready。
    # 避免文件不存在时被 sqlite3.connect 自动创建。
    return 200, {"status": "ready"}

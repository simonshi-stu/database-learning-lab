"""课程运行器：真实 DuckDB 查询与本地学生 Python 子进程。"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import duckdb

from . import core

ROOT = Path(__file__).resolve().parent.parent


def _json_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def _raw_tables(con, rows):
    con.execute("""CREATE TABLE raw_events(
        event_id VARCHAR, order_id VARCHAR, booking_id VARCHAR, kind VARCHAR,
        amount_cents BIGINT, event_time TIMESTAMPTZ, arrived_at TIMESTAMPTZ,
        source VARCHAR, schema_version INTEGER, payload_json VARCHAR)""")
    for row in rows:
        if core.validate_event(row):
            continue
        try:
            event_time = datetime.fromisoformat(row["event_time"].replace("Z", "+00:00"))
            arrived = datetime.fromisoformat(row["arrived_at"].replace("Z", "+00:00"))
        except (KeyError, AttributeError, ValueError):
            continue
        con.execute("INSERT INTO raw_events VALUES (?,?,?,?,?,?,?,?,?,?)", [
            row.get("event_id"), row.get("order_id"), row.get("booking_id"), row.get("kind"),
            row.get("amount_cents"), event_time, arrived, row.get("source"), row.get("schema_version"),
            json.dumps(row.get("payload", {}), ensure_ascii=False),
        ])
    con.execute("""CREATE TABLE bookings AS
        SELECT event_id, booking_id,
               CAST(json_extract_string(payload_json, '$.room_id') AS VARCHAR) AS room_id,
               CAST(json_extract_string(payload_json, '$.check_in') AS DATE) AS check_in,
               CAST(json_extract_string(payload_json, '$.check_out') AS DATE) AS check_out
        FROM raw_events WHERE kind='hotel_booking'""")


def _context(root, raw=False):
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    sales_files = core.parquet_files(root, "sales")
    hotel_files = core.parquet_files(root, "hotel")
    if sales_files:
        pattern = (Path(root) / "silver" / "sales" / "*" / "*.parquet").as_posix()
        con.execute("CREATE VIEW facts AS SELECT * FROM read_parquet(" + core.sql_text(pattern) +
                    ", hive_partitioning=true, union_by_name=true)")
    else:
        con.execute("CREATE TABLE facts(event_id VARCHAR, business_date DATE, signed_cents BIGINT, arrived_at VARCHAR)")
    if hotel_files:
        pattern = (Path(root) / "silver" / "hotel" / "*" / "*.parquet").as_posix()
        con.execute("CREATE TABLE hotel_nights AS SELECT * FROM read_parquet(" + core.sql_text(pattern) +
                    ", hive_partitioning=true, union_by_name=true)")
    else:
        con.execute("CREATE TABLE hotel_nights(booking_id VARCHAR, stay_date DATE, room_nights BIGINT)")
    if raw:
        _raw_tables(con, [item["row"] for item in core.load_raw(root) if isinstance(item["row"], dict)])
    return con


def run_sql(code, root, raw=False, analyze=False):
    text = (code or "").strip().rstrip(";").strip()
    if not text:
        return {"columns": [], "rows": [], "error": "请先写一条 SQL。", "plan": None}
    if ";" in text or not re.match(r"(?is)^(SELECT|WITH|EXPLAIN)\b", text):
        return {"columns": [], "rows": [], "error": "本关只接受一条只读 SELECT/WITH 查询。", "plan": None}
    if re.search(r"(?i)\b(INSERT|UPDATE|DELETE|DROP|ALTER|ATTACH|COPY|PRAGMA|CALL|INSTALL|LOAD)\b", text):
        return {"columns": [], "rows": [], "error": "查询中包含本关不允许的写入或外部访问关键字。", "plan": None}
    con = _context(root, raw=raw)
    try:
        if analyze:
            plan = con.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + text).fetchone()[1]
        else:
            plan = None
        cur = con.execute(text)
        columns = [d[0] for d in cur.description]
        rows = [[_json_value(v) for v in row] for row in cur.fetchall()]
        return {"columns": columns, "rows": rows, "error": None, "plan": plan}
    except Exception as exc:
        return {"columns": [], "rows": [], "error": str(exc).splitlines()[0], "plan": None}
    finally:
        con.close()


def run_python(code, root, args=None, files=None, env_extra=None, timeout=25):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if files:
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        python_files = [name for name in files if name.endswith(".py")]
        entry = "service.py" if "service.py" in python_files else (python_files[0] if python_files else "student.py")
        if not python_files:
            (root / "student.py").write_text(code, encoding="utf-8")
    else:
        (root / "student.py").write_text(code, encoding="utf-8")
        entry = "student.py"
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(ROOT), "LAKE_ROOT": str(root),
                "PYTHONIOENCODING": "utf-8", "LAKE_ARGS": json.dumps(args or {})})
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})
    try:
        proc = subprocess.run([sys.executable, "-B", entry], cwd=str(root), env=env,
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "result": None, "stdout": "", "stderr": "学生代码超过 25 秒，已终止。", "exit_code": None}
    stdout = proc.stdout.decode("utf-8", "replace")
    stderr = proc.stderr.decode("utf-8", "replace")
    result = None
    for line in reversed(stdout.splitlines()):
        if line.startswith("LAKE_RESULT="):
            try:
                result = json.loads(line.split("=", 1)[1])
            except json.JSONDecodeError:
                pass
    return {"ok": proc.returncode == 0 and result is not None, "result": result,
            "stdout": stdout, "stderr": stderr, "exit_code": proc.returncode}

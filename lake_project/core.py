"""可复跑的本地数据湖：不可变原始层、校验去重、Parquet 分层、原子发布。"""
import hashlib
import json
import os
import shutil
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import duckdb

SHANGHAI = ZoneInfo("Asia/Shanghai")
SALES_COLUMNS = [
    "business_date", "event_id", "order_id", "kind", "amount_cents", "signed_cents",
    "event_time", "arrived_at", "source", "schema_version", "store_id", "attributes",
    "raw_file", "raw_line", "run_id", "payload_sha256",
]
HOTEL_COLUMNS = [
    "stay_date", "booking_id", "room_id", "event_id", "room_nights", "check_in", "check_out",
    "source", "raw_file", "raw_line", "run_id", "payload_sha256",
]


def new_run_id(prefix="run"):
    return prefix + "-" + uuid4().hex[:12]


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid4().hex[:8])
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def json_read(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def sql_text(value):
    return "'" + str(value).replace("'", "''") + "'"


def business_date(event_time):
    dt = datetime.fromisoformat(str(event_time).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("event_time 必须带时区偏移")
    return dt.astimezone(SHANGHAI).date().isoformat()


def payload_hash(row):
    ignored = {"arrived_at", "raw_file", "raw_line", "run_id", "payload_sha256"}
    return sha256_bytes(canonical_bytes({k: v for k, v in row.items() if k not in ignored}))


def validate_event(row):
    if not isinstance(row, dict):
        return ["记录不是 JSON 对象"]
    reasons = []
    for field in ("event_id", "kind", "event_time", "arrived_at", "source", "schema_version"):
        if row.get(field) in (None, ""):
            reasons.append("缺失字段 " + field)
    if not isinstance(row.get("event_id"), str):
        reasons.append("event_id 必须是字符串")
    kind = row.get("kind")
    if kind not in ("payment", "refund", "hotel_booking"):
        reasons.append("未知 kind=" + str(kind))
    amount = row.get("amount_cents")
    if isinstance(amount, bool) or not isinstance(amount, int):
        reasons.append("amount_cents 必须是整数分")
    elif kind != "hotel_booking" and amount <= 0:
        reasons.append("销售金额必须大于 0")
    if kind in ("payment", "refund") and not row.get("order_id"):
        reasons.append("销售事件缺失 order_id")
    if kind == "hotel_booking":
        if not row.get("booking_id"):
            reasons.append("酒店事件缺失 booking_id")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            reasons.append("payload 必须是对象")
        else:
            for field in ("room_id", "check_in", "check_out"):
                if not payload.get(field):
                    reasons.append("payload 缺失 " + field)
            try:
                if date.fromisoformat(payload["check_in"]) >= date.fromisoformat(payload["check_out"]):
                    reasons.append("check_out 必须晚于 check_in")
            except (KeyError, TypeError, ValueError):
                reasons.append("入住/退房日期非法")
    if not isinstance(row.get("schema_version"), int) or isinstance(row.get("schema_version"), bool):
        reasons.append("schema_version 必须是整数")
    if not isinstance(row.get("payload"), dict):
        reasons.append("payload 必须是对象")
    try:
        business_date(row.get("event_time"))
        datetime.fromisoformat(str(row.get("arrived_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        reasons.append("event_time/arrived_at 日期非法")
    return reasons


def write_raw_batch(root, rows, run_id=None, crash_after_raw=False):
    root = Path(root)
    run_id = run_id or new_run_id("ingest")
    payload = b"".join(canonical_bytes(row) + b"\n" for row in rows)
    digest = sha256_bytes(payload)
    batch_dir = root / "raw" / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    raw_path = batch_dir / ("batch-" + digest[:16] + ".jsonl")
    if raw_path.exists():
        if sha256_file(raw_path) != digest:
            raise RuntimeError("原始文件内容寻址冲突")
    else:
        tmp = raw_path.with_suffix(".tmp")
        with tmp.open("wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, raw_path)
    manifest_path = batch_dir / (raw_path.name + ".manifest.json")
    marker_path = batch_dir / (raw_path.name + ".confirmed")
    manifest = json_read(manifest_path)
    if manifest is None:
        manifest = {
            "run_id": run_id,
            "batch_id": raw_path.stem,
            "raw_file": raw_path.relative_to(root).as_posix(),
            "raw_sha256": digest,
            "row_count": len(rows),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": sorted({str(r.get("source", "")) for r in rows if isinstance(r, dict)}),
            "confirmed": marker_path.exists(),
        }
        json_write(manifest_path, manifest)
    if crash_after_raw:
        return dict(manifest, replayed=raw_path.exists() and manifest["run_id"] != run_id,
                    crash_after_raw=True, confirmed=marker_path.exists())
    if not marker_path.exists():
        json_write(marker_path, {"run_id": run_id, "confirmed_at": datetime.now(timezone.utc).isoformat()})
    manifest["confirmed"] = True
    return dict(manifest, replayed=manifest["run_id"] != run_id, crash_after_raw=False)


def confirmed_manifests(root):
    root = Path(root)
    out = []
    for path in sorted((root / "raw" / "batches").glob("*.manifest.json")):
        marker = path.with_name(path.name.replace(".manifest.json", ".confirmed"))
        if marker.exists():
            item = json_read(path)
            item["manifest_file"] = path.relative_to(root).as_posix()
            item["confirmed_by"] = json_read(marker)
            out.append(item)
    return out


def load_raw(root):
    root = Path(root)
    rows = []
    seen = set()
    for manifest in confirmed_manifests(root):
        rel = manifest["raw_file"]
        if rel in seen:
            continue
        seen.add(rel)
        path = root / rel
        if sha256_file(path) != manifest["raw_sha256"]:
            raise RuntimeError("原始文件校验失败：" + rel)
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                row = {"_parse_error": str(exc)}
            rows.append({
                "row": row,
                "raw_file": rel,
                "raw_line": line_no,
                "run_id": manifest["run_id"],
                "batch_id": manifest["batch_id"],
            })
    return rows


def collect(root):
    raw = load_raw(root)
    def arrival_key(item):
        row = item.get("row", {})
        try:
            dt = datetime.fromisoformat(str(row.get("arrived_at")).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return float("inf")
            return dt.timestamp()
        except (TypeError, ValueError):
            return float("inf")
    raw = sorted(raw, key=lambda item: (arrival_key(item), item["raw_file"], item["raw_line"]))
    accepted = {}
    classified = []
    for item in raw:
        row = item["row"]
        reasons = list(item.get("_parse_error", "").split()) if "_parse_error" in row else validate_event(row)
        record = dict(item, reasons=reasons, payload_sha256=payload_hash(row) if isinstance(row, dict) else "")
        if reasons:
            record["classification"] = "rejected"
            classified.append(record)
            continue
        event_id = row["event_id"]
        digest = record["payload_sha256"]
        if event_id not in accepted:
            record["classification"] = "accepted"
            accepted[event_id] = record
        elif accepted[event_id]["payload_sha256"] == digest:
            record["classification"] = "duplicate"
        else:
            record["classification"] = "rejected"
            record["reasons"] = ["同 event_id 不同内容（冲突）"]
        classified.append(record)
    payment_orders = {r["row"]["order_id"] for r in accepted.values() if r["row"]["kind"] == "payment"}
    for record in classified:
        row = record["row"]
        if record["classification"] == "accepted" and row.get("kind") == "refund" and row.get("order_id") not in payment_orders:
            record["classification"] = "rejected"
            record["reasons"] = ["找不到原支付，隔离复核"]
            accepted.pop(row["event_id"], None)
    facts = []
    for record in accepted.values():
        row = record["row"]
        base = {
            "event_id": row["event_id"], "event_time": row["event_time"], "arrived_at": row["arrived_at"],
            "source": row["source"], "schema_version": row["schema_version"],
            "raw_file": record["raw_file"], "raw_line": record["raw_line"], "run_id": record["run_id"],
            "payload_sha256": record["payload_sha256"],
        }
        if row["kind"] in ("payment", "refund"):
            amount = row["amount_cents"]
            facts.append(dict(base, business_date=business_date(row["event_time"]), order_id=row["order_id"],
                              kind=row["kind"], amount_cents=amount,
                              signed_cents=amount if row["kind"] == "payment" else -amount,
                              store_id=row["payload"].get("store_id"),
                              attributes=json.dumps(row, ensure_ascii=False, sort_keys=True)))
    counts = {name: sum(r["classification"] == name for r in classified)
              for name in ("accepted", "duplicate", "rejected")}
    counts["total"] = len(classified)
    counts["conflicts"] = sum("冲突" in "；".join(r["reasons"]) for r in classified)
    counts["conserved"] = counts["total"] == counts["accepted"] + counts["duplicate"] + counts["rejected"]
    return dict(classified=classified, facts=facts, counts=counts, accepted=accepted)


def hotel_nights(facts):
    nights = []
    for fact in facts:
        if fact["kind"] != "hotel_booking":
            continue
        payload = json.loads(fact["attributes"])
        start = date.fromisoformat(payload["check_in"])
        end = date.fromisoformat(payload["check_out"])
        for offset in range((end - start).days):
            stay = start + timedelta(days=offset)
            nights.append({
                "stay_date": stay.isoformat(), "booking_id": fact["order_id"] if "order_id" in fact else payload["booking_id"],
                "room_id": payload["room_id"], "event_id": fact["event_id"], "room_nights": 1,
                "check_in": payload["check_in"], "check_out": payload["check_out"], "source": fact["source"],
                "raw_file": fact["raw_file"], "raw_line": fact["raw_line"], "run_id": fact["run_id"],
                "payload_sha256": fact["payload_sha256"],
            })
    return nights


def all_facts(root):
    collected = collect(root)
    facts = collected["facts"]
    nights = []
    for record in collected["accepted"].values():
        row = record["row"]
        if row.get("kind") != "hotel_booking":
            continue
        payload = row["payload"]
        start = date.fromisoformat(payload["check_in"])
        end = date.fromisoformat(payload["check_out"])
        for offset in range((end - start).days):
            stay = start + timedelta(days=offset)
            nights.append({
                "stay_date": stay.isoformat(), "booking_id": row["booking_id"], "room_id": payload["room_id"],
                "event_id": row["event_id"], "room_nights": 1, "check_in": payload["check_in"],
                "check_out": payload["check_out"], "source": row["source"], "raw_file": record["raw_file"],
                "raw_line": record["raw_line"], "run_id": record["run_id"],
                "payload_sha256": record["payload_sha256"],
            })
    return facts, nights, collected


def _insert_rows(con, table, columns, rows):
    if rows:
        con.execute("CREATE TABLE " + table + " (" + ", ".join(c + " VARCHAR" if c in {
            "business_date", "stay_date", "event_id", "order_id", "booking_id", "kind", "event_time", "arrived_at",
            "source", "store_id", "attributes", "raw_file", "room_id", "check_in", "check_out", "run_id", "payload_sha256"
        } else c + " BIGINT" for c in columns) + ")")
        con.executemany("INSERT INTO " + table + " VALUES (" + ",".join("?" for _ in columns) + ")",
                        [[r.get(c) for c in columns] for r in rows])
    else:
        con.execute("CREATE TABLE " + table + " (" + ", ".join(c + " VARCHAR" if c in {
            "business_date", "stay_date", "event_id", "order_id", "booking_id", "kind", "event_time", "arrived_at",
            "source", "store_id", "attributes", "raw_file", "room_id", "check_in", "check_out", "run_id", "payload_sha256"
        } else c + " BIGINT" for c in columns) + ")")


def _replace_dir(staging, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(target.name + ".old-" + uuid4().hex[:8])
    if target.exists():
        os.replace(target, backup)
    try:
        os.replace(staging, target)
    except Exception:
        if backup.exists():
            os.replace(backup, target)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def rebuild(root, dates=None, compact=True, split_files=False):
    root = Path(root)
    facts, nights, collected = all_facts(root)
    sales = [r for r in facts if r["kind"] in ("payment", "refund")]
    sales_dates = sorted({r["business_date"] for r in sales})
    hotel_dates = sorted({r["stay_date"] for r in nights})
    affected_sales = sorted(set(dates if dates is not None else sales_dates) & set(sales_dates))
    affected_hotel = sorted(set(dates if dates is not None else hotel_dates) & set(hotel_dates))
    staging_root = root / ".staging" / new_run_id("rebuild")
    staging_root.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    _insert_rows(con, "sales_facts", SALES_COLUMNS, sales)
    _insert_rows(con, "hotel_facts", HOTEL_COLUMNS, nights)
    written = []
    try:
        for day in affected_sales:
            rows = [r for r in sales if r["business_date"] == day]
            part = staging_root / "silver" / "sales" / ("business_date=" + day)
            part.mkdir(parents=True, exist_ok=True)
            if split_files:
                for row in rows:
                    name = "event-" + row["event_id"].replace("/", "_") + "-" + row["payload_sha256"][:10] + ".parquet"
                    con.execute("COPY (SELECT * FROM sales_facts WHERE event_id = " + sql_text(row["event_id"]) +
                                ") TO " + sql_text(part / name) + " (FORMAT PARQUET)")
            else:
                digest = sha256_bytes(canonical_bytes(sorted(rows, key=lambda r: r["event_id"])))[:16]
                con.execute("COPY (SELECT * FROM sales_facts WHERE business_date = " + sql_text(day) +
                            ") TO " + sql_text(part / ("data-" + digest + ".parquet")) + " (FORMAT PARQUET)")
            target = root / "silver" / "sales" / part.name
            _replace_dir(part, target)
            written.extend(p.relative_to(root).as_posix() for p in target.rglob("*.parquet"))
        for day in affected_hotel:
            rows = [r for r in nights if r["stay_date"] == day]
            part = staging_root / "silver" / "hotel" / ("stay_date=" + day)
            part.mkdir(parents=True, exist_ok=True)
            if split_files:
                for row in rows:
                    name = row["booking_id"] + "-" + row["stay_date"] + "-" + row["payload_sha256"][:10] + ".parquet"
                    con.execute("COPY (SELECT * FROM hotel_facts WHERE booking_id = " + sql_text(row["booking_id"]) +
                                " AND stay_date = " + sql_text(day) + ") TO " + sql_text(part / name) + " (FORMAT PARQUET)")
            else:
                digest = sha256_bytes(canonical_bytes(sorted(rows, key=lambda r: (r["booking_id"], r["stay_date"]))))[:16]
                con.execute("COPY (SELECT * FROM hotel_facts WHERE stay_date = " + sql_text(day) +
                            ") TO " + sql_text(part / ("data-" + digest + ".parquet")) + " (FORMAT PARQUET)")
            target = root / "silver" / "hotel" / part.name
            _replace_dir(part, target)
            written.extend(p.relative_to(root).as_posix() for p in target.rglob("*.parquet"))
    finally:
        con.close()
        shutil.rmtree(staging_root, ignore_errors=True)
    quality = dict(collected["counts"])
    quality["rejected_rows"] = [
        {"event_id": r["row"].get("event_id") if isinstance(r["row"], dict) else None,
         "raw_file": r["raw_file"], "raw_line": r["raw_line"], "run_id": r["run_id"],
         "reason": "；".join(r["reasons"]), "raw_row": r["row"]}
        for r in collected["classified"] if r["classification"] == "rejected"
    ]
    quality["duplicates"] = [
        {"event_id": r["row"].get("event_id"), "raw_file": r["raw_file"], "raw_line": r["raw_line"],
         "run_id": r["run_id"], "reason": "相同 event_id 与内容，重复投递"}
        for r in collected["classified"] if r["classification"] == "duplicate"
    ]
    quality["affected_partitions"] = {"sales": affected_sales, "hotel": affected_hotel}
    quality["written_files"] = sorted(written)
    json_write(root / "quality" / "latest.json", quality)
    return quality


def parquet_files(root, domain):
    base = Path(root) / "silver" / domain
    return sorted(p for p in base.rglob("*.parquet")) if base.exists() else []


def file_hashes(root, domain=None):
    bases = [Path(root) / "silver" / domain] if domain else [Path(root) / "silver"]
    return {p.relative_to(root).as_posix(): sha256_file(p)
            for base in bases if base.exists() for p in sorted(base.rglob("*.parquet"))}


def _read_table(root, domain):
    files = parquet_files(root, domain)
    if not files:
        return [], None
    con = duckdb.connect()
    pattern = (Path(root) / "silver" / domain / "*" / "*.parquet").as_posix()
    try:
        cur = con.execute("SELECT * FROM read_parquet(" + sql_text(pattern) + ", hive_partitioning=true, union_by_name=true)")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return rows, con
    except Exception:
        con.close()
        raise


def read_sales(root):
    rows, con = _read_table(root, "sales")
    if con:
        con.close()
    return rows


def read_hotel(root):
    rows, con = _read_table(root, "hotel")
    if con:
        con.close()
    return rows


def daily_reports(root):
    sales = read_sales(root)
    hotel = read_hotel(root)
    sales_daily = {}
    for row in sales:
        day = str(row["business_date"])
        sales_daily[day] = sales_daily.get(day, 0) + int(row["signed_cents"])
    hotel_daily = {}
    for row in hotel:
        day = str(row["stay_date"])
        hotel_daily[day] = hotel_daily.get(day, 0) + int(row["room_nights"])
    return dict(sorted(sales_daily.items())), dict(sorted(hotel_daily.items()))


def scan_evidence(root, day="2026-01-08", domain="sales"):
    files = parquet_files(root, domain)
    if not files:
        return {"scanning_files": "0/0", "total_files_read": 0, "filenames": [], "plan": "", "plan_json": {}}
    con = duckdb.connect()
    date_col = "business_date" if domain == "sales" else "stay_date"
    pattern = (Path(root) / "silver" / domain / "*" / "*.parquet").as_posix()
    con.execute("CREATE VIEW facts AS SELECT * FROM read_parquet(" + sql_text(pattern) + ", hive_partitioning=true, union_by_name=true)")
    plan_json = json.loads(con.execute("EXPLAIN (ANALYZE, FORMAT JSON) SELECT * FROM facts WHERE " + date_col +
                                      " = DATE " + sql_text(day)).fetchone()[1])
    con.close()
    scans = []
    def walk(node):
        info = node.get("extra_info", {})
        if "Scanning Files" in info:
            scans.append(info)
        for child in node.get("children", []):
            walk(child)
    walk(plan_json)
    filenames = []
    for info in scans:
        filenames.extend([x.strip() for x in str(info.get("Filename(s)", "")).splitlines() if x.strip()])
    return {
        "scanning_files": scans[0].get("Scanning Files", "0/0") if scans else "0/0",
        "total_files_read": int(scans[0].get("Total Files Read", 0)) if scans else 0,
        "filenames": filenames,
        "file_filters": scans[0].get("File Filters") if scans else None,
        "plan": json.dumps(plan_json, ensure_ascii=False, indent=2),
        "plan_json": plan_json,
    }


def wrong_totals(root):
    collected = collect(root)
    valid = [r["row"] for r in collected["classified"] if not validate_event(r["row"]) and r["row"].get("kind") in ("payment", "refund")]
    no_dedup = sum(r["amount_cents"] if r["kind"] == "payment" else -r["amount_cents"] for r in valid)
    by_order = {}
    for row in valid:
        by_order.setdefault(row["order_id"], row)
    order_dedup = sum(r["amount_cents"] if r["kind"] == "payment" else -r["amount_cents"] for r in by_order.values())
    return {"no_dedup_total": no_dedup, "order_id_dedup_total": order_dedup}


def lineage(root, event_id):
    facts = [r for r in read_sales(root) if r["event_id"] == event_id]
    nights = [r for r in read_hotel(root) if r["event_id"] == event_id]
    out = []
    for row in facts + nights:
        source = Path(root) / row["raw_file"]
        raw_rows = source.read_text(encoding="utf-8").splitlines()
        out.append({"fact": row, "source_row": json.loads(raw_rows[int(row["raw_line"]) - 1])})
    return out


def publish(root, version=None, force_fail=False):
    root = Path(root)
    version = version or new_run_id("report")
    quality = json_read(root / "quality" / "latest.json", {"counts": {}})
    counts = quality.get("counts", quality)
    sales, hotel = daily_reports(root)
    sales_unique = len({r["event_id"] for r in read_sales(root)}) == len(read_sales(root))
    hotel_keys = [(r["booking_id"], r["room_id"], str(r["stay_date"])) for r in read_hotel(root)]
    checks = {
        "row_conservation": counts.get("conserved") is True,
        "sales_event_id_unique": sales_unique,
        "hotel_grain_unique": len(hotel_keys) == len(set(hotel_keys)),
        "has_accepted_output": counts.get("accepted", 0) > 0 or counts.get("accepted", 0) == 0 and counts.get("total", 0) == 0,
        "no_unresolved_conflicts": counts.get("conflicts", 0) == 0,
        "publish_injection": not force_fail,
    }
    staging = root / "published" / "staging" / version
    staging.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        sales_files = parquet_files(root, "sales")
        hotel_files = parquet_files(root, "hotel")
        if sales_files:
            pattern = (root / "silver" / "sales" / "*" / "*.parquet").as_posix()
            con.execute("CREATE VIEW sales AS SELECT * FROM read_parquet(" + sql_text(pattern) + ", hive_partitioning=true, union_by_name=true)")
            con.execute("COPY (SELECT business_date, SUM(signed_cents) AS net_cents FROM sales GROUP BY business_date ORDER BY business_date) TO " +
                        sql_text(staging / "sales_daily.parquet") + " (FORMAT PARQUET)")
        if hotel_files:
            pattern = (root / "silver" / "hotel" / "*" / "*.parquet").as_posix()
            con.execute("CREATE VIEW hotel AS SELECT * FROM read_parquet(" + sql_text(pattern) + ", hive_partitioning=true, union_by_name=true)")
            con.execute("COPY (SELECT stay_date, SUM(room_nights) AS room_nights FROM hotel GROUP BY stay_date ORDER BY stay_date) TO " +
                        sql_text(staging / "hotel_nights.parquet") + " (FORMAT PARQUET)")
    finally:
        con.close()
    manifest = {
        "version": version, "created_at": datetime.now(timezone.utc).isoformat(), "checks": checks,
        "quality_passed": all(checks.values()), "sales_daily": sales, "hotel_nights": hotel,
        "input_manifests": [m["manifest_file"] + ":" + m["raw_sha256"] for m in confirmed_manifests(root)],
        "silver_files": file_hashes(root), "quality": quality,
    }
    json_write(staging / "report_manifest.json", manifest)
    json_write(staging / "reconciliation.json", {"sales_daily": sales, "sales_total": sum(sales.values()),
                                                  "hotel_nights": hotel, "hotel_total": sum(hotel.values())})
    json_write(staging / "lineage.json", {
        "sales": [{k: r[k] for k in ("event_id", "raw_file", "raw_line", "run_id", "payload_sha256")} for r in read_sales(root)],
        "hotel": [{k: r[k] for k in ("event_id", "booking_id", "raw_file", "raw_line", "run_id")} for r in read_hotel(root)],
    })
    if not all(checks.values()):
        return dict(manifest, published=False, current_version=current_version(root), staging=str(staging))
    reports = root / "published" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    target = reports / version
    if target.exists():
        shutil.rmtree(target)
    os.replace(staging, target)
    pointer = root / "published" / "CURRENT"
    old = current_version(root)
    previous = root / "published" / "PREVIOUS"
    if old:
        json_write(previous, {"version": old, "saved_at": datetime.now(timezone.utc).isoformat()})
    tmp = pointer.with_name("CURRENT.tmp-" + uuid4().hex[:8])
    tmp.write_text(version, encoding="utf-8")
    os.replace(tmp, pointer)
    return dict(manifest, published=True, previous_version=old, current_version=version, report_dir=str(target))


def current_version(root):
    path = Path(root) / "published" / "CURRENT"
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def read_published(root, version=None):
    root = Path(root)
    version = version or current_version(root)
    if not version:
        return None
    directory = root / "published" / "reports" / version
    manifest = json_read(directory / "report_manifest.json")
    if not manifest:
        return None
    con = duckdb.connect()
    def rows(name):
        path = directory / name
        if not path.exists():
            return []
        cur = con.execute("SELECT * FROM read_parquet(" + sql_text(path.as_posix()) + ")")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    try:
        sales_rows = rows("sales_daily.parquet")
        hotel_rows = rows("hotel_nights.parquet")
    finally:
        con.close()
    return {"version": version, "manifest": manifest, "sales_rows": sales_rows, "hotel_rows": hotel_rows,
            "reconciliation": json_read(directory / "reconciliation.json")}


def rollback(root):
    root = Path(root)
    previous = json_read(root / "published" / "PREVIOUS")
    current = current_version(root)
    if not previous or not current:
        raise RuntimeError("没有可回退的已发布版本")
    pointer = root / "published" / "CURRENT"
    tmp = pointer.with_name("CURRENT.tmp-" + uuid4().hex[:8])
    tmp.write_text(previous["version"], encoding="utf-8")
    os.replace(tmp, pointer)
    json_write(root / "published" / "PREVIOUS", {"version": current, "saved_at": datetime.now(timezone.utc).isoformat()})
    return {"from": current, "to": previous["version"]}


def backfill(root, dates):
    root = Path(root)
    days = sorted(set(dates))
    before = file_hashes(root)
    quality = rebuild(root, dates=days)
    result = publish(root, new_run_id("backfill"))
    after = file_hashes(root)
    unaffected = {p: h for p, h in before.items() if not any(("=" + d + "/") in p for d in days)}
    changed_unaffected = [p for p, h in unaffected.items() if after.get(p) != h]
    return {"dates": days, "quality": quality, "publish": result,
            "unaffected_files_unchanged": not changed_unaffected, "changed_unaffected": changed_unaffected,
            "before": before, "after": after}


def run_pipeline(root, rows, run_id=None, compact=True, split_files=False):
    ingestion = write_raw_batch(root, rows, run_id=run_id)
    quality = rebuild(root, compact=compact, split_files=split_files)
    published = publish(root)
    return {"ingestion": ingestion, "quality": quality, "publish": published,
            "reports": read_published(root), "scan": scan_evidence(root)}

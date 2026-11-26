"""DL01–DL12 的真实本地数据湖引擎；复用课程统一 case/step 回放结构。"""
import json
import os
import re
from copy import deepcopy
from pathlib import Path

from .course_engines import case, step, ENGINES
from lake_project import acceptance, core, exercises
from lake_project.fixtures import (bad_batch, baseline_batch, conflict_batch, hotel_batch,
                                   late_batch, nullable_batch, sales_batch, utc_batch)


def _rows_for(kind, scenario):
    if kind in ("grain", "dedup"):
        rows = sales_batch()
        if scenario == "wrong":
            rows += conflict_batch() + bad_batch()
        return rows
    if kind == "hotel":
        rows = hotel_batch()
        if scenario == "wrong":
            invalid = deepcopy(rows[0])
            invalid["event_id"] = "h-bad"
            invalid["payload"]["check_out"] = invalid["payload"]["check_in"]
            rows.append(invalid)
        return rows
    if kind == "incremental":
        rows = baseline_batch() + late_batch()
        if scenario in ("boundary", "wrong"):
            retry = deepcopy(rows[0])
            retry["arrived_at"] = "2026-01-09T00:00:02+08:00"
            rows.append(retry)
        if scenario == "wrong":
            rows += bad_batch()
        return rows
    if kind == "business_date":
        rows = baseline_batch() + utc_batch()
        if scenario == "wrong":
            rows += late_batch()
        return rows
    if kind in ("partition_scan", "report_scan", "metric_lineage"):
        rows = sales_batch() + hotel_batch()
        if scenario == "wrong":
            rows += late_batch()
        return rows
    if kind in ("publish", "capstone_files"):
        return sales_batch() + hotel_batch()
    return sales_batch() + hotel_batch()


def _raw_snapshot(rows):
    return [{"raw_line": i + 1, "event_id": r.get("event_id"), "kind": r.get("kind"),
             "amount_cents": r.get("amount_cents"), "event_time": r.get("event_time"),
             "arrived_at": r.get("arrived_at")} for i, r in enumerate(rows)]


def _base_extra(root, rows):
    collected = core.collect(root) if core.confirmed_manifests(root) else None
    return {
        "input": _raw_snapshot(rows),
        "raw_files": [m["raw_file"] + " sha256=" + m["raw_sha256"] for m in core.confirmed_manifests(root)],
        "classification": collected["counts"] if collected else {},
        "rejections": [{"event_id": r["row"].get("event_id"), "raw_line": r["raw_line"],
                        "reason": "；".join(r["reasons"])} for r in collected["classified"]
                       if r["classification"] == "rejected"] if collected else [],
        "duplicates": [{"event_id": r["row"].get("event_id"), "raw_line": r["raw_line"]}
                       for r in collected["classified"] if r["classification"] == "duplicate"] if collected else [],
        "partitions": sorted(p.relative_to(root).as_posix() for p in (root / "silver").rglob("*.parquet")),
        "architecture": ["原始事件", "校验/隔离/去重", "Parquet 分区", "独立连接日报/对账"],
    }


def _case(name, passed, expected, actual, error, steps, extra, sql=None):
    return case(name, passed, expected=expected, actual=actual, error=error, steps=steps, extra=extra)


def _plan_scan(plan, total_files=None):
    if not plan:
        return {"scanning_files": "0/0", "total_files_read": 0, "filenames": []}
    data = json.loads(plan)
    found = []
    def walk(node):
        info = node.get("extra_info", {})
        if "Scanning Files" in info or info.get("Function") == "READ_PARQUET":
            found.append(info)
        for child in node.get("children", []):
            walk(child)
    walk(data)
    info = next((item for item in found if "Scanning Files" in item), found[0] if found else {})
    total_read = sum(int(item.get("Total Files Read", 0)) for item in found)
    scanning = info.get("Scanning Files") or (f"{total_read}/{total_files}" if total_files is not None else "0/0")
    return {"scanning_files": scanning, "total_files_read": total_read,
            "filenames": info.get("Filename(s)", ""), "file_filters": info.get("File Filters"),
            "plan": plan}


def _grain(root, code, scenario):
    rows = _rows_for("grain", scenario)
    core.write_raw_batch(root, rows)
    result = exercises.run_sql(code, root, raw=True)
    value = result["rows"][0][0] if result["rows"] and result["rows"][0] else None
    passed = result["error"] is None and value == 3
    steps = [step("真实输入 · 4 行 JSONL", "input", {"raw_events": _raw_snapshot(rows)}, note="p1 与 r1 同订单但不同事件。", actor="采集器"),
             step("执行学生 SQL", "read", {"查询结果": [dict(zip(result["columns"], r)) for r in result["rows"]]}, code,
                  result["error"] or "COUNT(DISTINCT event_id) 应得到 3。", "学生 SQL")]
    extra = _base_extra(root, rows)
    extra["output"] = result
    return [_case("销售事件粒度为 event_id", passed, 3, value, result["error"] or (None if passed else "order_id 只有 2 个，不能代表 3 笔事件。"), steps, extra)]


def _hotel(root, code, scenario):
    rows = _rows_for("hotel", scenario)
    core.write_raw_batch(root, rows)
    result = exercises.run_sql(code, root, raw=True)
    expected = [["2026-01-07", 1], ["2026-01-08", 2], ["2026-01-09", 1]]
    passed = result["error"] is None and result["rows"] == expected
    steps = [step("真实输入 · 两笔预订", "input", {"raw_events": _raw_snapshot(rows)}, note="7→9 与 8→10，退房当天不计。", actor="采集器"),
             step("展开逐晚事实", "read", {"房晚": [dict(zip(result["columns"], r)) for r in result["rows"]]}, code,
                  result["error"] or "预期 1/2/1，共 4 间夜。", "学生 SQL")]
    extra = _base_extra(root, rows)
    extra["output"] = result
    return [_case("酒店逐晚粒度与退房边界", passed, expected, result["rows"], result["error"] or (None if passed else "预订行数 2 不等于间夜数 4。"), steps, extra)]


def _incremental(root, code, scenario):
    rows = _rows_for("incremental", scenario)
    args = {"rows": rows, "watermark": "2026-01-08T23:59:59+08:00"}
    result = exercises.run_python(code, root, args=args)
    expected = ["r1", "p3"] if scenario == "normal" else ["r1", "p3", "p1"]
    actual = (result["result"] or {}).get("event_ids")
    passed = result["ok"] and actual == expected
    steps = [step("全量历史与到达水位", "input", {"raw_events": _raw_snapshot(rows)}, note="watermark=" + args["watermark"], actor="采集器"),
             step("执行学生增量函数", "read", {"选中事件": [{"event_id": x} for x in actual or []]}, code,
                  result["stderr"] or "增量只按 arrived_at 过滤；重复允许传到下游。", "学生 Python")]
    extra = _base_extra(root, rows)
    extra["output"] = result
    return [_case("增量不丢迟到事件", passed, expected, actual, result["stderr"] or (None if passed else "全量重发或水位字段用错。"), steps, extra)]


def _dedup(root, code, scenario):
    rows = _rows_for("dedup", scenario)
    ingestion = core.write_raw_batch(root, rows)
    result = exercises.run_sql(code, root, raw=True)
    records = [dict(zip(result["columns"], r)) for r in result["rows"]]
    ids = [r.get("event_id") for r in records]
    total = sum((-r["amount_cents"] if r.get("kind") == "refund" else r["amount_cents"])
                for r in records if isinstance(r.get("amount_cents"), int))
    passed = result["error"] is None and sorted(ids) == ["p1", "p2", "r1"] and total == 5000
    collected = core.collect(root)
    steps = [step("校验和与原始行", "input", {"raw_events": _raw_snapshot(rows)},
                  note="SHA-256=" + ingestion["raw_sha256"], actor="采集器"),
             step("分类：接受/重复/拒收", "validate", {"分类": collected["classified"]},
                  note=json.dumps(collected["counts"], ensure_ascii=False), actor="校验器"),
             step("执行学生去重 SQL", "read", {"去重结果": records}, code,
                  result["error"] or "p1/p2/r1 三行；净额 5000。", "学生 SQL")]
    extra = _base_extra(root, rows)
    extra["output"] = result
    extra["dedup_total"] = total
    return [_case("按 event_id 去重并保留退款", passed, {"event_ids": ["p1", "p2", "r1"], "net_cents": 5000},
                  {"event_ids": ids, "net_cents": total}, result["error"] or (None if passed else "错误解会得到 14900 分，或按 order_id 吞掉退款。"), steps, extra)]


def _business_date(root, code, scenario):
    rows = _rows_for("business_date", scenario)
    core.write_raw_batch(root, rows)
    result = exercises.run_sql(code, root, raw=True)
    actual = {r[0]: r[1] for r in result["rows"]}
    expected = {}
    for row in rows:
        if row.get("kind") in ("payment", "refund") and not core.validate_event(row):
            expected[row["event_id"]] = core.business_date(row["event_time"])
    passed = result["error"] is None and actual == expected
    steps = [step("带偏移的真实事件时间", "input", {"raw_events": _raw_snapshot(rows)}, note="会话时区 UTC，目标时区 Asia/Shanghai。", actor="采集器"),
             step("派生业务日", "read", {"业务日": [dict(zip(result["columns"], r)) for r in result["rows"]]}, code,
                  result["error"] or "UTC 16:30 → 上海次日 00:30。", "学生 SQL")]
    extra = _base_extra(root, rows)
    extra["output"] = result
    return [_case("业务日按时区映射", passed, expected, actual, result["error"] or (None if passed else "直接 CAST(event_time AS DATE) 会使用 UTC 日期。"), steps, extra)]


def _partition_scan(root, code, scenario):
    rows = _rows_for("partition_scan", scenario)
    out = core.run_pipeline(root, rows)
    result = exercises.run_sql(code, root, analyze=True)
    value = result["rows"][0][0] if result["rows"] and result["rows"][0] else None
    scan = _plan_scan(result["plan"], len(extra_files(root)))
    expected_scan = "1/3" if scenario == "wrong" else "1/2"
    passed = result["error"] is None and value == -4900 and scan["scanning_files"] == expected_scan
    steps = [step("真实输入与去重", "input", {"raw_events": _raw_snapshot(rows)}, note=json.dumps(out["quality"], ensure_ascii=False)[:500], actor="采集器"),
             step("写业务日期分区", "committed", {"Parquet 文件": [{"file": p} for p in extra_files(root)]}, note="一个业务日一个目录。", actor="DuckDB"),
             step("独立连接执行并分析", "read", {"查询结果": [dict(zip(result["columns"], r)) for r in result["rows"]]}, code,
                  result["error"] or "Scanning Files: " + scan["scanning_files"], "独立连接")]
    extra = _base_extra(root, rows)
    extra.update({"output": result, "scan": scan, "reconciliation": out["reports"]["reconciliation"]})
    return [_case("8 日查询值与分区裁剪", passed, {"net_cents": -4900, "scanning_files": expected_scan},
                  {"net_cents": value, "scanning_files": scan["scanning_files"]},
                  result["error"] or (None if passed else "查询扫了全部分区，或金额/日期口径不对。"), steps, extra)]


def extra_files(root):
    return sorted(p.relative_to(root).as_posix() for p in core.parquet_files(root, "sales"))


def _report_scan(root, code, scenario):
    rows = _rows_for("report_scan", scenario)
    out = core.run_pipeline(root, rows, split_files=(scenario == "boundary"))
    result = exercises.run_sql(code, root, analyze=True)
    expected = [[day, amount] for day, amount in out["reports"]["reconciliation"]["sales_daily"].items()]
    passed = result["error"] is None and result["rows"] == expected
    steps = [step("真实输入", "input", {"raw_events": _raw_snapshot(rows)}, actor="采集器"),
             step("写 Parquet（边界场景为逐事件小文件）", "committed", {"Parquet 文件": [{"file": p} for p in extra_files(root)]},
                  note="正常 1 文件/分区；边界逐事件小文件。", actor="DuckDB"),
             step("执行日报 SQL", "read", {"日报": [dict(zip(result["columns"], r)) for r in result["rows"]]}, code,
                  result["error"] or "退款使用 signed_cents。", "学生 SQL")]
    extra = _base_extra(root, rows)
    extra.update({"output": result, "scan": _plan_scan(result["plan"], len(extra_files(root))), "file_count": len(extra_files(root)),
                  "reconciliation": out["reports"]["reconciliation"]})
    return [_case("日报金额符号与小文件对照", passed, expected, result["rows"],
                  result["error"] or (None if passed else "把 refund 当正数会得到错误日报。"), steps, extra)]


def _metric_lineage(root, code, scenario):
    rows = _rows_for("metric_lineage", scenario)
    out = core.run_pipeline(root, rows)
    result = exercises.run_sql(code, root)
    expected = [[day, amount] for day, amount in out["reports"]["reconciliation"]["sales_daily"].items()]
    lineage = core.lineage(root, "r1")
    passed = result["error"] is None and result["rows"] == expected and bool(lineage)
    steps = [step("真实输入 · 退款 8 日发生/9 日到达", "input", {"raw_events": _raw_snapshot(rows)}, actor="采集器"),
             step("执行指标 SQL", "read", {"日报": [dict(zip(result["columns"], r)) for r in result["rows"]]}, code,
                  result["error"] or "口径使用 business_date。", "学生 SQL"),
             step("从 r1 回到源记录", "read", {"lineage": lineage}, note="raw_file + raw_line + run_id。", actor="血缘")]
    extra = _base_extra(root, rows)
    extra.update({"output": result, "lineage": lineage, "reconciliation": out["reports"]["reconciliation"]})
    return [_case("发生日口径与行级血缘", passed, expected, result["rows"],
                  result["error"] or (None if passed else "按 arrived_at 分组会使总额不变但分日错误。"), steps, extra)]


def _publish(root, code, scenario):
    old = core.run_pipeline(root, sales_batch() + hotel_batch())
    old_version = old["publish"]["version"]
    if scenario == "wrong":
        core.write_raw_batch(root, conflict_batch())
    result = exercises.run_python(code, root, env_extra={"LAKE_FORCE_FAIL": "1" if scenario == "boundary" else "0"})
    current = core.current_version(root)
    published = core.read_published(root)
    if scenario == "normal":
        passed = result["ok"] and current not in (None, old_version, "unchecked") and published is not None
        expected = "检查通过后发布新版本"
    else:
        passed = result["ok"] and current == old_version and published is not None
        expected = "发布失败，CURRENT 仍为 " + old_version
    steps = [step("已有有效版本", "committed", {"CURRENT": [{"version": old_version}]}, actor="发布器"),
             step("执行学生暂存/检查/发布", "pending", {"CURRENT": [{"version": current}], "staging": [{"path": str(root / "published" / "staging")}]}, code,
                  result["stderr"] or "checked=True 才会执行质量检查。", "学生 Python"),
             step("独立连接读回当前版本", "read", {"报表": published["reconciliation"] if published else {}},
                  note="旧版可读性由独立 DuckDB 连接验证。", actor="读者")]
    extra = _base_extra(root, sales_batch() + hotel_batch())
    extra.update({"output": result, "old_version": old_version, "current_version": current,
                  "checks": (result["result"] or {}).get("checks"), "published_report": published})
    return [_case("质量门禁与原子发布", passed, expected, current, result["stderr"] or (None if passed else "未检查就切指针，或失败时丢了旧版。"), steps, extra)]


def _capstone(root, code, scenario, files, stage=5):
    files = {k + suffix: v for k, suffix in {"service": ".py", "transform": ".sql", "report": ".sql", "tests": ".py"}.items()
             for v in [(files or {}).get(k, "")]}
    stage = max(1, min(5, int(stage or 5)))
    rows = sales_batch() + hotel_batch()
    input_path = root / "input.jsonl"
    input_path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    result = exercises.run_python(code, root, files=files)
    transform = exercises.run_sql(files.get("transform.sql", ""), root, analyze=(stage == 3))
    report = exercises.run_sql(files.get("report.sql", ""), root)
    tests = exercises.run_python(files.get("tests.py", ""), root, files={"tests.py": files.get("tests.py", "")})
    published = core.read_published(root)
    collected = core.collect(root) if core.confirmed_manifests(root) else {"counts": {}}
    expected_sales = [[day, amount] for day, amount in acceptance.EXPECTED_SALES_DAILY.items()]
    expected_hotel = [[day, amount] for day, amount in acceptance.EXPECTED_HOTEL_NIGHTS.items()]
    scan = _plan_scan(transform["plan"], len(extra_files(root))) if stage == 3 else None
    checks = {
        "四个文件真实写入": all((root / name).exists() for name in ("service.py", "transform.sql", "report.sql", "tests.py")),
    }
    if stage == 1:
        checks.update({
            "采集代码执行": result["ok"],
            "原始批次已确认": len(core.confirmed_manifests(root)) == 1,
            "原始输入 6 行": len(core.load_raw(root)) == 6,
        })
    elif stage == 2:
        counts = collected["counts"]
        checks.update({
            "采集代码执行": result["ok"],
            "互斥分类 5/1/0": (counts.get("accepted"), counts.get("duplicate"), counts.get("rejected")) == (5, 1, 0),
            "行数守恒": counts.get("conserved") is True,
            "销售与酒店分区落盘": len(core.parquet_files(root, "sales")) == 2 and len(core.parquet_files(root, "hotel")) == 3,
        })
    elif stage == 3:
        checks.update({
            "采集发布代码执行": result["ok"] and published is not None,
            "单日净额 -4900": transform["error"] is None and transform["rows"] == [[-4900]],
            "分区裁剪 1/2": bool(scan and scan["scanning_files"] == "1/2" and scan["total_files_read"] == 1),
        })
    elif stage == 4:
        checks.update({
            "采集发布代码执行": result["ok"] and published is not None,
            "销售日报": transform["error"] is None and transform["rows"] == expected_sales,
            "酒店间夜": report["error"] is None and report["rows"] == expected_hotel,
            "独立发布报表对账": published is not None and published["reconciliation"]["sales_daily"] == acceptance.EXPECTED_SALES_DAILY
                and published["reconciliation"]["hotel_nights"] == acceptance.EXPECTED_HOTEL_NIGHTS,
        })
    else:
        checks.update({
            "采集发布代码执行": result["ok"] and published is not None,
            "销售计算 SQL": transform["error"] is None and transform["rows"] == expected_sales,
            "酒店报表 SQL": report["error"] is None and report["rows"] == expected_hotel,
            "独立测试": tests["ok"] and bool(tests["result"]) and all(tests["result"].values()),
        })
    passed = all(checks.values())
    steps = [step("空目录与 input.jsonl", "input", {"raw_events": _raw_snapshot(rows)}, note=f"阶段 {stage}/5；四个文件从空目录开始。", actor="学生"),
             step("执行 service.py", "committed", {"发布版本": [{"version": core.current_version(root)}]}, files.get("service.py", ""),
                  result["stderr"] or "按当前阶段采集、校验或发布。", "学生 Python"),
             step("执行 transform.sql", "read", {"销售": [dict(zip(transform["columns"], r)) for r in transform["rows"]]}, files.get("transform.sql", ""),
                  transform["error"] or ("Scanning Files: " + scan["scanning_files"] if scan else "销售日报或单日查询。"), "学生 SQL"),
             step("执行 report.sql 与 tests.py", "read", {"酒店": [dict(zip(report["columns"], r)) for r in report["rows"]], "测试": tests["result"]},
                  files.get("report.sql", ""), tests["stderr"], "学生")]
    extra = _base_extra(root, rows)
    extra.update({"output": result, "transform": transform, "report": report, "tests": tests, "checks": checks,
                  "published_report": published, "stage": stage, "scan": scan,
                  "classification": collected.get("counts", {})})
    return [_case(f"DL11 阶段 {stage}/5", passed, checks, {k: v for k, v in checks.items()},
                  None if passed else "当前阶段证据不完整；按阶段目标补齐代码后重跑。", steps, extra)]


def _fault(root, code, scenario):
    mode = "publish_fail" if scenario == "normal" else scenario
    old = None
    before = None
    if mode != "crash":
        old = core.run_pipeline(root, sales_batch() + hotel_batch())["publish"]["version"]
        before = core.read_published(root)
    result = exercises.run_python(code, root, args=mode)
    ok = bool((result["result"] or {}).get("ok"))
    recovery = (result["result"] or {}).get("evidence") or {}
    published = core.read_published(root)
    if mode == "crash":
        independent = published is not None and published["reconciliation"]["sales_total"] == 5000 \
            and recovery.get("first_confirmed") is False and recovery.get("raw_files") == 1
        expected = "确认前退出后重投，净额仍为 5000"
    elif mode == "conflict":
        independent = core.current_version(root) == old and published == before and core.collect(root)["counts"]["conflicts"] == 1 \
            and recovery.get("failed_published") is False and recovery.get("old_readable") is True
        expected = "冲突隔离，当前版本仍为旧版且旧报表不变"
    elif mode == "publish_fail":
        independent = core.current_version(root) not in (None, old) and published is not None \
            and recovery.get("failed_published") is False and recovery.get("old_readable") is True \
            and recovery.get("recovered") is True and recovery.get("new_version") == core.current_version(root)
        expected = "发布失败时旧版可读，重新发布后 CURRENT 前进"
    else:
        independent = published is not None and published["reconciliation"]["sales_daily"].get("2026-01-06") == 1200 \
            and recovery.get("unaffected_files_unchanged") is True
        expected = "只回填 2026-01-06，其他日期不变"
    passed = result["ok"] and ok and independent
    steps = [step("故障场景 " + mode, "input", {"raw_events": _raw_snapshot(_rows_for("fault", "normal"))}, actor="故障注入"),
             step("学生执行恢复代码", "pending", {"CURRENT": [{"version": core.current_version(root)}]}, code,
                  result["stderr"] or "平台不只相信 ok，继续独立复核。", "学生 Python"),
             step("独立连接复核", "read", {"报表": published["reconciliation"] if published else {}, "恢复证据": recovery},
                  note=expected, actor="验收器")]
    extra = _base_extra(root, _rows_for("fault", "normal"))
    extra.update({"output": result, "mode": mode, "independent_check": independent, "recovery": recovery,
                  "old_version": old, "current_version": core.current_version(root), "published_report": published})
    return [_case("故障恢复独立验收", passed, expected, {"student_ok": ok, "independent_ok": independent},
                  result["stderr"] or (None if passed else "故障没有真正发生，或恢复证据不完整。"), steps, extra)]


def _pg_outbox(root, code, scenario):
    dsn = os.getenv("LAKE_PG_DSN")
    if not dsn:
        return [case("真实 PostgreSQL outbox", None, expected="配置 LAKE_PG_DSN 后运行",
                     actual="跳过：未配置隔离教学库", steps=[step("依赖检查", "marker", {})],
                     extra=dict(not_measured="跳过：未配置 LAKE_PG_DSN。L0 DuckDB 主线不受影响，不计失败也不计通过。"))]
    import psycopg
    from uuid import uuid4
    schema = "lake_dl04_" + uuid4().hex[:10]
    steps = []
    first = second = None
    try:
        with psycopg.connect(dsn, autocommit=True) as con:
            con.execute("CREATE SCHEMA " + schema)
            con.execute("CREATE TABLE " + schema + ".outbox(event_id text PRIMARY KEY, payload jsonb NOT NULL, published boolean NOT NULL DEFAULT false)")
            for row in baseline_batch():
                con.execute("INSERT INTO " + schema + ".outbox(event_id,payload) VALUES (%s,%s)", (row["event_id"], json.dumps(row)))
        steps.append(step("真实 PG 隔离 schema 与 outbox", "committed", {"outbox": _raw_snapshot(baseline_batch())},
                          note="schema=" + schema, actor="PostgreSQL"))
        env = {"LAKE_PG_DSN": dsn, "LAKE_SCHEMA": schema}
        first = exercises.run_python(code, root, env_extra=dict(env, LAKE_CRASH_AFTER_RAW="1"))
        steps.append(step("第一次 worker：写文件后确认前退出", "error", {}, code,
                          "exit_code=" + str(first["exit_code"]), "worker 1"))
        second = exercises.run_python(code, root, env_extra=dict(env, LAKE_CRASH_AFTER_RAW="0"))
        core.rebuild(root)
        published = core.publish(root)
        report = core.read_published(root)
        with psycopg.connect(dsn, autocommit=True) as con:
            acked = con.execute("SELECT count(*) FROM " + schema + ".outbox WHERE published").fetchone()[0]
        passed = first["exit_code"] == 73 and second["ok"] and acked == 3 and report is not None \
            and report["reconciliation"]["sales_total"] == 5000
        steps.append(step("重启重投并独立读回报表", "read", {"outbox_acked": [{"count": acked}], "报表": report["reconciliation"] if report else {}},
                          note="消息可重复，结果不可重复。", actor="验收器"))
        return [_case("真实 outbox 重投与幂等", passed, "3 条确认，净额 5000", "acked=" + str(acked),
                      None if passed else "确认顺序错误导致丢事件，或报表未幂等。", steps,
                      dict(first=first, second=second, schema=schema, report=report))]
    except Exception as exc:
        return [case("真实 PostgreSQL outbox", False, error=str(exc), steps=steps, extra=dict(schema=schema))]
    finally:
        try:
            with psycopg.connect(dsn, autocommit=True) as con:
                con.execute("DROP SCHEMA " + schema + " CASCADE")
        except Exception:
            pass


def run(lesson, code, run_dir, choice="", scenario="", files=None, stage=None):
    root = Path(run_dir) / "lake"
    root.mkdir(parents=True, exist_ok=True)
    kind = lesson["lake_case"]
    scenario = scenario or "normal"
    if kind == "grain":
        cases = _grain(root, code, scenario)
    elif kind == "hotel":
        cases = _hotel(root, code, scenario)
    elif kind == "incremental":
        cases = _incremental(root, code, scenario)
    elif kind == "pg_outbox":
        cases = _pg_outbox(root, code, scenario)
    elif kind == "dedup":
        cases = _dedup(root, code, scenario)
    elif kind == "business_date":
        cases = _business_date(root, code, scenario)
    elif kind == "partition_scan":
        cases = _partition_scan(root, code, scenario)
    elif kind == "report_scan":
        cases = _report_scan(root, code, scenario)
    elif kind == "metric_lineage":
        cases = _metric_lineage(root, code, scenario)
    elif kind == "publish":
        cases = _publish(root, code, scenario)
    elif kind == "capstone_files":
        cases = _capstone(root, code, scenario, files or {}, stage or 5)
    elif kind == "fault_acceptance":
        cases = _fault(root, code, scenario)
    else:
        raise ValueError("unknown lake case: " + kind)
    return cases


ENGINES["lake"] = run

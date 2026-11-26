"""进阶项目 · 订单与退款数据湖（DL01–DL12）的课程契约与起始代码。"""
CHAPTER = "进阶项目 → 订单与退款数据湖"
SCHEMA = ("raw_events(event_id, order_id/booking_id, kind, amount_cents, event_time, arrived_at, source, schema_version, payload) → "
          "sales_facts(business_date, event_id, signed_cents, raw_file, raw_line, run_id) / "
          "hotel_nights(stay_date, booking_id, room_id, room_nights)")
EXAMPLE = {"name": "手算基线（合成数据）", "columns": ["事件", "业务日", "金额/间夜", "说明"],
           "rows": [["p1 付款", "2026-01-07", "9900 分", "第一条付款重复投递一次"],
                    ["p2 付款", "2026-01-08", "5000 分", "第二笔付款"],
                    ["r1 退款", "2026-01-08", "-9900 分", "8 日发生、9 日到达"],
                    ["酒店 b1/b2", "7/8/9 日", "1/2/1 间夜", "退房当天不计"]]}
SCENARIOS = [
    {"id": "normal", "name": "正常", "description": "固定小样本；期望独立写死。"},
    {"id": "boundary", "name": "边界", "description": "重复、跨时区、迟到或小文件边界。"},
    {"id": "wrong", "name": "错解/坏输入", "description": "加入冲突、坏行或错误口径，参考解仍应正确处理。"},
]


def L(id, title, summary, goal, lake_case, editor_lang, starter, solution, hint, principle, predict, scenarios=None, minutes=6, build_presets=None, contract="", lake_stages=None, prerequisites=None):
    return {
        "id": id, "title": "进阶项目 · " + title, "summary": summary, "goal": goal,
        "schema": SCHEMA, "starter": starter, "hint": hint, "solution": solution,
        "mode": "sql", "problem": None, "chapter": CHAPTER, "engine": "lake", "lake_case": lake_case,
        "editor_lang": editor_lang, "predict": predict, "principle": principle, "example": EXAMPLE,
        "scenarios": scenarios or SCENARIOS, "choices": [], "txn_mode": None,
        "contract": contract or "真实本地运行：Python + DuckDB + Parquet。输入/输出/拒绝原因/执行计划均来自本次运行；未配置环境会明确跳过。",
        "build_presets": build_presets, "lake_stages": lake_stages, "prerequisites": prerequisites or [], "minutes": minutes, "guide_kind": "lake", "guide_stage": lake_case,
    }


PY_PRINT = '''import json, os
from lake_project.student import stage_and_publish
print("LAKE_RESULT=" + json.dumps(stage_and_publish(
    os.environ["LAKE_ROOT"], checked=True,
    force_fail=os.getenv("LAKE_FORCE_FAIL") == "1")))
'''

DL11_SERVICE_STAGE1 = '''import json, os
from pathlib import Path
from lake_project import core
from lake_project.student import read_jsonl

root = Path(os.environ["LAKE_ROOT"])
rows = read_jsonl(root / "input.jsonl")
# TODO: 把 rows 写入不可变原始层，并打印 raw_file 与 input_rows。
ingest = None
print("LAKE_RESULT=" + json.dumps({"raw_file": None, "input_rows": len(rows)}, default=str))
'''

DL11_SERVICE_STAGE2 = '''import json, os
from pathlib import Path
from lake_project import core
from lake_project.student import read_jsonl

root = Path(os.environ["LAKE_ROOT"])
rows = read_jsonl(root / "input.jsonl")
ingest = core.write_raw_batch(root, rows)
# TODO: rebuild 后打印 accepted/duplicate/rejected，核对 5/1/0。
quality = None
print("LAKE_RESULT=" + json.dumps({"ingest": ingest, "quality": quality}, default=str))
'''

DL11_SERVICE = '''import json, os
from pathlib import Path
from lake_project import core
from lake_project.student import read_jsonl

root = Path(os.environ["LAKE_ROOT"])
rows = read_jsonl(root / "input.jsonl")
# 原始层内容寻址；同批重跑不会覆盖文件。
ingest = core.write_raw_batch(root, rows)
# 校验、隔离、event_id 去重后写销售与酒店分区。
quality = core.rebuild(root)
# 独立读者只会看到通过质量门禁后切换的 CURRENT。
report = core.publish(root)
print("LAKE_RESULT=" + json.dumps({
    "ingest": ingest, "quality": quality, "published": report["published"],
    "version": core.current_version(root)}, default=str))
'''

DL11_TRANSFORM_STAGE3 = '''-- TODO: 只查 2026-01-08，让执行计划证明 Scanning Files: 1/2。
SELECT SUM(signed_cents) AS net_cents
FROM facts;
'''
DL11_TRANSFORM_STAGE4 = '''-- TODO: 按业务日输出销售日报，退款已由 signed_cents 记为负数。
SELECT business_date, SUM(amount_cents) AS net_cents
FROM facts
GROUP BY business_date
ORDER BY business_date;
'''
DL11_TRANSFORM = '''SELECT business_date, SUM(signed_cents) AS net_cents
FROM facts
GROUP BY business_date
ORDER BY business_date;
'''
DL11_REPORT_STAGE4 = '''-- TODO: 酒店按 stay_date 汇总间夜，预期 7/8/9 日为 1/2/1。
SELECT 'TODO' AS stay_date, 0 AS room_nights;
'''
DL11_REPORT = '''SELECT stay_date, SUM(room_nights) AS room_nights
FROM hotel_nights
GROUP BY stay_date
ORDER BY stay_date;
'''
DL11_TESTS_STAGE5 = '''import json, os
from lake_project import acceptance, core

# TODO: 用独立常量核对日报、总额和酒店间夜；不要用待测 SQL 生成期望。
report = core.read_published(os.environ["LAKE_ROOT"])
print("LAKE_RESULT=" + json.dumps({"implemented": False}))
'''
DL11_TESTS = '''import json, os
from lake_project import acceptance, core

report = core.read_published(os.environ["LAKE_ROOT"])
r = report["reconciliation"]
print("LAKE_RESULT=" + json.dumps({
    "sales_daily": r["sales_daily"] == acceptance.EXPECTED_SALES_DAILY,
    "sales_total": r["sales_total"] == acceptance.EXPECTED_SALES_TOTAL,
    "hotel": r["hotel_nights"] == acceptance.EXPECTED_HOTEL_NIGHTS}))
'''

DL11_SERVICE_REF1 = '''import json, os
from pathlib import Path
from lake_project import core
from lake_project.student import read_jsonl

root = Path(os.environ["LAKE_ROOT"])
rows = read_jsonl(root / "input.jsonl")
ingest = core.write_raw_batch(root, rows)
print("LAKE_RESULT=" + json.dumps({"raw_file": ingest["raw_file"], "input_rows": len(rows)}, default=str))
'''

DL11_SERVICE_REF2 = '''import json, os
from pathlib import Path
from lake_project import core
from lake_project.student import read_jsonl

root = Path(os.environ["LAKE_ROOT"])
rows = read_jsonl(root / "input.jsonl")
ingest = core.write_raw_batch(root, rows)
quality = core.rebuild(root)
print("LAKE_RESULT=" + json.dumps({"ingest": ingest, "quality": {k: quality[k] for k in ("accepted", "duplicate", "rejected", "total")}}, default=str))
'''

DL11_TRANSFORM_REF3 = '''SELECT SUM(signed_cents) AS net_cents
FROM facts
WHERE business_date = DATE '2026-01-08';
'''

DL11_STAGE_FILES = [
    {"service": DL11_SERVICE_STAGE1},
    {"service": DL11_SERVICE_STAGE2},
    {"service": DL11_SERVICE, "transform": DL11_TRANSFORM_STAGE3},
    {"service": DL11_SERVICE, "transform": DL11_TRANSFORM_STAGE4, "report": DL11_REPORT_STAGE4},
    {"service": DL11_SERVICE, "transform": DL11_TRANSFORM, "report": DL11_REPORT, "tests": DL11_TESTS_STAGE5},
]
DL11_REFERENCE_FILES = [
    {"service": DL11_SERVICE_REF1},
    {"service": DL11_SERVICE_REF2},
    {"service": DL11_SERVICE, "transform": DL11_TRANSFORM_REF3},
    {"service": DL11_SERVICE, "transform": DL11_TRANSFORM, "report": DL11_REPORT},
    {"service": DL11_SERVICE, "transform": DL11_TRANSFORM, "report": DL11_REPORT, "tests": DL11_TESTS},
]
DL11_STAGES = [
    {"id": 1, "title": "采集", "focus": "service", "preset": "stage1",
     "goal": "从空目录读取 input.jsonl，写一个已确认的不可变原始批次。",
     "evidence": "1 个 confirmed manifest，6 行原始输入；本阶段不要求发布。"},
    {"id": 2, "title": "校验去重", "focus": "service", "preset": "stage2",
     "goal": "在采集后 rebuild，按 event_id 去重并观察互斥分类。",
     "evidence": "accepted=5、duplicate=1、rejected=0，销售与酒店分区已落盘。"},
    {"id": 3, "title": "分区读回", "focus": "transform", "preset": "stage3",
     "goal": "补全 service.py 的发布，再写单日 SQL，用真实计划证明分区裁剪。",
     "evidence": "发布版本可读；1 月 8 日净额 -4900；Scanning Files: 1/2。"},
    {"id": 4, "title": "日报对账", "focus": "report", "preset": "stage4",
     "goal": "把 transform.sql 改成销售日报，并写酒店间夜报表，与独立发布报表对账。",
     "evidence": "销售 9900/-4900、合计 5000；酒店 1/2/1；CURRENT 可独立读回。"},
    {"id": 5, "title": "独立测试", "focus": "tests", "preset": "stage5",
     "goal": "用 acceptance 常量写测试，证明结果不是由学生 SQL 自己反推。",
     "evidence": "四个文件真实落盘；三条独立断言全部通过。"},
]

for _stage, _files in zip(DL11_STAGES, DL11_REFERENCE_FILES):
    _stage["reference_files"] = _files

DL11_BUILD_PRESETS = {
    "service": [
        {"id": "starter", "name": "空目录骨架（待实现）", "code": "from pathlib import Path\n\n# TODO: 读取 input.jsonl，写不可变原始层，rebuild 后 publish。\nraise NotImplementedError('请实现采集与发布')\n"},
        {"id": "stage1", "name": "阶段 1 起始 · 采集", "code": DL11_SERVICE_STAGE1},
        {"id": "stage1_ref", "name": "阶段 1 参考 · 采集", "code": DL11_SERVICE_REF1},
        {"id": "stage2", "name": "阶段 2 起始 · 校验去重", "code": DL11_SERVICE_STAGE2},
        {"id": "stage2_ref", "name": "阶段 2 参考 · 校验去重", "code": DL11_SERVICE_REF2},
        {"id": "reference", "name": "完整参考 service.py", "code": DL11_SERVICE},
    ],
    "transform": [
        {"id": "starter", "name": "空 SQL（待实现）", "code": "SELECT 'TODO' AS status;"},
        {"id": "stage3", "name": "阶段 3 起始 · 单日裁剪", "code": DL11_TRANSFORM_STAGE3},
        {"id": "stage3_ref", "name": "阶段 3 参考 · 单日裁剪", "code": DL11_TRANSFORM_REF3},
        {"id": "stage4", "name": "阶段 4 起始 · 销售日报", "code": DL11_TRANSFORM_STAGE4},
        {"id": "stage4_ref", "name": "阶段 4 参考 · 销售日报", "code": DL11_TRANSFORM},
        {"id": "reference", "name": "完整参考 transform.sql", "code": DL11_TRANSFORM},
    ],
    "report": [
        {"id": "starter", "name": "空 SQL（待实现）", "code": "SELECT 'TODO' AS status;"},
        {"id": "stage4", "name": "阶段 4 起始 · 酒店间夜", "code": DL11_REPORT_STAGE4},
        {"id": "stage4_ref", "name": "阶段 4 参考 · 酒店间夜", "code": DL11_REPORT},
        {"id": "reference", "name": "完整参考 report.sql", "code": DL11_REPORT},
    ],
    "tests": [
        {"id": "starter", "name": "空测试（待实现）", "code": "# TODO: 用 acceptance 常量断言，不用学生 SQL 生成期望。\n"},
        {"id": "stage5", "name": "阶段 5 起始 · 独立测试", "code": DL11_TESTS_STAGE5},
        {"id": "stage5_ref", "name": "阶段 5 参考 · 独立测试", "code": DL11_TESTS},
        {"id": "reference", "name": "完整参考 tests.py", "code": DL11_TESTS},
    ],
}

DL12_REFERENCE = '''import json, os
from pathlib import Path
from lake_project import core
from lake_project.fixtures import sales_batch, hotel_batch, conflict_batch, late_batch

root = os.environ["LAKE_ROOT"]
mode = json.loads(os.environ["LAKE_ARGS"])
evidence = {"mode": mode}
if mode == "crash":
    first = core.write_raw_batch(root, sales_batch(), crash_after_raw=True)
    core.write_raw_batch(root, sales_batch())
    core.rebuild(root); core.publish(root)
    ok = core.read_published(root)["reconciliation"]["sales_total"] == 5000
    evidence.update(first_confirmed=first["confirmed"], raw_files=len(list((Path(root) / "raw" / "batches").glob("*.jsonl"))))
elif mode == "conflict":
    old = core.current_version(root)
    before = core.read_published(root)
    core.write_raw_batch(root, conflict_batch()); core.rebuild(root)
    failed = core.publish(root)
    after = core.read_published(root)
    ok = not failed["published"] and core.current_version(root) == old and before == after
    evidence.update(failed_published=failed["published"], old_readable=before == after)
elif mode == "publish_fail":
    old = core.current_version(root)
    before = core.read_published(root)
    # 先制造一次发布失败，再用独立连接检查旧报表。
    failed = core.publish(root, force_fail=True)
    after = core.read_published(root)
    old_readable = core.current_version(root) == old and before == after
    # 旧版确认可读后，重新执行发布并核对版本已前进。
    recovered = core.publish(root)
    ok = not failed["published"] and old_readable and recovered["published"] and core.current_version(root) != old
    evidence.update(failed_published=failed["published"], old_readable=old_readable,
                    recovered=recovered["published"], old_version=old, new_version=core.current_version(root))
else:
    core.write_raw_batch(root, late_batch())
    out = core.backfill(root, ["2026-01-06"])
    ok = out["unaffected_files_unchanged"]
    evidence.update(unaffected_files_unchanged=out["unaffected_files_unchanged"])
print("LAKE_RESULT=" + json.dumps({"ok": ok, "evidence": evidence}, default=str))
'''

ADVANCED_LESSONS = [
    L("DL01", "DL01 SQL 与表粒度", "一行到底代表一笔付款、一笔退款，还是一个订单？",
      "业务问题：同一订单 o1 既有付款 p1 又有退款 r1。写一条 SQL 数出销售事件笔数，证明 order_id 不是事件主键。",
      "grain", "sql",
      "SELECT COUNT(DISTINCT order_id) AS event_count\nFROM raw_events\nWHERE kind IN ('payment', 'refund');",
      "SELECT COUNT(DISTINCT event_id) AS event_count\nFROM raw_events\nWHERE kind IN ('payment', 'refund');",
      "p1 与 r1 的 order_id 都是 o1，但 event_id 不同。先确定一行代表什么，再选 DISTINCT 的列。",
      "事实表粒度由业务事件定义；同一订单的付款和退款是两行。按 order_id 去重会吞掉退款。",
      dict(q="先猜：3 条销售事件、2 个订单，COUNT(DISTINCT order_id) 等于几？", options=["2", "3", "4"], answer=0), prerequisites=["l1"]),
    L("DL02", "DL02 支付/退款与逐晚建模", "把预订展开成每间房每晚一行",
      "业务问题：酒店两笔预订横跨 7→9 日和 8→10 日。写 SQL 生成房晚事实，验证 7/8/9 日分别为 1/2/1 间夜。",
      "hotel", "sql",
      "SELECT COUNT(*) AS room_nights FROM bookings;",
      "SELECT CAST(night AS DATE) AS stay_date, COUNT(*) AS room_nights\nFROM bookings,\n     generate_series(check_in, check_out - INTERVAL 1 DAY, INTERVAL 1 DAY) AS g(night)\nGROUP BY 1 ORDER BY 1;",
      "generate_series 的右端是 check_out - 1 天；退房当天不占房。",
      "预订事实粒度是 booking_id + room_id + stay_date。间夜数不能直接当收入，入住率还需要可售房晚分母。",
      dict(q="先猜：7→9 日住几晚？", options=["1 晚", "2 晚", "3 晚"], answer=1)),
    L("DL03", "DL03 全量和增量采集", "用到达水位只取新批次，但允许重复",
      "业务问题：每天不能把全部历史重发一遍。写 select_incremental，按 arrived_at 水位取新事件；重复由下游去重。",
      "incremental", "python",
      '''import json, os
def select_incremental(rows, watermark):
    return rows
args = json.loads(os.environ["LAKE_ARGS"])
print("LAKE_RESULT=" + json.dumps({"event_ids": [r["event_id"] for r in select_incremental(args["rows"], args["watermark"])]}))
''',
      '''import json, os
def select_incremental(rows, watermark):
    return [r for r in rows if r.get("arrived_at", "") > watermark]
args = json.loads(os.environ["LAKE_ARGS"])
print("LAKE_RESULT=" + json.dumps({"event_ids": [r["event_id"] for r in select_incremental(args["rows"], args["watermark"])]}))
''',
      "水位比较的是 arrived_at，不是 event_time。迟到事件业务日更早，但到达时间仍可能大于水位。",
      "全量简单可重建，增量减少传输；增量至少一次投递不可避免，正确性由 event_id 去重保证。",
      dict(q="先猜：退款 8 日发生、9 日到达，会被 8 日水位增量取到吗？", options=["会", "不会"], answer=0), prerequisites=["l1"]),
    L("DL04", "DL04 PostgreSQL outbox 与失败重投", "先落稳原始文件，再确认 outbox",
      "业务问题：worker 写完原始文件后、确认前退出。用真实 PostgreSQL 隔离 schema 重投，验证消息重复但报表只算一次。",
      "pg_outbox", "python",
      '''import os, psycopg
from lake_project import core
with psycopg.connect(os.environ["LAKE_PG_DSN"]) as c:
    rows = c.execute("SELECT payload FROM " + os.environ["LAKE_SCHEMA"] + ".outbox WHERE NOT published").fetchall()
if os.getenv("LAKE_CRASH_AFTER_RAW") == "1":
    with psycopg.connect(os.environ["LAKE_PG_DSN"]) as c:
        c.execute("UPDATE " + os.environ["LAKE_SCHEMA"] + ".outbox SET published=true")
    os._exit(73)
core.write_raw_batch(os.environ["LAKE_ROOT"], [r[0] for r in rows])
print("LAKE_RESULT=" + json.dumps({"written": len(rows)}))
''',
      '''import os, psycopg
from lake_project import core
with psycopg.connect(os.environ["LAKE_PG_DSN"]) as c:
    rows = c.execute("SELECT payload FROM " + os.environ["LAKE_SCHEMA"] + ".outbox WHERE NOT published").fetchall()
core.write_raw_batch(os.environ["LAKE_ROOT"], [r[0] for r in rows])
if os.getenv("LAKE_CRASH_AFTER_RAW") == "1":
    os._exit(73)
with psycopg.connect(os.environ["LAKE_PG_DSN"]) as c:
    c.execute("UPDATE " + os.environ["LAKE_SCHEMA"] + ".outbox SET published=true")
print("LAKE_RESULT=" + json.dumps({"written": len(rows)}))
''',
      "确认前退出会重投；只有原始文件稳定后才能推进 published。未配置 LAKE_PG_DSN 时本关明确跳过。",
      "Outbox 把业务写入与事件待发布放进同一事务；文件系统确认与数据库确认之间仍可能重复，因此下游必须幂等。",
      dict(q="先猜：写文件后确认前退出，重启会怎样？", options=["事件丢失", "重复投递", "数据库回滚业务单"], answer=1), prerequisites=["l1"]),
    L("DL05", "DL05 校验、隔离、去重", "第一切片：把 rn 改成正确去重条件",
      "业务问题：4 行输入里 p1 重复投递一次。只改窗口函数的过滤条件，保留 p1/p2/r1 三行，得到总额 5000 分。",
      "dedup", "sql",
      "SELECT event_id, order_id, kind, amount_cents\nFROM (\n  SELECT *, ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY arrived_at) AS rn\n  FROM raw_events\n) WHERE rn >= 1;",
      "SELECT event_id, order_id, kind, amount_cents\nFROM (\n  SELECT *, ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY arrived_at) AS rn\n  FROM raw_events\n) WHERE rn = 1;",
      "第一关只改一处：rn >= 1 改成 rn = 1。不要把 PARTITION BY 改成 order_id。",
      "校验、重复、冲突分类互斥；输入行数 = 接受唯一行 + 重复行 + 拒收/冲突行。相同 event_id 不同内容必须隔离。",
      dict(q="先猜：不去重时总额是多少？", options=["5000", "9900", "14900"], answer=2), prerequisites=["l2", "DL03"]),
    L("DL06", "DL06 迟到数据与分区回填", "业务日按 Asia/Shanghai，不按到达日",
      "业务问题：UTC 时间 2026-01-07 16:30 对应上海 1 月 8 日。写 SQL 从 event_time 派生业务日，并观察迟到只影响目标分区。",
      "business_date", "sql",
      "SELECT event_id, CAST(event_time AS DATE) AS business_date\nFROM raw_events\nWHERE kind IN ('payment', 'refund')\nORDER BY event_id;",
      "SELECT event_id, CAST(timezone('Asia/Shanghai', event_time) AS DATE) AS business_date\nFROM raw_events\nWHERE kind IN ('payment', 'refund')\nORDER BY event_id;",
      "DuckDB 会话时区是 UTC；timezone('Asia/Shanghai', event_time) 后才取 DATE。",
      "event_time 决定业务日，arrived_at 决定何时收到。迟到跨日要回填发生日分区，不能把退款记到到达日。",
      dict(q="先猜：UTC 1 月 7 日 16:30 的上海业务日是？", options=["1 月 7 日", "1 月 8 日"], answer=1), prerequisites=["l4", "DL03"]),
    L("DL07", "DL07 Parquet 内部与分区裁剪", "第二切片：查 8 日，看实际扫描文件",
      "业务问题：silver/sales 有两个日期分区。写一条 8 日净额查询，并从真实执行计划确认只读 1/2 个 Parquet 文件。",
      "partition_scan", "sql",
      "SELECT SUM(signed_cents) AS net_cents FROM facts;",
      "SELECT SUM(signed_cents) AS net_cents\nFROM facts\nWHERE business_date = DATE '2026-01-08';",
      "WHERE 直接比较 DATE 分区列；不要用函数包住 business_date。结果应为 -4900，计划 Scanning Files: 1/2。",
      "Hive 分区目录让查询先排除整目录；列裁剪、row group 裁剪是文件内部的其他优化，不能混为一谈。",
      dict(q="先猜：两个文件里查 8 日，会读几个？", options=["1 个", "2 个", "0 个"], answer=0), prerequisites=["l3", "DL05", "DL06"]),
    L("DL08", "DL08 报表查询、小文件与扫描对比", "同一结果，比较压实与逐事件小文件",
      "业务问题：销售日报不能把退款金额当正数。写报表 SQL，并在正常/边界场景比较一个日期一个文件与多个小文件的扫描证据。",
      "report_scan", "sql",
      "SELECT business_date, SUM(amount_cents) AS net_cents\nFROM facts GROUP BY business_date ORDER BY business_date;",
      "SELECT business_date, SUM(signed_cents) AS net_cents\nFROM facts GROUP BY business_date ORDER BY business_date;",
      "signed_cents 已在事实层把退款记为负数；日报直接求和。",
      "小文件增加打开和元数据开销；压实不能改变业务结果。先证明结果一致，再比较文件数和计划。",
      dict(q="先猜：把退款当正数，8 日会算成？", options=["-4900", "14900", "5000"], answer=1), prerequisites=["l5", "DL02", "DL07"]),
    L("DL09", "DL09 指标口径和数据血缘", "从日报数字回到原始行",
      "业务问题：财务要按退款发生日冲减。写销售净额 SQL，并在运行证据里从 r1 回到 raw_file/raw_line/run_id。",
      "metric_lineage", "sql",
      "SELECT CAST(arrived_at AS DATE) AS business_date, SUM(signed_cents) AS net_cents\nFROM facts GROUP BY 1 ORDER BY 1;",
      "SELECT business_date, SUM(signed_cents) AS net_cents\nFROM facts GROUP BY business_date ORDER BY business_date;",
      "arrived_at 是传输时间，不是记账日。查看 extra.lineage 的 r1 原始行号。",
      "指标口径必须写清粒度、时间字段、金额符号和版本；血缘至少能回到文件、行号、事件 ID 与 run_id。",
      dict(q="先猜：按到达日统计，总额会变吗？", options=["会变成 14900", "总额不变但分日错", "变成 0"], answer=1), prerequisites=["l5", "DL06", "DL08"]),
    L("DL10", "DL10 暂存、质量检查、发布及回退", "第三切片：检查失败时旧版仍可读",
      "业务问题：新报表先写 staging，质量检查全部通过才原子切 CURRENT。写 checked=True，在发布失败场景证明旧版仍可读。",
      "publish", "python",
      PY_PRINT.replace("checked=True", "checked=False"),
      PY_PRINT,
      "不要直接写 CURRENT；调用 stage_and_publish(checked=True)。失败时检查 current_version 是否仍是旧版。",
      "暂存目录与发布指针分离；指针切换是发布动作。质量失败、进程失败都不能让读者看到半成品。",
      dict(q="先猜：新版本质量检查失败时，读者应看到？", options=["半成品", "上一有效版", "空报表"], answer=1), prerequisites=["DL05", "DL08", "DL09"]),
    L("DL11", "DL11 从空项目写采集与计算代码", "五阶段实战：采集 → 校验去重 → 分区读回 → 日报对账 → 独立测试",
      "业务问题：从空目录和 input.jsonl 出发，按五个阶段补齐 service.py、transform.sql、report.sql、tests.py；每阶段只验收当前产物，最终完成独立交付。",
      "capstone_files", "service", "", "", "先完成阶段 1 的原始批次，再逐步补 rebuild、发布、两条 SQL 和测试；不要跳过中间证据。",
      "综合关不是填空：四个文件会真实写入本次运行目录并执行。阶段导航、逐文件草稿和提交历史继续复用现有画板。",
      dict(q="先猜：从空目录到独立验收，至少要经过几个阶段？", options=["1 个", "3 个", "5 个"], answer=2), minutes=25,
      prerequisites=["DL01", "DL02", "DL03", "DL05", "DL06", "DL07", "DL08", "DL09", "DL10"],
      lake_stages=DL11_STAGES, build_presets=DL11_BUILD_PRESETS),
    L("DL12", "DL12 用故障场景验收", "综合关：亲手制造发布失败、检查旧报表，再恢复",
      "业务问题：选择故障场景，写代码制造并恢复它。发布失败场景必须先读旧版、确认失败后旧版仍可读，再重新发布；平台会独立检查文件、版本和未受影响分区。",
      "fault_acceptance", "python",
      '''import json, os
from lake_project import core
root = os.environ["LAKE_ROOT"]
mode = json.loads(os.environ["LAKE_ARGS"])
# TODO: 按 mode 制造崩溃、冲突、发布失败或定向回填，并输出恢复证据。
print("LAKE_RESULT=" + json.dumps({"ok": False, "mode": mode}))
''',
      DL12_REFERENCE,
      "四个场景都允许调用 core，但必须真的制造故障并验证恢复；publish_fail 要依次执行 force_fail=True、读旧报表、再次 publish。",
      "故障验收要同时证明：没有静默丢数、没有重复计入、旧版可读、发布可恢复、其他日期文件哈希不变。f1 是路线 C 总挑战，不作为本关先修。",
      dict(q="先猜：回填 1 月 6 日时，1 月 7/8 日文件应怎样？", options=["全部重写", "内容不变", "删除"], answer=1), minutes=18,
      prerequisites=["DL03", "DL05", "DL06", "DL10", "DL11"],
      scenarios=[{"id": "crash", "name": "确认前崩溃", "description": "写原始文件后未确认退出，再重投。"},
                 {"id": "conflict", "name": "同 ID 冲突", "description": "冲突行隔离，旧版仍可读。"},
                 {"id": "publish_fail", "name": "发布失败后恢复", "description": "force_fail 后检查旧报表，再重新发布。"},
                 {"id": "backfill", "name": "迟到回填", "description": "只重算 1 月 6 日，其他文件不变。"}]),
]

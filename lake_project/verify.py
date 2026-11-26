"""独立验收矩阵：期望值写死，结果从文件/新连接读回。"""
import json
import os
import platform
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from . import acceptance, core
from .fixtures import bad_batch, baseline_batch, conflict_batch, hotel_batch, late_batch, nullable_batch, sales_batch, utc_batch

ROOT = Path(__file__).resolve().parent.parent


def verify(keep_dir=None):
    keep_dir = Path(keep_dir) if keep_dir else ROOT / "runs" / "lake-acceptance" / datetime.now().strftime("%Y%m%d-%H%M%S")
    keep_dir.mkdir(parents=True, exist_ok=True)
    results = []

    def check(name, fn):
        try:
            detail = fn()
            results.append({"name": name, "status": "通过", "detail": detail})
        except Exception as exc:
            results.append({"name": name, "status": "失败", "detail": str(exc)})

    def root(name):
        path = keep_dir / name
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        return path

    def baseline(name="baseline"):
        base = root(name)
        outcome = core.run_pipeline(base, sales_batch() + hotel_batch())
        return base, outcome

    def t_baseline():
        base, out = baseline()
        rec = out["reports"]["reconciliation"]
        assert rec["sales_daily"] == acceptance.EXPECTED_SALES_DAILY, rec
        assert rec["sales_total"] == acceptance.EXPECTED_SALES_TOTAL, rec
        assert rec["hotel_nights"] == acceptance.EXPECTED_HOTEL_NIGHTS, rec
        assert rec["hotel_total"] == 4, rec
        assert out["scan"]["scanning_files"] == acceptance.EXPECTED_SCAN_FILES, out["scan"]
        assert out["scan"]["total_files_read"] == 1
        assert len(core.parquet_files(base, "sales")) == 2
        return {"sales_daily": rec["sales_daily"], "sales_total": rec["sales_total"],
                "hotel_nights": rec["hotel_nights"], "scan": out["scan"]["scanning_files"],
                "files": out["scan"]["filenames"], "root": str(base)}
    check("正确净额、酒店间夜与单日 1/2 文件", t_baseline)

    def t_wrong():
        base = root("wrong")
        core.run_pipeline(base, sales_batch())
        wrong = core.wrong_totals(base)
        assert wrong["no_dedup_total"] == acceptance.EXPECTED_WRONG_TOTAL, wrong
        assert wrong["order_id_dedup_total"] == acceptance.EXPECTED_WRONG_TOTAL, wrong
        return wrong
    check("不去重与按 order_id 去重均为 14900，验收判失败", t_wrong)

    def t_repeat():
        base = root("repeat")
        first = core.run_pipeline(base, sales_batch())
        hashes = core.file_hashes(base)
        manifest = core.write_raw_batch(base, sales_batch())
        second = core.run_pipeline(base, sales_batch())
        assert manifest["replayed"] is True
        assert hashes == core.file_hashes(base)
        assert first["reports"]["reconciliation"] == second["reports"]["reconciliation"]
        assert second["quality"]["duplicate"] == acceptance.EXPECTED_DUPLICATES
        return {"replayed": manifest["replayed"], "same_hashes": True, "daily": second["reports"]["reconciliation"]}
    check("同批重跑原始不覆盖、结果不变", t_repeat)

    def t_empty():
        base = root("empty")
        out = core.run_pipeline(base, [])
        assert out["quality"]["total"] == 0
        assert out["reports"]["reconciliation"]["sales_total"] == 0
        return out["reports"]["reconciliation"]
    check("空批可重跑且不伪造金额", t_empty)

    def t_all_bad():
        base = root("all-bad")
        out = core.run_pipeline(base, bad_batch())
        assert out["quality"]["rejected"] == 3
        assert out["quality"]["conserved"] is True
        assert out["publish"]["published"] is False
        assert core.current_version(base) is None
        return {"rejected": out["quality"]["rejected"], "reasons": [r["reason"] for r in out["quality"]["rejected_rows"]]}
    check("全坏批次隔离且不发布", t_all_bad)

    def t_conflict():
        base = root("conflict")
        old = core.run_pipeline(base, sales_batch())["reports"]["reconciliation"]
        core.write_raw_batch(base, conflict_batch())
        quality = core.rebuild(base)
        failed = core.publish(base, force_fail=False)
        assert quality["conflicts"] == 1
        assert failed["published"] is False
        assert core.read_published(base)["reconciliation"] == old
        return {"conflicts": quality["conflicts"], "old_version_still_readable": core.current_version(base)}
    check("同 ID 不同内容隔离，旧版仍可读", t_conflict)

    def t_crash():
        base = root("crash")
        first = core.write_raw_batch(base, sales_batch(), crash_after_raw=True)
        assert first["confirmed"] is False
        assert core.confirmed_manifests(base) == []
        retry = core.write_raw_batch(base, sales_batch())
        assert retry["confirmed"] is True
        core.rebuild(base)
        core.publish(base)
        assert core.read_published(base)["reconciliation"]["sales_total"] == 5000
        return {"crash_confirmed": first["confirmed"], "retry_confirmed": retry["confirmed"], "raw_files": len(list((base / "raw" / "batches").glob("*.jsonl")))}
    check("写原始文件后确认前退出，重投不重复", t_crash)

    def t_late():
        base = root("late")
        core.run_pipeline(base, sales_batch() + hotel_batch())
        unaffected_before = {p: h for p, h in core.file_hashes(base).items() if "2026-01-07" in p or "2026-01-08" in p}
        core.write_raw_batch(base, late_batch())
        out = core.backfill(base, ["2026-01-06"])
        rec = core.read_published(base)["reconciliation"]
        assert rec["sales_daily"]["2026-01-06"] == 1200
        assert rec["sales_daily"]["2026-01-07"] == 9900
        assert rec["sales_daily"]["2026-01-08"] == -4900
        assert out["unaffected_files_unchanged"] is True
        assert all(core.file_hashes(base)[p] == h for p, h in unaffected_before.items())
        return {"affected": out["dates"], "unaffected_unchanged": True, "sales_daily": rec["sales_daily"]}
    check("迟到跨日只回填受影响分区", t_late)

    def t_timezone():
        base = root("timezone")
        out = core.run_pipeline(base, utc_batch())
        rec = out["reports"]["reconciliation"]
        assert rec["sales_daily"] == {"2026-01-08": 700}, rec
        return rec
    check("UTC 时间按 Asia/Shanghai 映射业务日", t_timezone)

    def t_nullable():
        base = root("nullable")
        core.run_pipeline(base, sales_batch())
        core.write_raw_batch(base, nullable_batch())
        core.rebuild(base)
        out = core.publish(base)
        rec = core.read_published(base)["reconciliation"]
        assert out["published"] is True
        assert rec["sales_daily"]["2026-01-09"] == 800
        row = next(r for r in core.read_sales(base) if r["event_id"] == "p4")
        assert json.loads(row["attributes"])["campaign_id"] is None
        return {"sales_daily": rec["sales_daily"], "nullable_field": "campaign_id=NULL"}
    check("新增可空字段兼容并保留", t_nullable)

    def t_publish_rollback():
        base = root("rollback")
        first = core.run_pipeline(base, sales_batch())["publish"]["version"]
        failed = core.publish(base, force_fail=True)
        assert failed["published"] is False
        assert core.current_version(base) == first
        second = core.publish(base)["version"]
        assert second != first
        rolled = core.rollback(base)
        assert core.current_version(base) == first
        assert rolled["from"] == second
        return {"failed_kept": first, "new_version": second, "rolled_back_to": core.current_version(base)}
    check("发布失败不切指针，回退读取旧版", t_publish_rollback)

    def t_lineage():
        base = root("lineage")
        core.run_pipeline(base, sales_batch())
        trace = core.lineage(base, "r1")
        assert len(trace) == 1
        item = trace[0]
        assert item["fact"]["raw_line"] == 3
        assert item["source_row"]["event_id"] == "r1"
        assert str(item["fact"]["business_date"]) == "2026-01-08"
        return item
    check("日报可回溯到原始文件、行号和 run_id", t_lineage)

    results.append({"name": "真实 PostgreSQL outbox", "status": "跳过",
                    "detail": "未配置 LAKE_PG_DSN；基础 L0 全部使用 DuckDB，不计失败。" if not os.getenv("LAKE_PG_DSN") else "需在课程 DL04 中运行真实隔离 schema 验收。"})
    results.append({"name": "Kafka/Debezium、Spark、S3/Iceberg", "status": "未实测",
                    "detail": "按 L2 顺序提供复现命令；本机未搭建这些环境，不阻塞主线。"})
    failed = sum(r["status"] == "失败" for r in results)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "duckdb": duckdb.__version__, "platform": platform.platform()},
        "expectations": {"sales_daily": acceptance.EXPECTED_SALES_DAILY, "sales_total": acceptance.EXPECTED_SALES_TOTAL,
                         "wrong_total": acceptance.EXPECTED_WRONG_TOTAL, "hotel_nights": acceptance.EXPECTED_HOTEL_NIGHTS,
                         "scan_files": acceptance.EXPECTED_SCAN_FILES},
        "passed": failed == 0, "failed": failed, "results": results, "evidence_dir": str(keep_dir),
    }
    (ROOT / "runs").mkdir(exist_ok=True)
    (ROOT / "runs" / "lake-verify.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report


def main():
    report = verify()
    for item in report["results"]:
        print(item["status"] + " | " + item["name"] + " | " + json.dumps(item["detail"], ensure_ascii=False)[:240])
    print("PASS" if report["passed"] else "FAIL")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

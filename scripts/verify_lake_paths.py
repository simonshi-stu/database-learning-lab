"""独立目录实跑 DL11 五阶段、DL12 恢复与 DL07 裁剪，并核对课程/路由数量。"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from fastapi.routing import APIRoute
from lab import course_lake, course_lake_data
from lab.course_server import LESSONS, app


def lesson(lesson_id):
    return next(item for item in LESSONS if item["id"] == lesson_id)


def starter_files(item):
    return {name: options[0]["code"] for name, options in item["build_presets"].items()}


def check_dl11():
    item = lesson("DL11")
    evidence = []
    with tempfile.TemporaryDirectory(prefix="lake-dl11-") as tmp:
        for index, stage in enumerate(item["lake_stages"]):
            files = starter_files(item)
            files.update(stage["reference_files"])
            cases = course_lake.run(item, "", Path(tmp), scenario="normal", files=files, stage=index + 1)
            assert cases and cases[0]["passed"] is True, (stage["id"], cases)
            evidence.append({"stage": stage["id"], "title": stage["title"], "passed": True,
                             "checks": cases[0]["extra"]["checks"]})
        root = Path(tmp) / "lake"
        report = course_lake.core.read_published(root)
        assert report["reconciliation"]["sales_total"] == 5000
        assert len(list((root / "raw" / "batches").glob("*.jsonl"))) == 1
    return {"stages": evidence, "same_raw_file": True, "sales_total": 5000}


def check_dl12():
    item = lesson("DL12")
    evidence = []
    for scenario in item["scenarios"]:
        with tempfile.TemporaryDirectory(prefix="lake-dl12-") as tmp:
            cases = course_lake.run(item, item["solution"], Path(tmp), scenario=scenario["id"])
            assert cases and cases[0]["passed"] is True, (scenario["id"], cases)
            extra = cases[0]["extra"]
            evidence.append({"scenario": scenario["id"], "passed": True, "recovery": extra["recovery"],
                             "independent_check": extra["independent_check"]})
    return evidence


def check_dl07():
    item = lesson("DL07")
    with tempfile.TemporaryDirectory(prefix="lake-dl07-") as tmp:
        cases = course_lake.run(item, item["solution"], Path(tmp), scenario="normal")
        assert cases and cases[0]["passed"] is True, cases
        scan = cases[0]["extra"]["scan"]
        assert scan["scanning_files"] == "1/2" and scan["total_files_read"] == 1, scan
    return {"scanning_files": scan["scanning_files"], "total_files_read": scan["total_files_read"]}


def counts():
    paths = sorted({route.path for route in app.routes if isinstance(route, APIRoute)})
    course_http = [path for path in paths if path == "/course" or path.startswith("/api/course")]
    return {
        "lessons": len(LESSONS),
        "chapters": len({item.get("chapter") for item in LESSONS}),
        "lake_lessons": sum(item["id"].startswith("DL") for item in LESSONS),
        "old_l_f_lessons": sum(item["id"] in {"l1", "l2", "l3", "l4", "l5", "f1"} for item in LESSONS),
        "pages_total": sum(path in {"/", "/course"} for path in paths),
        "course_pages": sum(path == "/course" for path in paths),
        "original_lab_pages": sum(path == "/" for path in paths),
        "course_http_endpoints": len(course_http),
        "course_http_paths": course_http,
        "frontend_hash_routes": len(LESSONS) + 1,
        "lake_hash_routes": sum(item["id"].startswith("DL") for item in LESSONS),
    }


def main():
    report = {"counts": counts(), "DL11": check_dl11(), "DL12": check_dl12(), "DL07": check_dl07(),
              "postgresql_outbox": "跳过" if not os.getenv("LAKE_PG_DSN") else "需在 DL04 单独验收",
              "kafka_spark_iceberg": "未实测"}
    path = ROOT / "runs" / "lake-paths.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

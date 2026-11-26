"""学生可调用的小型 API；所有输入输出都限制在本次练习目录。"""
import json
from pathlib import Path

from . import core


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def select_incremental(rows, watermark):
    return [r for r in rows if r.get("arrived_at", "") > watermark]


def stage_and_publish(root, checked=True, force_fail=False):
    if not checked:
        pointer = Path(root) / "published" / "CURRENT"
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text("unchecked", encoding="utf-8")
        return {"published": True, "checked": False, "current_version": "unchecked"}
    quality = core.rebuild(root)
    result = core.publish(root, force_fail=force_fail)
    return {"published": result["published"], "checked": True, "quality": quality,
            "current_version": core.current_version(root), "checks": result["checks"]}


def verify_baseline(root):
    report = core.read_published(root)
    if not report:
        return {"sales_daily_ok": False, "sales_total_ok": False, "hotel_ok": False}
    reconciliation = report["reconciliation"]
    return {
        "sales_daily_ok": reconciliation["sales_daily"] == {"2026-01-07": 9900, "2026-01-08": -4900},
        "sales_total_ok": reconciliation["sales_total"] == 5000,
        "hotel_ok": reconciliation["hotel_nights"] == {"2026-01-07": 1, "2026-01-08": 2, "2026-01-09": 1},
    }

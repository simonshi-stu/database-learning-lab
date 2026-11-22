"""Build the browser-only exercise. No API executes a learner's code."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "order_project"
CHAPTER_FILES = {"schema.sql", "service.py", "api.py", "health.py", "deploy.json", "observe.py"}


def project_bundle():
    catalog = json.loads((PROJECT / "catalog.json").read_text(encoding="utf-8"))
    if len(catalog["chapters"]) != len(CHAPTER_FILES) or {c["file"] for c in catalog["chapters"]} != CHAPTER_FILES:
        raise ValueError("项目章节文件列表无效")
    checks = (PROJECT / "tests/checks.py").read_text(encoding="utf-8")
    support = {
        "README.md": (PROJECT / "EXPORT.md").read_text(encoding="utf-8"),
        "tests/checks.py": checks,
        "run_cluster.py": (PROJECT / "run_cluster.py").read_text(encoding="utf-8"),
        ".github/workflows/order-lab.yml": (PROJECT / "student-workflow.yml").read_text(encoding="utf-8"),
    }
    result = []
    for chapter in catalog["chapters"]:
        name = chapter["file"]
        if not name in CHAPTER_FILES:
            raise ValueError("项目文件无效")
        result.append(dict(chapter, starter=(PROJECT / "starter" / name).read_text(encoding="utf-8"),
                           reference=(PROJECT / "reference" / name).read_text(encoding="utf-8")))
    return dict(catalog, chapters=result, checks=checks, support=support)

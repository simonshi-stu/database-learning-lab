"""DL01–DL12 网页同款路由验证：参考解全场景通过、起始解正常场景必须失败。"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

BASE = os.environ.get("COURSE_BASE", "http://127.0.0.1:8022").rstrip("/")


def get_json(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def run_case(lesson, code, scenario, files=None, stage=None):
    payload = {"lesson_id": lesson["id"], "code": code, "choice": "", "scenario": scenario}
    if files is not None:
        payload["files"] = files
    if stage is not None:
        payload["stage"] = stage
    request = urllib.request.Request(
        BASE + "/api/course/run", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"passed": False, "run_id": "http-error",
                "cases": [{"passed": False, "error": f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}"}]}


def presets(lesson, preset_id):
    return {name: next(p["code"] for p in options if p["id"] == preset_id)
            for name, options in lesson["build_presets"].items()}


def stage_files(lesson, index, reference):
    stage = lesson["lake_stages"][index]
    files = presets(lesson, "starter")
    if reference:
        files.update(stage["reference_files"])
    else:
        files[stage["focus"]] = next(p["code"] for p in lesson["build_presets"][stage["focus"]]
                                     if p["id"] == stage["preset"])
    return files


def main():
    lessons = [l for l in get_json("/api/course")["lessons"] if l["id"].startswith("DL")]
    if [l["id"] for l in lessons] != [f"DL{i:02d}" for i in range(1, 13)]:
        raise SystemExit("课程接口未完整注册 DL01–DL12")
    results = []
    for lesson in lessons:
        if lesson.get("lake_stages"):
            for index, stage in enumerate(lesson["lake_stages"]):
                result = run_case(lesson, "", "normal", stage_files(lesson, index, True), index + 1)
                case = (result.get("cases") or [{}])[0]
                results.append({"lesson": lesson["id"], "kind": "reference", "scenario": "stage-" + str(stage["id"]),
                                "passed": result.get("passed"), "run_id": result.get("run_id"),
                                "scan": ((case.get("extra") or {}).get("scan") or {}).get("scanning_files"),
                                "error": case.get("error")})
            for index, stage in enumerate(lesson["lake_stages"]):
                result = run_case(lesson, "", "normal", stage_files(lesson, index, False), index + 1)
                results.append({"lesson": lesson["id"], "kind": "starter", "scenario": "stage-" + str(stage["id"]),
                                "passed": result.get("passed"), "run_id": result.get("run_id"),
                                "error": (result.get("cases") or [{}])[0].get("error")})
        for scenario in lesson["scenarios"]:
            files = presets(lesson, "reference") if lesson.get("build_presets") else None
            result = run_case(lesson, lesson["solution"], scenario["id"], files, 5 if lesson.get("lake_stages") else None)
            case = (result.get("cases") or [{}])[0]
            scan = ((case.get("extra") or {}).get("scan") or {}).get("scanning_files")
            results.append({"lesson": lesson["id"], "kind": "reference", "scenario": scenario["id"],
                            "passed": result.get("passed"), "run_id": result.get("run_id"),
                            "scan": scan, "error": case.get("error")})
        files = presets(lesson, "starter") if lesson.get("build_presets") else None
        starter_scenario = "crash" if lesson["id"] == "DL12" else "normal"
        result = run_case(lesson, lesson["starter"], starter_scenario, files)
        results.append({"lesson": lesson["id"], "kind": "starter", "scenario": starter_scenario,
                        "passed": result.get("passed"), "run_id": result.get("run_id"),
                        "error": (result.get("cases") or [{}])[0].get("error")})
    failures = []
    for item in results:
        if item["lesson"] == "DL04" and not os.environ.get("LAKE_PG_DSN"):
            if item["passed"] is not None:
                failures.append(item["lesson"] + " DL04 未配置数据库时应跳过")
        elif item["kind"] == "reference" and item["passed"] is not True:
            failures.append(item["lesson"] + " 参考解未通过 " + item["scenario"])
        elif item["kind"] == "starter" and item["passed"] is not False:
            failures.append(item["lesson"] + " 起始解不应通过正常场景")
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "base": BASE,
              "passed": not failures, "failures": failures, "results": results}
    path = ROOT / "runs" / "lake-course-verify.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for item in results:
        print(item["lesson"], item["kind"], item["scenario"], item["passed"], item.get("scan") or "")
    print(f"\n{len(results)} 组，失败 {len(failures)} 组；报告：{path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

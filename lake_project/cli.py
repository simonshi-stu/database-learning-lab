"""终端入口：demo / verify / inspect / worker / backfill / rollback。"""
import argparse
import json
import sys
from pathlib import Path

from . import core
from .fixtures import hotel_batch, sales_batch
from .verify import verify


def _print(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv=None):
    parser = argparse.ArgumentParser(description="订单与退款数据湖 · 本地可复跑项目")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="从固定合成数据跑完整切片")
    demo.add_argument("--root", default="runs/lake-demo")
    demo.add_argument("--sales-only", action="store_true")
    verify_p = sub.add_parser("verify", help="运行独立验收矩阵")
    verify_p.add_argument("--evidence-dir")
    inspect = sub.add_parser("inspect", help="独立连接读回文件、计划和报表")
    inspect.add_argument("--root", required=True)
    inspect.add_argument("--date", default="2026-01-08")
    worker = sub.add_parser("worker", help="模拟写原始文件后确认前退出")
    worker.add_argument("--root", required=True)
    worker.add_argument("--crash-after-raw", action="store_true")
    backfill = sub.add_parser("backfill", help="只重算指定日期并发布")
    backfill.add_argument("--root", required=True)
    backfill.add_argument("--date", action="append", required=True)
    rollback = sub.add_parser("rollback", help="切回上一个已发布版本")
    rollback.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    if args.command == "demo":
        rows = sales_batch() if args.sales_only else sales_batch() + hotel_batch()
        _print(core.run_pipeline(args.root, rows))
        return 0
    if args.command == "verify":
        report = verify(args.evidence_dir)
        _print({"passed": report["passed"], "failed": report["failed"], "report": "runs/lake-verify.json",
                "evidence_dir": report["evidence_dir"]})
        return 0 if report["passed"] else 1
    if args.command == "inspect":
        result = {"root": str(Path(args.root).resolve()), "current_version": core.current_version(args.root),
                  "files": core.file_hashes(args.root), "scan": core.scan_evidence(args.root, args.date),
                  "reports": core.read_published(args.root), "wrong": core.wrong_totals(args.root),
                  "lineage_r1": core.lineage(args.root, "r1")}
        _print(result)
        return 0
    if args.command == "worker":
        result = core.write_raw_batch(args.root, sales_batch(), crash_after_raw=args.crash_after_raw)
        _print(result)
        return 73 if args.crash_after_raw else 0
    if args.command == "backfill":
        _print(core.backfill(args.root, args.date))
        return 0
    if args.command == "rollback":
        _print(core.rollback(args.root))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

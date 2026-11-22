"""Export the project editor as an independent static site."""
import argparse
import json
import shutil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab.order_project_ui import ROOT, project_bundle

def build(out):
    out.mkdir(parents=True, exist_ok=True)
    assets = out / "static"
    assets.mkdir(exist_ok=True)
    src = ROOT / "static"
    for f in ("course.css", "order-project.css", "order-project.mjs", "order-project-worker.mjs", "project-zip.mjs"):
        shutil.copy2(src / f, assets / f)
    shutil.copytree(src / "vendor", assets / "vendor", dirs_exist_ok=True)
    page = (src / "order-project.html").read_text(encoding="utf-8").replace('"/static/', '"./static/')
    page = page.replace('content="/api/course/project"', 'content="./project.json"')
    page = page.replace('href="/course#home"', 'href="https://github.com/simonshi-stu/database-learning-lab/blob/feat/cloud-backend-learning/COURSE.md#home"')
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / "project.json").write_text(json.dumps(project_bundle(), ensure_ascii=False), encoding="utf-8")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    build(parser.parse_args().directory)

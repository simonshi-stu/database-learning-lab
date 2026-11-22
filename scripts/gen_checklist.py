"""生成 45 关核对表 docs/checklist.md（学习目标|引擎版本|关键结论|代码/官方资料|用例|回放证据|未验证）。"""
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:8022'

with urllib.request.urlopen(BASE + '/api/course/checklist', timeout=30) as r:
    data = json.loads(r.read().decode('utf-8'))

lines = ['# 45 关核对表', '',
         '每关一行：学习目标｜适用引擎与版本｜关键结论｜对应代码或官方资料｜正常/边界/错误用例｜回放证据步｜未验证项。',
         '页面文案与判题共用 lab/course_content.py 结构化数据；结论区分 SQLite / PostgreSQL / 教学 C++，不跨引擎套用。', '']
for row in data['rows']:
    lines.append(f"## {row['id']} · {row['title']}")
    lines.append(f"- 学习目标：{row['goal']}")
    lines.append(f"- 引擎与版本：{row['engine']}")
    lines.append(f"- 关键结论：{row['conclusion']}")
    lines.append(f"- 代码/资料：{row['refs']}")
    lines.append(f"- 用例：{row['cases']}")
    lines.append(f"- 回放证据：{row['evidence']}")
    if row.get('unverified'):
        lines.append(f"- 未验证：{'；'.join(row['unverified'])}")
    lines.append('')
lines.append('## 术语白话（首次出现先白话后术语）')
for k, v in data['glossary'].items():
    lines.append(f'- {k}：{v}')
lines.append('')
lines.append('## 官方参考')
lines.append('- https://www.postgresql.org/docs/18/transaction-iso.html')
lines.append('- https://www.postgresql.org/docs/18/explicit-locking.html')
lines.append('- https://www.sqlite.org/isolation.html')
(ROOT / 'docs' / 'checklist.md').write_text('\n'.join(lines), encoding='utf-8')
print('checklist rows:', len(data['rows']))
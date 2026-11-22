# 63 关逐关验收

| 题号 | 引擎 | 参考解 | 反例 | 跳过原因/备注 |
| --- | --- | --- | --- | --- |
| 00a | SQLite | 通过 | 失败 | [] |
| 00b | SQLite | 通过 | 失败 | 主键 ['sku']，类型 {'sku': 'TEXT', 'qty': 'INTEGER'} |
| 00c | SQLite | 通过 | 失败 | [['A', 1]] |
| 00d | SQLite | 通过 | 失败 | [['A', 1]] |
| select | SQLite | 通过 | 失败 | [['A', 3], ['B', 0]] |
| where | SQLite | 通过 | 失败 | [['A', 2], ['C', 3]] |
| fk | SQLite | 通过 | 失败 | 1 行订单 |
| join | SQLite | 通过 | 失败 | [[1, '苹果', 2], [2, '香蕉', 1]] |
| stock | SQLite | 通过 | 失败 | [['A', 1]] |
| decrement | SQLite | 通过 | 失败 | [['A', 0], ['B', 4]] |
| commit | SQLite | 通过 | 失败 | [['A', 3], ['B', 4]] |
| rollback | SQLite | 通过 | 失败 | [['A', 2], ['B', 4]] |
| retry | SQLite | 通过 | 失败 | 尝试1 1 行 / 尝试2 0 行，最终 [['A', 1]]，订单 1 笔 |
| choice | SQLite | 通过 | 失败 | [['A', 3], ['B', 4]] |
| order | SQLite | 通过 | 失败 | HTTP 200 PLACED，库存 [['A', 0]]，订单 1 笔 |
| wait | SQLite | 通过 | 失败 | 最终 [['A', 0]]，B 等到 A 提交=True |
| busy | SQLite | 通过 | 失败 | busy=True，重试后 [['A', 0]] |
| pg-connect | PostgreSQL | 通过 | 失败 | [['A', 1]] |
| pg-iso | PostgreSQL | 通过 | 失败 | 2 |
| pg-lock | PostgreSQL | 通过 | 失败 | B=ok，最终 [[1, 0], [2, 2]] |
| deadlock | PostgreSQL | 通过 | 通过 | cross={'A': '40P01', 'B': 'committed'} uniform={'A |
| pg-plan | PostgreSQL | 通过 | 失败 | 前 50 行 / 后 50 行 / 基准 50 行 |
| exit | SQLite | 通过 | 失败 | 退出码 73，0 预订 / 0 房晚，找回=False |
| pg-contrast | PostgreSQL | 通过 | 通过 | {'wait': 'B 等待后成功', 'timeout': '55P03', 'deadlock_ |
| pg-guide-sales-schema | PostgreSQL | 通过 | 失败 | 1/1 通过 |
| pg-guide-sales-single | PostgreSQL | 通过 | 失败 | 1/1 通过 |
| pg-guide-sales-insufficient | PostgreSQL | 通过 | 失败 | 1/1 通过 |
| pg-guide-sales-retry | PostgreSQL | 通过 | 失败 | 1/1 通过 |
| pg-guide-sales-race | PostgreSQL | 通过 | 失败 | 1/1 通过 |
| pg-guide-hotel-schema | PostgreSQL | 通过 | 失败 | 1/1 通过 |
| pg-guide-hotel-single | PostgreSQL | 通过 | 失败 | 1/1 通过 |
| pg-guide-hotel-twonights | PostgreSQL | 通过 | 失败 | 1/1 通过 |
| pg-guide-hotel-conflict | PostgreSQL | 通过 | 失败 | 1/1 通过 |
| pg-guide-hotel-retry | PostgreSQL | 通过 | 失败 | 1/1 通过 |
| pg-final-sales | PostgreSQL | 通过 | 失败 | 4/4 通过 |
| pg-final-hotel | PostgreSQL | 通过 | 失败 | 4/4 通过 |
| pg-build-sales | PostgreSQL | 通过 | 失败 | 4/4 通过 |
| pg-build-hotel | PostgreSQL | 通过 | 失败 | 4/4 通过 |
| cpp-page | 教学C++ | 通过 | 失败 | insert ok=True free=44 重读 keys=[3] |
| cpp-buffer | 教学C++ | 通过 | 通过 | ops=miss,evict,miss,evict,miss |
| cpp-btree | 教学C++ | 通过 | 通过 | ops=search |
| cpp-exec | 教学C++ | 通过 | 通过 | Scan 6 → Filter 0 → Project 0 |
| cpp-locks | 教学C++ | 通过 | 通过 | ops=grant,wait,commit,release,grant,commit,release |
| sales | SQLite(原实验室) | 通过 | 失败 |  |
| hotel | SQLite(原实验室) | 通过 | 失败 |  |
| c1 | PostgreSQL+DuckDB/数据湖 | 通过 | 失败 | 订单1 库存4 查询[{'sku': 'A', 'qty': 4}] |
| c2 | PostgreSQL+DuckDB/数据湖 | 通过 | 无编辑反例 | 见前后快照 |
| d1 | PostgreSQL+DuckDB/数据湖 | 通过 | 无编辑反例 | rc=0 |
| d2 | PostgreSQL+DuckDB/数据湖 | 通过 | 失败 | 订单1 库存4 replayed=True |
| d3 | PostgreSQL+DuckDB/数据湖 | 通过 | 通过 | 见快照 |
| d4 | PostgreSQL+DuckDB/数据湖 | 通过 | 通过 | 结果1行 rc1=73 |
| d5 | PostgreSQL+DuckDB/数据湖 | 通过 | 失败 | 结果1 失败2 |
| l1 | PostgreSQL+DuckDB/数据湖 | 通过 | 无编辑反例 | 相等 |
| l2 | PostgreSQL+DuckDB/数据湖 | 通过 | 通过 | 原始2 有效2 坏0 |
| l3 | PostgreSQL+DuckDB/数据湖 | 通过 | 失败 | err=None 命中2 列['biz_date', 'net_cents'] |
| l4 | PostgreSQL+DuckDB/数据湖 | 通过 | 失败 | 净5000 间夜4 逐日{'2026-12-07': 2, '2026-12-08': 2} 重复1 |
| l5 | PostgreSQL+DuckDB/数据湖 | 通过 | 失败 | err=None 行[{'biz_date': '2026-12-07', 'net_cents': |
| k1 | Docker/K8s | 通过 | 通过 | Up 2 seconds |
| k2 | Docker/K8s | 跳过 | 无编辑反例 | 跳过：示意 replicas=2；无集群未实测。安装入口：1) Docker Desktop 已装；2) 下载 kind |
| k3 | Docker/K8s | 跳过 | 跳过 | 跳过：示意 UID uid-219c3c1e→uid-7882935d；无集群未实测。安装入口：1) Docker De |
| k4 | Docker/K8s | 跳过 | 无编辑反例 | 跳过：示意探针行为；无集群未实测。安装入口：1) Docker Desktop 已装；2) 下载 kind.exe 放入 |
| k5 | Docker/K8s | 跳过 | 跳过 | 跳过：示意 rollout/回滚；无集群未实测。安装入口：1) Docker Desktop 已装；2) 下载 kind |
| f1 | PostgreSQL+DuckDB/数据湖 | 通过 | 失败 | 净5000 订单2 结果5 |

参考解：通过 59 / 失败 0 / 跳过 4（共 63 关）。
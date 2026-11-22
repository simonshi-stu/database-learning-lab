# 我的订单服务
每个源文件来自一章。先确认浏览器中每章的进度都是「通过」，再下载最终 ZIP。

## 本地看结果
解压，进入包含 schema.sql 的目录，运行 `python -m unittest discover -s tests -p checks.py -v`。
带 TODO 的文件要先补完。请用教学数据，不要填真实订单、账号、数据库或集群密钥。

## 在线跑真实 Kubernetes
1. 在你自己的 GitHub 新仓库放入解压后的文件（包括隐藏的 `.github/workflows/order-lab.yml`）。用你自己的 Actions runner 配额；不需要创建云主机或提供 Kubernetes 密钥。
2. 推送到 main。在 Actions 打开 **My order service — real Kubernetes**。如果没自动开始，选择 **Run workflow**。
3. 打开 **Observe readiness and replacement** 步骤，或从 Artifacts 下载 **order-observation**。对照两次 Pod UID、探针结果、重新读取同一笔订单、重复提交后的库存。
4. 测试集群临时存在，流程结束后清理；它不提供公网生产站点。不要把此配置用在生产服务。

已有专用本地 kind 教学集群，也可以运行
`python run_cluster.py --project-dir . --context kind-order-lab --image python:3.12-slim`。

工作流还会在独立的无集群任务里，使用真实证据复跑你的 `observe.py`。两项结果分开：集群独立验证通过，表示探针、换 Pod、旧小票与新订单都核实；分析器通过，表示你的代码正确解释了那次真实实验。下载 `order-observation` 后也可在导出目录执行 `PYTHONPATH=. python tests/checks.py --observation order-observation.json` 自己核对。

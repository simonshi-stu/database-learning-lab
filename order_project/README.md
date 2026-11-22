# 从零构建订单服务

先完成 SQL、主外键、条件扣减、提交与回滚、重试的基础课。这个项目把这些概念装进同一个订单服务：先做能下单的小系统，再部署、制造一次故障，最后用实际观察证明它恢复了。

| 章节 | 你完成的文件 | 通过这一关后可以解释 |
| --- | --- | --- |
| 1 建表 | `schema.sql` | 为什么重复初始化不能把库存改回 5？ |
| 2 写事务 | `service.py` | 为什么扣库存和写小票必须一起提交？什么是同键重试？ |
| 3 接入 HTTP | `api.py` | 客户端如何下单、重试和区分失败类型？ |
| 4 健康检查 | `health.py` | 为什么进程在线也可能没准备好接请求？ |
| 5 部署 | `deploy.json` | 谁重建 Pod？如何把流量发给合适的实例？数据放哪里？ |
| 6 故障演练 | `observe.py` | 如何证明换了 Pod，并证明原订单小票和库存仍在？ |

每章读目标与示例，在起始代码的 TODO 写一小段，再跑用例。参考实现用来定位卡点；最终导出时应组装自己的文件。浏览器里的第四至六章测试检查探针逻辑、清单结构和证据分析，不能代替 Kubernetes 实测。

## 运行本地参考实现

用 Python 3.11+，无第三方应用依赖：

```sh
PYTHONPATH=order_project/reference python -m unittest discover -s order_project/tests -p checks.py
python order_project/reference/api.py
```

第二条命令会在当前目录创建 `orders.db` 并监听 `127.0.0.1:8080`。另开窗口：

```sh
curl http://127.0.0.1:8080/ready
curl -i -H 'Content-Type: application/json' -H 'Idempotency-Key: req-1' \
  -d '{"sku":"A","qty":1}' http://127.0.0.1:8080/orders
```

第一次返回 HTTP 201、库存剩余 4；同样命令再次执行应 HTTP 200、返回相同小票且标记 `replayed: true`。换一个键才产生下一笔订单。不要把实验 HTTP 服务器直接暴露给公网。

## 在真实教学集群验证

准备 Docker、kind 和 kubectl。使用专用 kind 集群，不要用生产上下文。需要可供单节点实验使用的默认 StorageClass，以及能够拉取或提前导入 Python 镜像。

```sh
kind create cluster --name order-lab --wait 90s
kubectl --context kind-order-lab get storageclass
python order_project/run_cluster.py --context kind-order-lab
```

想跑自己导出的代码，给 runner 加 `--project-dir /path/to/export`。仅运行你自己信任的代码，部署远程代码的运行环境与本机账号应隔离。

脚本在随机命名的教学命名空间运行：创建代码 ConfigMap、PVC、Service、Deployment；等待 Pod 的 Ready 条件并验证 Service 的 EndpointSlice 是否已列出该 Pod；经仅绑定本机的 port-forward 发起探针与真实订单；删除本次教学 Pod；等待另一个 UID 就绪；验证同一 PVC 的声明 UID、原请求的重放、剩余库存从 4 变成 3。输出 JSON 行，`result.passed=true` 才代表通过。拉镜像或绑定卷失败会显示实测失败及诊断，示意记录不会计为通过。默认会请求清理该实验命名空间；`--keep` 仅用于保留实验资源便于手动检查。

学生可以用仓库里的 kind 工作流在自己的 GitHub Actions 运行，不需要向课程网页提供 kubeconfig。课堂示例只演示应用 Pod 故障和单节点的磁盘保留，不承诺节点故障恢复或零停机；扩成多副本之前，需要更换数据库部署方式。

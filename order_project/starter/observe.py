"""读懂运行记录后再下结论。测试夹具不是实时集群。"""


def analyze(before, after):
    # before: uid, claim_uid, live, ready, order_status, order 小票
    # after: uid, claim_uid, live, ready, replay_status, replay,
    # new_status, new_order（同一个 SKU 新买 1 件）
    # TODO 1: 前后 /live 和 /ready 都应返回 200。
    # TODO 2: 两个非空 Pod UID 不同，且持久卷声明 UID 相同。
    # TODO 3: 旧请求重试返回原小票，新请求只再扣 1 件。
    return {"healthy": False, "new_pod": False, "same_volume": False, "persisted": False}

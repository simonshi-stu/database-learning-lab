"""Evaluate evidence from the real cluster exercise; do not confuse logs with proof."""


def analyze(before, after):
    before_order, replay, second = before.get("order"), after.get("replay"), after.get("new_order")
    before_order, replay, second = before_order or {}, replay or {}, second or {}
    before_uid, after_uid = before.get("uid"), after.get("uid")
    before_claim, after_claim = before.get("claim_uid"), after.get("claim_uid")
    remaining = before_order.get("stock_left")
    return {
        "healthy": all(m.get(name) == 200 for m in (before, after) for name in ("live", "ready")),
        "new_pod": bool(before_uid and after_uid and before_uid != after_uid),
        "same_volume": bool(before_claim and before_claim == after_claim),
        "persisted": (before.get("order_status") == 201
                      and after.get("replay_status") == 200 and replay.get("replayed") is True
                      and before_order.get("replayed") is False
                      and bool(before_order.get("request_key"))
                      and all(replay.get(k) == before_order.get(k) for k in ("request_key", "sku", "qty", "stock_left"))
                      and after.get("new_status") == 201
                      and bool(second.get("request_key"))
                      and second["request_key"] != before_order["request_key"]
                      and second.get("sku") == before_order.get("sku")
                      and type(second.get("qty")) is int and second["qty"] == 1
                      and second.get("replayed") is False
                      and type(remaining) is int
                      and second.get("stock_left") == remaining - 1),
    }

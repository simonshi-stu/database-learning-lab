"""固定合成样本；期望值独立写在 acceptance.py，不在这里由 SQL 推导。"""
from copy import deepcopy

EXPECTED_SALES_DAILY = {"2026-01-07": 9900, "2026-01-08": -4900}
EXPECTED_SALES_TOTAL = 5000
EXPECTED_WRONG_TOTAL = 14900
EXPECTED_HOTEL_NIGHTS = {"2026-01-07": 1, "2026-01-08": 2, "2026-01-09": 1}


def sales_event(event_id, order_id, kind, amount_cents, event_time, arrived_at, **extra):
    row = {
        "event_id": event_id,
        "order_id": order_id,
        "kind": kind,
        "amount_cents": amount_cents,
        "event_time": event_time,
        "arrived_at": arrived_at,
        "source": "sales",
        "schema_version": 1,
        "payload": {"store_id": "SHOP-01"},
    }
    row.update(extra)
    return row


def hotel_event(event_id, booking_id, check_in, check_out, arrived_at, **extra):
    row = {
        "event_id": event_id,
        "booking_id": booking_id,
        "kind": "hotel_booking",
        "amount_cents": 0,
        "event_time": check_in + "T00:00:00+08:00",
        "arrived_at": arrived_at,
        "source": "hotel",
        "schema_version": 1,
        "payload": {
            "room_id": "R101" if booking_id == "b1" else "R102",
            "check_in": check_in,
            "check_out": check_out,
        },
    }
    row.update(extra)
    return row


def baseline_batch():
    rows = [
        sales_event("p1", "o1", "payment", 9900,
                    "2026-01-07T10:00:00+08:00", "2026-01-07T10:00:01+08:00"),
        sales_event("p2", "o2", "payment", 5000,
                    "2026-01-08T10:00:00+08:00", "2026-01-08T10:00:01+08:00"),
        sales_event("r1", "o1", "refund", 9900,
                    "2026-01-08T09:00:00+08:00", "2026-01-09T00:00:01+08:00"),
    ]
    return rows


def sales_batch(duplicate=True):
    rows = baseline_batch()
    if duplicate:
        retry = deepcopy(rows[0])
        retry["arrived_at"] = "2026-01-09T00:00:02+08:00"
        rows.append(retry)
    return rows


def hotel_batch():
    return [
        hotel_event("h1", "b1", "2026-01-07", "2026-01-09", "2026-01-06T12:00:00+08:00"),
        hotel_event("h2", "b2", "2026-01-08", "2026-01-10", "2026-01-07T12:00:00+08:00"),
    ]


def conflict_batch():
    row = deepcopy(baseline_batch()[0])
    row["amount_cents"] = 9901
    row["arrived_at"] = "2026-01-10T00:00:02+08:00"
    return [row]


def bad_batch():
    missing = sales_event("bad-1", "o9", "payment", 100,
                          "2026-01-08T10:00:00+08:00", "2026-01-08T10:00:01+08:00")
    del missing["amount_cents"]
    boolean = sales_event("bad-2", "o9", "payment", True,
                          "2026-01-08T10:00:00+08:00", "2026-01-08T10:00:01+08:00")
    wrong_date = sales_event("bad-3", "o9", "payment", 100,
                             "not-a-date", "2026-01-08T10:00:01+08:00")
    return [missing, boolean, wrong_date]


def late_batch():
    return [sales_event("p3", "o3", "payment", 1200,
                        "2026-01-06T23:30:00+08:00", "2026-01-09T08:00:00+08:00")]


def utc_batch():
    return [sales_event("p-utc", "o-utc", "payment", 700,
                        "2026-01-07T16:30:00+00:00", "2026-01-08T00:30:00+08:00")]


def nullable_batch():
    row = sales_event("p4", "o4", "payment", 800,
                      "2026-01-09T10:00:00+08:00", "2026-01-09T10:00:01+08:00")
    row["campaign_id"] = None
    return [row]

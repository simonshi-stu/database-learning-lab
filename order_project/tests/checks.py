"""Bounded test cases shared between the browser's disposable worker and local CLI."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
from tempfile import TemporaryDirectory
import unittest


WORK = Path.cwd()
HERE = Path(__file__).resolve().parent if "__file__" in globals() else WORK
# Browser runs in its virtual project directory, with files at the root. The
# repository's command points module imports at order_project/reference.
PROJECT = WORK if (WORK / "schema.sql").exists() else HERE.parent / "reference"


class SchemaChecks(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.sql = (PROJECT / "schema.sql").read_text(encoding="utf-8")
        self.db.executescript(self.sql)

    def test_01_seed_is_created_once(self):
        """重跑建表脚本不会把 A 的剩余库存重置为 5。"""
        self.assertEqual(self.db.execute("SELECT qty FROM inventory WHERE sku='A'").fetchone(), (5,))
        self.db.execute("UPDATE inventory SET qty=4 WHERE sku='A'")
        self.db.executescript(self.sql)
        self.assertEqual(self.db.execute("SELECT qty FROM inventory WHERE sku='A'").fetchone(), (4,))

    def test_02_inventory_rejects_invalid_quantities(self):
        """负库存和非整数量应由数据库拒绝。"""
        for qty in (-1, 1.5):
            with self.subTest(qty=qty), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute("UPDATE inventory SET qty=? WHERE sku='A'", (qty,))

    def test_03_order_constraints(self):
        """小票必须关联已有 SKU，且同一请求键只有一份。"""
        self.db.execute("INSERT INTO orders(request_key,sku,qty,stock_left) VALUES ('rq-1','A',1,4)")
        for args in (("rq-1","A",1,4), ("rq-2","unknown",1,4), ("rq-3","A",0,4)):
            with self.subTest(args=args), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute("INSERT INTO orders(request_key,sku,qty,stock_left) VALUES (?,?,?,?)", args)


class OrderDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "orders.sqlite"
        from service import connect, init_db
        self.connect = connect
        init_db(self.db)

    def snapshot(self):
        with closing(self.connect(self.db)) as conn:
            return conn.execute("SELECT qty FROM inventory WHERE sku='A'").fetchone()[0], conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


class ServiceChecks(OrderDatabase):
    def test_01_first_request_deducts_stock(self):
        """首单 HTTP 201；初始库存 5、买 2，剩余为 3。"""
        from service import place_order
        status, receipt = place_order(self.db, "one", "A", 2)
        self.assertEqual((status, receipt["stock_left"], receipt["replayed"]), (201, 3, False))
        self.assertEqual(self.snapshot(), (3, 1))

    def test_02_replay_then_mismatch(self):
        """同键同参返原小票，同键不同数量返回冲突。"""
        from service import place_order
        first_status, first = place_order(self.db, "one", "A", 2)
        self.assertEqual(first_status, 201)
        self.assertEqual(place_order(self.db, "other", "A", 1)[0], 201)
        self.assertEqual(place_order(self.db, "one", "A", 2), (200, {**first, "replayed": True}))
        self.assertEqual(place_order(self.db, "one", "A", 1), (409, {"error": "key_conflict"}))
        self.assertEqual(self.snapshot(), (2, 2))

    def test_03_missing_sku_and_sold_out(self):
        """未知商品和库存不足失败时都不生成订单。"""
        from service import place_order
        self.assertEqual(place_order(self.db, "missing", "Z", 1), (404, {"error": "unknown_sku"}))
        self.assertEqual(place_order(self.db, "too-many", "A", 6), (409, {"error": "insufficient_stock"}))
        self.assertEqual(self.snapshot(), (5, 0))

    def test_04_receipt_error_rolls_back_deduction(self):
        """写小票时数据库拒绝，前一步库存扣减一起回滚。"""
        from service import place_order
        with closing(self.connect(self.db)) as conn:
            conn.execute("CREATE TRIGGER stop_order BEFORE INSERT ON orders BEGIN SELECT RAISE(ABORT, 'test fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            place_order(self.db, "will-fail", "A", 1)
        self.assertEqual(self.snapshot(), (5, 0))


class ApiChecks(OrderDatabase):
    def test_01_create_and_retry_over_contract(self):
        """POST 下单返回 201，重复相同请求返回 200。"""
        from api import dispatch
        args = ("POST", "/orders", {"Idempotency-Key": "req-1"}, b'{"sku":"A","qty":2}', self.db)
        status, receipt = dispatch(*args)
        self.assertEqual((status, receipt["stock_left"]), (201, 3))
        self.assertEqual(dispatch(*args), (200, {**receipt, "replayed": True}))
        self.assertEqual(self.snapshot(), (3, 1))

    def test_02_invalid_requests_have_no_side_effect(self):
        """缺键、坏 JSON、布尔数量等输入不会写库。"""
        from api import dispatch
        key = {"Idempotency-Key": "r1"}
        for headers, body, code in [
            ({}, b'{"sku":"A","qty":1}', "invalid_key"),
            (key, b"{", "invalid_json"),
            (key, b'{"sku":"A","qty":true}', "invalid_order"),
            (key, b'{"sku":"A","qty":0}', "invalid_order"),
        ]:
            with self.subTest(body=body):
                self.assertEqual(dispatch("POST", "/orders", headers, body, self.db), (400, {"error": code}))
        self.assertEqual(self.snapshot(), (5, 0))

    def test_03_route_and_body_limits(self):
        """错误路由和超大请求有明确响应。"""
        from api import dispatch
        self.assertEqual(dispatch("GET", "/orders", {}, b"", self.db)[0], 405)
        self.assertEqual(dispatch("GET", "/bad", {}, b"", self.db)[0], 404)
        self.assertEqual(dispatch("POST", "/orders", {}, b" " * 5000, self.db)[0], 413)


class HealthChecks(OrderDatabase):
    def test_01_healthy_database(self):
        """初始化完成后，/live 和 /ready 均成功。"""
        from health import health_result
        self.assertEqual(health_result("/live", self.db), (200, {"status": "alive"}))
        self.assertEqual(health_result("/ready", self.db), (200, {"status": "ready"}))

    def test_02_missing_or_uninitialized_database(self):
        """缺文件不应变成健康；不存在的数据库不能被探针新建。"""
        from health import health_result
        missing = self.db.with_name("absent.sqlite")
        self.assertEqual(health_result("/live", missing)[0], 200)
        self.assertEqual(health_result("/ready", missing), (503, {"status": "not_ready"}))
        self.assertFalse(missing.exists())
        sqlite3.connect(missing).close()
        self.assertEqual(health_result("/ready", missing)[0], 503)


class DeploymentChecks(unittest.TestCase):
    def setUp(self):
        manifest = json.loads((PROJECT / "deploy.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["kind"], "List")
        self.objects = {r["kind"]: r for r in manifest["items"]}
        self.deployment = self.objects["Deployment"]["spec"]
        self.pod = self.deployment["template"]["spec"]
        self.container = self.pod["containers"][0]

    def test_01_routing_and_probes(self):
        """选择器相同，Service 只把请求送到 Ready Pod。"""
        service = self.objects["Service"]["spec"]
        self.assertEqual(service["type"], "ClusterIP")
        self.assertEqual(service["selector"], self.deployment["template"]["metadata"]["labels"])
        self.assertEqual(service["ports"][0]["targetPort"], self.container["ports"][0]["name"])
        self.assertEqual(self.container["livenessProbe"]["httpGet"]["path"], "/live")
        self.assertEqual(self.container["readinessProbe"]["httpGet"]["path"], "/ready")

    def test_02_persisted_volume(self):
        """应用容器替换时保留数据库所挂载的 PVC。"""
        claims = {v["name"]: (v.get("persistentVolumeClaim") or {}).get("claimName") for v in self.pod["volumes"]}
        mount = next(v for v in self.container["volumeMounts"] if v["mountPath"] == "/data")
        self.assertEqual(claims.get(mount["name"]), self.objects["PersistentVolumeClaim"]["metadata"]["name"])
        self.assertEqual(self.deployment["replicas"], 1)
        self.assertEqual(self.deployment["strategy"]["type"], "Recreate")


class RecoveryChecks(unittest.TestCase):
    def fixture(self):
        order = {"request_key":"one", "sku":"A", "qty":1, "stock_left":4, "replayed":False}
        return (dict(uid="pod-one", claim_uid="same-volume", live=200, ready=200, order_status=201, order=order),
                dict(uid="pod-two", claim_uid="same-volume", live=200, ready=200, replay_status=200,
                     replay={**order, "replayed":True}, new_status=201, new_order={"stock_left": 3}))

    def test_01_positive_evidence(self):
        """所有证据齐全：探针成功、Pod 换代、小票和库存仍在。"""
        from observe import analyze
        self.assertEqual(analyze(*self.fixture()), dict(healthy=True, new_pod=True, same_volume=True, persisted=True))

    def test_02_absent_or_misleading_evidence(self):
        """进程活着不足以证明就绪；只换了名字也不足以证明换代。"""
        from observe import analyze
        before, after = self.fixture()
        after.update(uid=before["uid"], claim_uid=None, ready=503)
        after["replay"]["stock_left"] = 5
        self.assertEqual(analyze(before, after), dict(healthy=False, new_pod=False, same_volume=False, persisted=False))
        self.assertFalse(any(analyze({}, {}).values()))

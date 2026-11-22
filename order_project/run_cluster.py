"""Run the order lab against an explicitly selected, disposable kind context."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen
from uuid import uuid4


class Lab:
    def __init__(self, context, directory, kubectl):
        self.base = [kubectl, "--context", context]
        self.context = context
        self.directory = directory
        self.namespace = "order-lab-" + uuid4().hex[:10]

    def command(self, *args, namespace=True, stdin=None, timeout=30):
        prefix = self.base + (["-n", self.namespace] if namespace else [])
        result = subprocess.run([*prefix, *args], input=stdin, text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"kubectl {args[0]} failed: {result.stderr.strip()[:1200]}")
        return result.stdout

    def obj(self, *args):
        return json.loads(self.command("get", *args, "-o", "json"))

    @contextmanager
    def forward(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "forward.log"
            cmd = [*self.base, "-n", self.namespace, "port-forward", "--address", "127.0.0.1", "svc/orders", ":8080"]
            with path.open("wb") as log, subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT) as proc:
                try:
                    deadline = time.monotonic() + 15
                    output = ""
                    while time.monotonic() < deadline:
                        output = path.read_text(encoding="utf-8", errors="replace")
                        match = re.search(r"Forwarding from 127\.0\.0\.1:(\d+)", output)
                        if match:
                            yield "http://127.0.0.1:" + match.group(1)
                            return
                        if proc.poll() is not None:
                            break
                        time.sleep(0.2)
                    raise RuntimeError(f"Port-forward did not start: {output[:600]}")
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()

    def readiness(self, prior_uid=None):
        deadline, seen = time.monotonic() + 90, set()
        while time.monotonic() < deadline:
            for pod in self.obj("pods", "-l", "app=order-lab")["items"]:
                uid = pod["metadata"]["uid"]
                if uid == prior_uid or pod["metadata"].get("deletionTimestamp"):
                    continue
                ready = any(c["type"] == "Ready" and c["status"] == "True" for c in pod.get("status", {}).get("conditions", []))
                state = (uid, ready)
                if state not in seen:
                    emit("pod_state", pod=pod["metadata"]["name"], uid=uid, ready=ready)
                    seen.add(state)
                if ready:
                    return pod
            time.sleep(0.6)
        raise RuntimeError("No Ready replacement Pod before deadline. Check image pull, scheduling and PVC binding.")

    def wait_service(self, uid):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            slices = self.obj("endpointslices", "-l", "kubernetes.io/service-name=orders")["items"]
            if any(
                endpoint.get("targetRef", {}).get("uid") == uid and endpoint.get("conditions", {}).get("ready") is True
                for item in slices for endpoint in item.get("endpoints", [])
            ):
                emit("service_endpoint_ready", uid=uid)
                return True
            time.sleep(0.5)
        raise RuntimeError("The Service did not publish the Ready Pod as an endpoint; check selector and target port.")

    def deploy(self, image):
        args = ["create", "configmap", "orders-code"]
        for fname in ("api.py", "schema.sql", "service.py", "health.py"):
            args.append(f"--from-file={fname}={self.directory / fname}")
        self.command(*args)
        manifest = json.loads((self.directory / "deploy.json").read_text(encoding="utf-8"))
        # Accept only the lab's three expected object shapes and no caller-provided namespace.
        if manifest.get("kind") != "List" or sorted((o.get("kind"), o.get("metadata", {}).get("name")) for o in manifest["items"]) != [("Deployment","orders"),("PersistentVolumeClaim","orders-data"),("Service","orders")]:
            raise RuntimeError("Expected one orders Deployment, orders-data PVC, and orders Service.")
        for item in manifest["items"]:
            if item["metadata"].get("namespace", self.namespace) != self.namespace:
                raise RuntimeError("Do not specify another namespace in the project files.")
            item["metadata"]["namespace"] = self.namespace
            if item["kind"] == "Deployment":
                item["spec"]["template"]["spec"]["containers"][0]["image"] = image
        self.command("apply", "-f", "-", stdin=json.dumps(manifest))


def emit(event, **payload):
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


def request(url, path, key=None):
    if key:
        req = Request(url + path, data=b'{"sku":"A","qty":1}', headers={"Content-Type":"application/json","Idempotency-Key":key})
    else:
        req = Request(url + path)
    with urlopen(req, timeout=10) as resp:
        return resp.status, json.load(resp)


def run(lab, image):
    lab.deploy(image)
    pod = lab.readiness()
    uid = pod["metadata"]["uid"]
    lab.wait_service(uid)
    first_claim = lab.obj("pvc", "orders-data")["metadata"]["uid"]
    with lab.forward() as url:
        live, _ = request(url, "/live")
        ready, _ = request(url, "/ready")
        order_status, order = request(url, "/orders", "one")
    before = dict(uid=uid, claim_uid=first_claim, live=live, ready=ready, order_status=order_status, order=order)
    emit("before", **before)
    lab.command("delete", "pod", pod["metadata"]["name"], "--wait=false")
    emit("delete_requested", uid=uid)
    replacement = lab.readiness(prior_uid=uid)
    lab.wait_service(replacement["metadata"]["uid"])
    second_claim = lab.obj("pvc", "orders-data")["metadata"]["uid"]
    with lab.forward() as url:
        live, _ = request(url, "/live")
        ready, _ = request(url, "/ready")
        replay_status, replay = request(url, "/orders", "one")
        new_status, new_order = request(url, "/orders", "two")
    after = dict(uid=replacement["metadata"]["uid"], claim_uid=second_claim, live=live, ready=ready, replay_status=replay_status, replay=replay, new_status=new_status, new_order=new_order)
    emit("after", **after)
    # Independently grade real observations; learner code cannot decide success.
    checks = {
        "healthy": all(m[which] == 200 for m in (before, after) for which in ("live", "ready")),
        "new_pod": before["uid"] != after["uid"],
        "same_volume": before["claim_uid"] == after["claim_uid"],
        "persisted": (
            order_status == 201 and replay_status == 200 and new_status == 201
            and replay.get("replayed") is True and order.get("request_key") == "one"
            and all(replay.get(k) == order.get(k) for k in ("request_key", "sku", "qty", "stock_left"))
            and new_order.get("request_key") == "two"
            and order.get("stock_left") == 4 and new_order.get("stock_left") == 3
        ),
    }
    emit("result", passed=all(checks.values()), checks=checks)
    return all(checks.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="A dedicated kind-* teaching cluster context")
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument("--image", default="python:3.12-slim")
    parser.add_argument("--project-dir", type=Path, default=(Path(__file__).resolve().parent / "reference"
                          if (Path(__file__).resolve().parent / "reference").is_dir()
                          else Path(__file__).resolve().parent))
    parser.add_argument("--keep", action="store_true", help="Leave generated namespace for inspection")
    opt = parser.parse_args()
    if not re.fullmatch(r"kind-[a-z0-9][a-z0-9-]*", opt.context):
        parser.error("Use a dedicated kind-* context; never production.")
    lab = Lab(opt.context, opt.project_dir.resolve(), opt.kubectl)
    created = False
    try:
        lab.command("create", "namespace", lab.namespace, namespace=False)
        created = True
        emit("namespace", name=lab.namespace)
        return 0 if run(lab, opt.image) else 1
    except Exception as exc:
        emit("error", error=str(exc))
        if created:
            for query in (("get","pods,pvc","-o","wide"), ("get","events","--sort-by=.lastTimestamp")):
                try:
                    emit("diagnostics", output=lab.command(*query, timeout=10)[-2500:])
                except Exception:
                    pass
        return 1
    finally:
        if created and not opt.keep:
            try:
                lab.command("delete","namespace",lab.namespace,"--wait=false",namespace=False)
                emit("cleanup_requested", namespace=lab.namespace)
            except Exception as exc:
                emit("cleanup_failed", namespace=lab.namespace, error=str(exc))


if __name__ == "__main__":
    raise SystemExit(main())

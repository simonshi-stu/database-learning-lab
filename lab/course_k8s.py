"""路线 C · K8s 五关。有 kind 集群则实测；无则可读示意+安装入口，实测标跳过。

k1 用本地已有镜像真跑容器讲镜像/容器/端口；k2-k5 无集群时模拟 Pod 生命周期（标示意/未实测）。
学生改 YAML（副本/探针）驱动模拟或真实集群；Web 不执行任意命令。
"""
import json
import re
import subprocess
import time
import uuid

from .course_engines import case, step, ENGINES

CTX = 'kind-lab-cloud'
INSTALL = ('安装入口：1) Docker Desktop 已装；2) 下载 kind.exe 放入 bin/；'
           '3) 因 Docker Hub 不可达，需经镜像代理拉取 kindest/node:v1.30.0 后 '
           '`bin\\kind.exe create cluster --name lab-cloud`；4) kubectl 已随 Docker 提供。')


def _kubectl(*args):
    return subprocess.run(['kubectl', '--context', CTX, *args], capture_output=True, timeout=60)


def _cluster_ok():
    p = _kubectl('get', 'nodes', '--no-headers')
    return p.returncode == 0 and b'Ready' in p.stdout


def _parse_yaml(code):
    rep = re.search(r'replicas:\s*(\d+)', code or '')
    ready = 'readinessProbe' in (code or '')
    live = 'livenessProbe' in (code or '')
    bad = 'BAD_CONFIG' in (code or '') or 'image: wrong' in (code or '')
    return dict(replicas=int(rep.group(1)) if rep else 1, readiness=ready, liveness=live, bad=bad)


def _run_k8s(lesson, code, run_dir, choice='', scenario=''):
    kc = lesson.get('k8s_case', 'k1')
    y = _parse_yaml(code)
    steps = []
    extra = dict(install=INSTALL)
    if kc == 'k1':
        img = 'postgres:17-alpine'
        have = subprocess.run(['docker', 'image', 'inspect', img], capture_output=True, timeout=30).returncode == 0
        if not have:
            return [case('容器运行同一API', None, error=None, steps=[],
                         extra=dict(not_measured='跳过：本地无可用镜像且 registry 不可达。' + INSTALL))]
        name = 'lab-cloud-k1-' + uuid.uuid4().hex[:6]
        run = subprocess.run(['docker', 'run', '-d', '--name', name, '-p', '55434:5432',
                              '-e', 'POSTGRES_PASSWORD=lab', img], capture_output=True, timeout=60)
        cid = run.stdout.decode().strip()[:12]
        steps.append(step('docker run（镜像→容器）', 'committed', {}, None,
                          f'镜像{img} 容器{cid} 端口55434→5432', 'docker'))
        time.sleep(2)
        ps = subprocess.run(['docker', 'ps', '--filter', f'name={name}', '--format', '{{.Status}}'], capture_output=True, timeout=30)
        steps.append(step('容器状态', 'read', {'状态': [dict(status=ps.stdout.decode().strip())]}, None, '端口映射生效', 'docker'))
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
        steps.append(step('删除容器（镜像仍在）', 'committed', {}, None, '容器可删镜像不变', 'docker'))
        passed = run.returncode == 0 and 'Up' in ps.stdout.decode()
        return [case('镜像/容器/端口', passed, expected='容器Up 端口映射', actual=ps.stdout.decode().strip(),
                     steps=steps, extra=extra)]
    # k2-k5
    if not _cluster_ok():
        # 可读示意 + 跳过
        uid_old = 'uid-' + uuid.uuid4().hex[:8]
        uid_new = 'uid-' + uuid.uuid4().hex[:8]
        if kc == 'k2':
            steps.append(step(f'示意：Deployment replicas={y["replicas"]} → {y["replicas"]} 个 Pod', 'illustrative', {}, None, '示意/未实测', 'kind'))
            msg = f'示意 replicas={y["replicas"]}；无集群未实测。' + INSTALL
        elif kc == 'k3':
            steps.append(step(f'示意：删除 Pod {uid_old}', 'illustrative', {}, None, '示意/未实测', 'kubectl'))
            steps.append(step(f'示意：新 Pod {uid_new} 补位；持续请求有成功也有失败（不预设零中断）', 'illustrative', {}, None, '示意/未实测', 'controller'))
            msg = f'示意 UID {uid_old}→{uid_new}；无集群未实测。' + INSTALL
        elif kc == 'k4':
            steps.append(step(f'示意：readiness={y["readiness"]} 不通过→移出 endpoints；liveness={y["liveness"]} 失败→重启', 'illustrative', {}, None, '示意/未实测', 'kubelet'))
            msg = '示意探针行为；无集群未实测。' + INSTALL
        else:
            steps.append(step(f'示意：坏配置={y["bad"]} rollout 卡住→手动回滚→订单仍在（SQL 不依赖容器文件系统）', 'illustrative', {}, None, '示意/未实测', 'rollout'))
            msg = '示意 rollout/回滚；无集群未实测。' + INSTALL
        return [case(lesson['title'], None, error=None, steps=steps,
                     extra=dict(not_measured='跳过：' + msg, uid_old=uid_old, uid_new=uid_new))]
    # 真实集群路径（有 kind 时）
    ns = 'lab-cloud'
    _kubectl('create', 'namespace', ns)
    manifest = f"""apiVersion: apps/v1
kind: Deployment
metadata: {{name: web, namespace: {ns}}}
spec:
  replicas: {y['replicas']}
  selector: {{matchLabels: {{app: web}}}}
  template:
    metadata: {{labels: {{app: web}}}}
    spec:
      containers:
      - name: web
        image: postgres:17-alpine
        env: [{{name: POSTGRES_PASSWORD, value: lab}}]
        ports: [{{containerPort: 5432}}]
"""
    (run_dir / 'deploy.yaml').write_text(manifest, encoding='utf-8')
    _kubectl('apply', '-f', str(run_dir / 'deploy.yaml'))
    _kubectl('rollout', 'status', 'deployment/web', '-n', ns, '--timeout=90s')
    pods = _kubectl('get', 'pods', '-n', ns, '-o', 'json')
    items = json.loads(pods.stdout or '{}').get('items', [])
    uids = [i['metadata']['uid'] for i in items]
    steps.append(step(f'真实 Deployment replicas={y["replicas"]}', 'committed', {'pods': [dict(uid=u) for u in uids]}, None, '真实集群', 'kind'))
    if kc == 'k3' and uids:
        _kubectl('delete', 'pod', items[0]['metadata']['name'], '-n', ns, '--wait=false')
        time.sleep(6)
        pods2 = _kubectl('get', 'pods', '-n', ns, '-o', 'json')
        uids2 = [i['metadata']['uid'] for i in json.loads(pods2.stdout or '{}').get('items', [])]
        steps.append(step('删Pod后新UID', 'committed', {'pods': [dict(uid=u) for u in uids2]}, None, f'旧{uids[0]} 新{uids2}', 'controller'))
        passed = len(uids2) >= 1 and uids2[0] != uids[0]
        return [case('删Pod观察补副本', passed, expected='新UID≠旧UID', actual=str(uids2), steps=steps, extra=extra)]
    return [case(lesson['title'], len(uids) == y['replicas'], expected=f'{y["replicas"]} pods', actual=str(len(uids)), steps=steps, extra=extra)]


ENGINES.update({'k8s': _run_k8s})
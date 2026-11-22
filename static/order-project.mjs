import {makeZip} from "./project-zip.mjs";
const $ = id => document.getElementById(id);
const KEY = "order-project-workspace-v1";
let db, current, changeLock = false, worker, serial = 0;
let state = {drafts: {}, passed: {}, last: null};
try { const saved = JSON.parse(localStorage.getItem(KEY)); if (saved?.drafts && saved?.passed) state = saved; } catch { /* start clean */ }
const editor = globalThis.CodeMirror?.fromTextArea($("project-editor"), {lineNumbers:true, indentUnit:4, tabSize:4, lineWrapping:true, theme:"default"});
const read = () => editor?.getValue() ?? $("project-editor").value;
function set(value) {
  changeLock = true;
  try {
    if (editor) { editor.setOption("mode", current.file.endsWith(".py") ? "python" : current.file.endsWith(".sql") ? "text/x-sql" : {name:"javascript", json:true}); editor.setValue(value); editor.clearHistory(); }
    else $("project-editor").value = value;
  } finally { changeLock = false; }
}
function save() {
  try { localStorage.setItem(KEY, JSON.stringify(state)); $("project-save").textContent = "已保存在此浏览器"; }
  catch { $("project-save").textContent = "未能保存，请下载备份"; }
}
const stopWorker = () => { serial++; worker?.terminate(); worker = null; };
function remember() { if (current && !changeLock) { state.drafts[current.id] = read(); save(); } }
if (editor) editor.on("change", remember); else $("project-editor").addEventListener("input", remember);
const before = ch => db.chapters.slice(0, db.chapters.indexOf(ch));
const missing = ch => before(ch).filter(c => !Object.hasOwn(state.passed, c.id));
function notice(msg) { $("project-notice").hidden = !msg; $("project-notice").textContent = msg || ""; }
function list(id, items) {
  const container = $(id); container.replaceChildren();
  for (const item of items ?? []) { const li = document.createElement("li"); li.textContent = item; container.append(li); }
}
const bridges = [
  [["00b","建表与主键"],["00c","插入第一笔库存"]],
  [["wait","并发等待"],["d2","重试同一订单"]],
  [["order","从请求到订单"],["pg-build-sales","业务代码综合题"]],
  [["d1","进程与端口"],["k1","镜像和容器"]],
  [["k2","认识 Deployment 与 Service"],["k4","探针和流量"]],
  [["k3","观察 Pod 替换"],["exit","进程退出与数据是否保留"]]
];
function nav() {
  const count = db.chapters.filter(ch => Object.hasOwn(state.passed, ch.id)).length;
  $("project-progress").textContent = `已通过 ${count} / ${db.chapters.length} 章`;
  $("project-bar").max = db.chapters.length; $("project-bar").value = count;
  $("project-nav").replaceChildren();
  db.chapters.forEach((ch, i) => {
    const b = document.createElement("button"), n = document.createElement("span"), title = document.createElement("span"), f = document.createElement("small");
    b.className = "nav-item"; n.className = "nav-num"; n.textContent = Object.hasOwn(state.passed, ch.id) ? "✓" : String(i+1).padStart(2,"0");
    if (ch === current) b.setAttribute("aria-current", "step");
    title.textContent = ch.title; f.textContent = ch.file; title.append(f); b.append(n,title);
    b.onclick = () => { location.hash = ch.id; };
    $("project-nav").append(b);
  });
}
function status(s, output) { $("project-status").textContent = s; $("project-output").textContent = output; }
function show(ch) {
  if (!ch) return;
  stopWorker(); current = ch; state.last = ch.id; nav(); save();
  $("project-chapter").textContent = ch.title;
  $("project-filename").textContent = ch.file;
  $("project-goal").textContent = ch.goal;
  $("project-example").textContent = typeof ch.example === "string" ? ch.example : JSON.stringify(ch.example, null, 2);
  list("project-concepts", ch.concepts); list("project-acceptance", ch.acceptance);
  const bridge = $("project-bridge"); bridge.replaceChildren();
  (bridges[db.chapters.indexOf(ch)] ?? []).forEach(([id,title]) => {
    const node = document.createElement("a"); node.href = $("foundation-link").getAttribute("href").split("#")[0] + "#" + id;
    node.textContent = title; bridge.append(node);
  });
  set(state.drafts[ch.id] ?? ch.starter);
  $("reference-wrap").hidden = true; $("reference-wrap").open = false;
  $("project-run").disabled = false; $("project-next").disabled = ch === db.chapters.at(-1);
  const needs = missing(ch);
  notice(needs.length ? "先完成前置章再测试这章：" + needs.map(c=>c.title).join("、") : "");
  status(Object.hasOwn(state.passed, ch.id) ? "已有通过版本" : "未运行", "完成 TODO 后，点「运行本章测试」。");
}
$("project-reset").onclick = () => { if (current && confirm("把本章草稿重置为起始代码？已通过的版本仍保留。")) { set(current.starter); remember(); } };
$("project-next").onclick = () => { const ch = db.chapters[db.chapters.indexOf(current)+1]; if (ch) location.hash = ch.id; };
$("project-ref").onclick = () => { $("project-reference").textContent = current.reference; const wrap = $("reference-wrap"); wrap.hidden = false; wrap.open = true; wrap.scrollIntoView({block:"nearest"}); };
$("project-run").onclick = () => {
  const ch = current, candidate = read(), needs = missing(ch);
  if (needs.length) { status("请先完成前置章", "本章会用到：" + needs.map(c=>c.file).join("、")); return; }
  if (candidate.length > 24000) { status("未运行","当前文件超过 24,000 字符。"); return; }
  stopWorker(); const token = serial;
  const files = Object.fromEntries([...before(ch).map(c => [c.file,state.passed[c.id]]),[ch.file,candidate]]);
  status("运行中","正在准备本章用例。首次加载 Python 环境可能需要一点时间。");
  const button = $("project-run"); button.disabled = true;
  let finished = false;
  const finish = data => {
    if (finished) return;
    finished = true; clearTimeout(timeout);
    if (token !== serial || current !== ch) return;
    stopWorker(); button.disabled = false;
    if (data.error) { status("未完成",data.error); return; }
    if (data.ok) { state.passed[ch.id] = candidate; nav(); save(); }
    status(data.ok ? `通过 · ${data.total}/${data.total}` : `未通过 · ${data.total-data.failed-data.skipped}/${data.total}`, data.message);
  };
  const timeout = setTimeout(() => finish({error:"加载或执行超时，已停止本次运行。请检查网络，或检查代码中是否有无限循环。"}), 45000);
  try {
    worker = new Worker(new URL("./order-project-worker.mjs", import.meta.url), {type:"module"});
    worker.onmessage = e => finish(e.data);
    worker.onerror = () => finish({error:"无法加载浏览器测试运行器，可先下载项目并运行本地测试。"});
    worker.postMessage({files, checks:db.checks, test:ch.test});
  } catch (e) { finish({error:String(e)}); }
};
$("project-download").onclick = () => {
  const files = {...db.support, ...Object.fromEntries(db.chapters.map(ch => [ch.file,state.passed[ch.id] ?? state.drafts[ch.id] ?? ch.starter]))};
  const url = URL.createObjectURL(makeZip(files));
  const a = document.createElement("a"); a.href=url; a.download="order-service-project.zip"; a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1200);
};
function onHash() { const id=location.hash.slice(1); show(db.chapters.find(ch=>ch.id===id) ?? db.chapters[0]); }
try {
  const res = await fetch(document.querySelector('meta[name="project-data"]').content);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  db = await res.json();
  $("project-title").textContent = db.title; $("project-desc").textContent = db.description;
  $("project-ref").disabled = false; $("project-reset").disabled = false; $("project-download").disabled = false;
  if (!location.hash && db.chapters.some(ch=>ch.id===state.last)) history.replaceState(null,"","#"+state.last);
  window.addEventListener("hashchange",onHash); onHash();
} catch (e) { notice("打开项目失败：" + e.message + "。请刷新后再试。"); }

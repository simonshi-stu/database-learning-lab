'use strict';

const PK = {
  products: ['id'], inventory: ['product_id'], orders: ['id'],
  order_items: ['order_id', 'product_id'], stock_movements: ['id'],
  room_calendar: ['room_id', 'night'], auxiliary_inventory: ['night'],
  bookings: ['request_id'], booking_nights: ['request_id', 'night'],
  active_room_nights: ['room_id', 'night'], reservation_events: ['sequence'],
};

const state = {
  problems: [], meta: null, mode: 'python', result: null,
  timeline: [], idx: 0, playing: false, timer: null, drafts: {},
};
let cm = null;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function rowKey(table, row) {
  const pk = PK[table] || Object.keys(row);
  return pk.map((k) => String(row[k])).join('|');
}

function tableOfSql(sql) {
  let m = /insert\s+into\s+([a-z_]+)/i.exec(sql) || /update\s+([a-z_]+)/i.exec(sql)
    || /delete\s+from\s+([a-z_]+)/i.exec(sql) || /from\s+([a-z_]+)/i.exec(sql);
  return m ? m[1].toLowerCase() : null;
}

function diffTables(prev, cur) {
  const out = {};
  const tables = new Set([...Object.keys(prev || {}), ...Object.keys(cur || {})]);
  for (const t of tables) {
    const before = new Map((prev?.[t] || []).map((r) => [rowKey(t, r), r]));
    const after = new Map((cur?.[t] || []).map((r) => [rowKey(t, r), r]));
    const changes = new Map();
    for (const [k, r] of after) {
      if (!before.has(k)) changes.set(k, { status: 'added', row: r });
      else if (JSON.stringify(before.get(k)) !== JSON.stringify(r)) changes.set(k, { status: 'changed', row: r, old: before.get(k) });
    }
    for (const [k, r] of before) if (!after.has(k)) changes.set(k, { status: 'removed', row: r });
    out[t] = changes;
  }
  return out;
}

/* ---------- 题目与编辑器 ---------- */

async function init() {
  const data = await (await fetch('/api/problems')).json();
  state.problems = data.problems;
  cm = CodeMirror.fromTextArea($('editor'), {
    lineNumbers: true, mode: 'python', indentUnit: 4, tabSize: 4,
    extraKeys: { Tab: (c) => c.replaceSelection('    ') },
  });
  renderTabs();
  selectProblem(state.problems[0].id);
  wireControls();
}

function renderTabs() {
  $('problem-tabs').innerHTML = state.problems.map((p) =>
    `<button data-id="${p.id}" class="${state.meta?.id === p.id ? 'active' : ''}">${esc(p.title)}</button>`).join('');
  for (const btn of $('problem-tabs').querySelectorAll('button')) {
    btn.onclick = () => selectProblem(btn.dataset.id);
  }
}

function selectProblem(id) {
  stopPlayback();
  state.meta = state.problems.find((p) => p.id === id);
  state.result = null; state.timeline = []; state.idx = 0;
  renderTabs();
  const m = state.meta;
  $('p-title').textContent = m.title;
  $('p-subtitle').textContent = m.subtitle;
  $('p-statement').textContent = m.statement;
  $('p-initial').textContent = m.initial_description;
  $('p-hints').innerHTML = m.hints.map((h) => `<li>${esc(h)}</li>`).join('');
  $('scenario-select').innerHTML = m.scenarios.map((s) =>
    `<option value="${s.id}">${esc(s.name)}</option>`).join('');
  $('scenario-desc').textContent = m.scenarios[0].description;
  const src = m.source;
  $('p-source').innerHTML =
    `<p>原仓库（只读）：<br><span class="src-file">${esc(src.repo)}</span><br>提交 <span class="src-file">${esc(src.commit.slice(0, 12))}</span></p>` +
    '<ul>' + src.files.map((f) =>
      `<li><span class="src-file">${esc(f.path)}</span> · ${esc(f.symbol)}${f.lines ? ' · L' + esc(f.lines) : ''}</li>`).join('') + '</ul>' +
    '<p class="muted">相关测试：' + src.tests.map(esc).join('、') + '</p>' +
    '<p class="muted">教学简化：</p><ul>' + m.simplifications.map((s) => `<li class="muted">${esc(s)}</li>`).join('') + '</ul>';
  clearResultPanels();
  loadPresets();
}

function currentPresets() {
  return state.mode === 'python' ? state.meta.presets : state.meta.sql_presets;
}

function loadPresets() {
  const presets = currentPresets();
  $('preset-select').innerHTML = presets.map((p) =>
    `<option value="${p.id}">${esc(p.name)}</option>`).join('');
  const draftKey = state.meta.id + ':' + state.mode;
  const code = state.drafts[draftKey] ?? presets[0].code;
  cm.setValue(code);
  cm.setOption('mode', state.mode === 'python' ? 'python' : 'text/x-sql');
  $('sql-help').textContent = state.meta.sql_help;
  $('sql-help').classList.toggle('hidden', state.mode !== 'sql');
}

function setMode(mode) {
  if (state.mode === mode) return;
  state.drafts[state.meta.id + ':' + state.mode] = cm.getValue();
  state.mode = mode;
  $('mode-python').classList.toggle('active', mode === 'python');
  $('mode-sql').classList.toggle('active', mode === 'sql');
  loadPresets();
}

function clearResultPanels() {
  $('judge-panel').classList.add('hidden');
  $('error-panel').classList.add('hidden');
  $('responses').innerHTML = '<p class="muted">运行后这里显示每笔请求的 HTTP 响应体和页面实际显示的文字。</p>';
  $('timeline').innerHTML = '<p class="muted">运行后这里显示 run_id 串联的真实轨迹。</p>';
  $('timeline-count').textContent = '';
  $('db-state').innerHTML = '<p class="muted">运行后这里显示数据库表内容。</p>';
  $('revoked-box').classList.add('hidden');
  $('visibility-badge').textContent = '';
  $('visibility-badge').className = 'badge';
  $('state-label').textContent = '';
  $('run-id-label').textContent = '';
  $('rp-slider').max = 0; $('rp-slider').value = 0;
}

/* ---------- 运行 ---------- */

async function run() {
  stopPlayback();
  const btn = $('run-btn');
  btn.disabled = true;
  $('run-status').textContent = '运行中…（独立临时数据库）';
  const payload = {
    problem: state.meta.id, mode: state.mode, code: cm.getValue(),
    scenario: $('scenario-select').value,
  };
  state.drafts[state.meta.id + ':' + state.mode] = cm.getValue();
  try {
    const resp = await fetch('/api/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await resp.json();
    if (!resp.ok) throw new Error(result.detail || resp.statusText);
    state.result = result;
    buildTimeline();
    renderResponses();
    renderJudge();
    state.idx = 0;
    renderTimeline();
    renderCurrent();
    $('run-status').textContent = result.error ? '运行出错' :
      (result.judge?.passed ? '判题通过' : '判题未通过');
  } catch (err) {
    $('error-panel').textContent = '请求失败：' + err.message;
    $('error-panel').classList.remove('hidden');
    $('run-status').textContent = '请求失败';
  } finally {
    btn.disabled = false;
  }
}

function buildTimeline() {
  const items = [
    ...state.result.events.map((e) => ({ ...e, _isSnapshot: false })),
    ...state.result.snapshots.map((s) => ({ ...s, kind: 'snapshot', label: `快照 · ${s.label}`, _isSnapshot: true })),
  ];
  items.sort((a, b) => a.seq - b.seq);
  state.timeline = items;
  $('rp-slider').max = Math.max(0, items.length - 1);
  $('timeline-count').textContent =
    `${state.result.events.length} 事件 · ${state.result.snapshots.length} 快照 · run ${state.result.run_id}`;
  $('run-id-label').textContent = 'run_id: ' + state.result.run_id;
}

/* ---------- 响应与判题 ---------- */

function renderResponses() {
  const frontend = {};
  for (const e of state.result.events) if (e.kind === 'frontend') frontend[e.actor] = e.label;
  const cards = state.result.responses.map((r) => {
    const body = JSON.stringify(r.body, null, 1);
    return `<div class="response-card">
      <div class="head"><span class="actor-chip">${esc(r.actor)}</span>
        <span class="http-chip http-${r.status}">HTTP ${r.status}</span></div>
      <div class="frontend-line">页面显示：${esc(frontend[r.actor] || '')}</div>
      <pre>${esc(body)}</pre></div>`;
  });
  const exitNote = state.result.events.some((e) => e.kind === 'process_exit_observed')
    ? '<div class="response-card"><div class="head"><span class="actor-chip">crash</span><span class="http-chip http-500">无响应</span></div><div class="frontend-line">页面显示：结果未确认——子进程提交后退出，客户端没收到回复；用原请求键重试找回。</div></div>'
    : '';
  $('responses').innerHTML = cards.join('') + exitNote || '<p class="muted">本次运行没有响应。</p>';
}

function renderJudge() {
  const panel = $('judge-panel');
  if (state.result.error) {
    panel.classList.add('hidden');
    $('error-panel').textContent = state.result.error;
    $('error-panel').classList.remove('hidden');
    return;
  }
  $('error-panel').classList.add('hidden');
  const j = state.result.judge;
  if (!j) { panel.classList.add('hidden'); return; }
  panel.classList.remove('hidden');
  const sum = $('judge-summary');
  sum.textContent = j.passed ? '全部通过 ✓' : '未通过 ✗';
  sum.className = j.passed ? 'pass' : 'fail';
  $('judge-checks').innerHTML = j.checks.map((c) =>
    `<tr class="${c.passed ? 'check-pass' : 'check-fail'}">
      <td>${esc(c.name)}</td><td>${esc(c.expected)}</td><td>${esc(c.actual)}</td>
      <td>${c.passed ? '通过' : '失败'}</td></tr>`).join('');
  $('judge-notes').innerHTML = j.notes.map((n) => `<div class="note-card">${esc(n)}</div>`).join('');
}

/* ---------- 时间轴与回放 ---------- */

function entryClass(e) {
  if (e._isSnapshot) return 'k-snapshot';
  if (e.kind === 'sql') return e.write ? 'k-sql-write' : 'k-sql-read';
  return 'k-' + e.kind;
}

function entryDetail(e) {
  const parts = [];
  if (e.sql) parts.push(`<pre>${esc(e.sql)}${e.params && (Array.isArray(e.params) ? e.params.length : Object.keys(e.params).length) ? '\n参数: ' + esc(JSON.stringify(e.params)) : ''}</pre>`);
  if (e.rowcount !== undefined && e.kind === 'sql') parts.push(`<span class="muted">影响/读到行数：${e.rowcount}</span>`);
  if (e.internal_sql?.length) parts.push(`<details><summary>触发器等内部 SQL（真实观测，${e.internal_sql.length} 条）</summary><pre>${esc(e.internal_sql.join('\n'))}</pre></details>`);
  if (e.rows_read?.length) parts.push(`<details><summary>读到的行（${e.rows_read.length}）</summary><pre>${esc(JSON.stringify(e.rows_read, null, 1))}</pre></details>`);
  if (e.request) parts.push(`<details><summary>请求体</summary><pre>${esc(JSON.stringify(e.request, null, 1))}</pre></details>`);
  if (e.body) parts.push(`<details><summary>响应体</summary><pre>${esc(JSON.stringify(e.body, null, 1))}</pre></details>`);
  if (e.reason) parts.push(`<pre>${esc(e.reason)}</pre>`);
  if (e.error) parts.push(`<pre class="sql-error">报错：${esc(e.error)}</pre>`);
  if (e.traceback) parts.push(`<details><summary>错误详情</summary><pre>${esc(e.traceback)}</pre></details>`);
  return parts.join('');
}

function renderTimeline() {
  const wrap = $('timeline');
  wrap.innerHTML = '';
  state.timeline.forEach((e, i) => {
    const div = document.createElement('div');
    div.className = 'tl-entry ' + entryClass(e);
    const tags = [];
    if (e.kind === 'unobserved') tags.push('<span class="tag unobserved">未观测</span>');
    if (e.kind === 'illustrative') tags.push('<span class="tag illustrative">教学示意</span>');
    if (e.kind === 'sql' && e.write && e.visibility === 'in_txn') tags.push('<span class="tag in_txn">事务内</span>');
    const detail = entryDetail(e);
    div.innerHTML = `<div class="tl-head"><span class="tl-seq">${e.seq}</span>
      <span class="actor-chip">${esc(e.actor || '')}</span>
      <span class="tl-label">${esc(e.label)}</span>${tags.join('')}</div>
      ${detail ? '<details>' + detail + '</details>' : ''}`;
    div.onclick = (ev) => {
      if (ev.target.closest('details') || ev.target.closest('summary')) return;
      stopPlayback(); state.idx = i; renderCurrent();
    };
    wrap.appendChild(div);
  });
}

function snapshotAt(idx) {
  const entry = state.timeline[idx];
  if (!entry) return null;
  let snap = null, pos = -1;
  for (let i = 0; i <= idx; i++) {
    if (state.timeline[i]._isSnapshot) { snap = state.timeline[i]; pos = i; }
  }
  const next = state.timeline[idx + 1];
  if (next && next._isSnapshot && ['sql', 'txn_commit', 'txn_rollback'].includes(entry.kind)) {
    snap = next; pos = idx + 1;
  }
  if (!snap) return null;
  let prev = null;
  for (let i = pos - 1; i >= 0; i--) {
    if (state.timeline[i]._isSnapshot) { prev = state.timeline[i]; break; }
  }
  return { snap, prev };
}

function renderCurrent() {
  const entries = $('timeline').children;
  for (let i = 0; i < entries.length; i++) entries[i].classList.toggle('current', i === state.idx);
  entries[state.idx]?.scrollIntoView({ block: 'nearest' });
  $('rp-slider').value = state.idx;
  renderDbState();
}

function renderDbState() {
  if ($('view-diff').classList.contains('active')) { renderFullDiff(); return; }
  const info = snapshotAt(state.idx);
  const badge = $('visibility-badge');
  if (!info) {
    $('db-state').innerHTML = '<p class="muted">还没有数据库快照。</p>';
    badge.textContent = ''; badge.className = 'badge';
    return;
  }
  const { snap, prev } = info;
  const phaseText = {
    initial: '初始数据', in_txn: '事务内暂时可见 · 未提交', autocommit: '自动提交写入',
    committed: '已提交 · 持久化', rolled_back: '已回滚 · 临时写入被撤销',
    marker: '场景对照点', final: '最终状态 · 已持久化',
  }[snap.phase] || snap.phase;
  badge.textContent = phaseText;
  badge.className = 'badge ' + snap.phase;
  $('state-label').textContent = `${snap.label}（${snap.actor}）`;

  const entry = state.timeline[state.idx];
  const changes = diffTables(prev?.tables, snap.tables);
  const readKeys = new Map();
  if (entry && entry.kind === 'sql' && entry.rows_read?.length) {
    const t = tableOfSql(entry.sql || '');
    if (t) readKeys.set(t, new Set(entry.rows_read.map((r) => rowKey(t, r))));
  }

  const revoked = [];
  if (snap.phase === 'rolled_back' && prev) {
    for (const [t, ch] of Object.entries(changes)) {
      for (const { status, row } of ch.values()) {
        if (status === 'removed') revoked.push({ table: t, row });
      }
    }
  }
  const box = $('revoked-box');
  if (revoked.length) {
    box.classList.remove('hidden');
    box.innerHTML = '<h4>被撤销的临时写入（事务内曾可见，回滚后已不存在）</h4>' +
      revoked.map((r) => `<div><span class="actor-chip">${esc(r.table)}</span> <code>${esc(JSON.stringify(r.row))}</code></div>`).join('');
  } else box.classList.add('hidden');

  $('db-state').innerHTML = Object.entries(snap.tables).map(([t, rows]) => {
    const ch = changes[t] || new Map();
    const reads = readKeys.get(t);
    const body = rows.map((r) => {
      const k = rowKey(t, r);
      let cls = '';
      if (ch.has(k)) cls = 'row-' + ch.get(k).status;
      else if (reads?.has(k)) cls = 'row-read';
      return `<tr class="${cls}">${Object.values(r).map((v) => `<td>${esc(v)}</td>`).join('')}</tr>`;
    }).join('');
    const cols = rows.length ? Object.keys(rows[0]) : (prev?.tables[t]?.length ? Object.keys(prev.tables[t][0]) : []);
    return `<div class="db-table"><h4>${esc(t)}<span class="count">${rows.length} 行</span></h4>
      <div class="tbl-scroll"><table><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead>
      <tbody>${body || `<tr><td colspan="${cols.length || 1}" class="muted">（空表）</td></tr>`}</tbody></table></div></div>`;
  }).join('');
}

function renderFullDiff() {
  const snaps = state.result.snapshots;
  const initial = snaps.find((s) => s.phase === 'initial');
  const final = snaps[snaps.length - 1];
  $('visibility-badge').textContent = '运行前 → 运行后';
  $('visibility-badge').className = 'badge final';
  $('state-label').textContent = '只比较真实持久化的前后状态（事务内临时写入不计入）';
  $('revoked-box').classList.add('hidden');
  if (!initial || !final) { $('db-state').innerHTML = '<p class="muted">缺少前后快照。</p>'; return; }
  const changes = diffTables(initial.tables, final.tables);
  $('db-state').innerHTML = Object.keys(final.tables).map((t) => {
    const ch = changes[t] || new Map();
    const rows = final.tables[t];
    const removed = [...ch.values()].filter((c) => c.status === 'removed');
    const cols = rows.length ? Object.keys(rows[0])
      : (initial.tables[t]?.length ? Object.keys(initial.tables[t][0]) : []);
    const renderRow = (r, status) => {
      const badge = status ? `<span class="diff-badge diff-${status}">${{ added: '新增', changed: '改动', removed: '删除' }[status]}</span>` : '';
      const cls = status ? 'row-' + status : '';
      return `<tr class="${cls}">${Object.values(r).map((v, i) =>
        `<td>${esc(v)}${i === 0 ? badge : ''}</td>`).join('')}</tr>`;
    };
    const body = rows.map((r) => renderRow(r, ch.get(rowKey(t, r))?.status)).join('')
      + removed.map((c) => renderRow(c.row, 'removed')).join('');
    const changedCount = [...ch.values()].filter((c) => c.status !== 'removed').length + removed.length;
    return `<div class="db-table"><h4>${esc(t)}<span class="count">${rows.length} 行 · ${changedCount ? changedCount + ' 处变化' : '无变化'}</span></h4>
      <div class="tbl-scroll"><table><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead>
      <tbody>${body || `<tr><td colspan="${cols.length || 1}" class="muted">（空表）</td></tr>`}</tbody></table></div></div>`;
  }).join('');
}

/* ---------- 播放控制 ---------- */

function stopPlayback() {
  state.playing = false;
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
  $('rp-play').textContent = '▶ 播放';
}

function togglePlay() {
  if (!state.timeline.length) return;
  if (state.playing) { stopPlayback(); return; }
  if (state.idx >= state.timeline.length - 1) state.idx = 0;
  state.playing = true;
  $('rp-play').textContent = '⏸ 暂停';
  state.timer = setInterval(() => {
    if (state.idx >= state.timeline.length - 1) { stopPlayback(); return; }
    state.idx += 1; renderCurrent();
  }, 800);
}

function jumpTo(kind) {
  const i = state.timeline.findIndex((e) => e.kind === kind);
  if (i >= 0) { stopPlayback(); state.idx = i; renderCurrent(); }
}

function wireControls() {
  $('mode-python').onclick = () => setMode('python');
  $('mode-sql').onclick = () => setMode('sql');
  $('preset-select').onchange = () => {
    const p = currentPresets().find((x) => x.id === $('preset-select').value);
    if (p) cm.setValue(p.code);
  };
  $('scenario-select').onchange = () => {
    const s = state.meta.scenarios.find((x) => x.id === $('scenario-select').value);
    $('scenario-desc').textContent = s ? s.description : '';
  };
  $('run-btn').onclick = run;
  $('rp-play').onclick = togglePlay;
  $('rp-start').onclick = () => { stopPlayback(); state.idx = 0; renderCurrent(); };
  $('rp-prev').onclick = () => { stopPlayback(); state.idx = Math.max(0, state.idx - 1); renderCurrent(); };
  $('rp-next').onclick = () => { stopPlayback(); state.idx = Math.min(state.timeline.length - 1, state.idx + 1); renderCurrent(); };
  $('rp-commit').onclick = () => jumpTo('txn_commit');
  $('rp-rollback').onclick = () => jumpTo('txn_rollback');
  $('rp-slider').oninput = () => { stopPlayback(); state.idx = Number($('rp-slider').value); renderCurrent(); };
  $('view-replay').onclick = () => {
    $('view-replay').classList.add('active'); $('view-diff').classList.remove('active');
    if (state.result) renderDbState();
  };
  $('view-diff').onclick = () => {
    $('view-diff').classList.add('active'); $('view-replay').classList.remove('active');
    if (state.result) renderFullDiff();
  };
  $('reset-btn').onclick = async () => {
    if (!confirm('删除所有本地运行记录（临时数据库与轨迹）？此操作不可恢复。')) return;
    const r = await (await fetch('/api/reset', { method: 'POST' })).json();
    state.result = null; state.timeline = []; state.idx = 0;
    clearResultPanels();
    $('run-status').textContent = `已清理 ${r.removed_runs} 次运行记录`;
  };
}

init();
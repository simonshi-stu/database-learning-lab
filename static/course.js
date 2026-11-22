'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const node = (tag, txt, cls) => { const e = document.createElement(tag); if (txt != null) e.textContent = txt; if (cls) e.className = cls; return e; };
  const arr = v => Array.isArray(v) ? v : [];
  const txt = v => v == null ? '' : typeof v === 'string' ? v : JSON.stringify(v, null, 2);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const S = {
    lessons: [], problems: [], meta: null, cap: null, cm: null, lang: 'sql',
    result: null, cases: [], idx: 0, capTimeline: [], step: 0,
    timer: null, busy: false, seq: 0, saved: '', setting: false,
    mascotMoment: null, rollbackSeen: false, guess: {}, httpStatus: null, assistSeq: 0, cppSeq: 0,
  };
  const KEY = 'backend-course-v2:';
  const PHASE = {
    before: '运行前', initial: '运行前', read: '读取结果', pending: '待提交',
    in_txn: '待提交 · 事务内', committed: '已提交', rolled_back: '已回滚',
    final: '运行后 · 已持久化', marker: '场景对照点', autocommit: '自动提交',
    open: '打开连接', close: '连接已关闭', error: '写入被拒绝',
    input: '请求输入', validate: '后端判断', http: '业务响应（回放）', frontend: '前端反馈',
    illustrative: '示意 · 未实测', wait: '等待锁',
  };
  const PK = {
    inventory: ['sku', 'product_id'], products: ['id'], orders: ['id'],
    order_items: ['order_id', 'product_id'], stock_movements: ['id'],
    room_calendar: ['room_id', 'night'], auxiliary_inventory: ['night'],
    bookings: ['request_id'], booking_nights: ['request_id', 'night'],
    active_room_nights: ['room_id', 'night'], reservation_events: ['sequence'],
  };
  const ACTOR_LABEL = {
    ok: '成功对照', rej: '正常拒订', 'rej-retry': '拒订重试', roll: '写第一晚后回滚',
    crash: '提交后退出', 'crash-retry': '退出后重试找回', aux: '辅助库存探针',
    A: '请求 A', B: '请求 B', retry: '同键重试', 'A-retry': '同键重试',
  };
  const MASCOT_OPEN = {
    select: '嗨，我是 QDuck（占位图）。这关只补一个字段：在 sku 后面写上 qty。',
    where: '盯住 qty 刚好等于 2 的那行：「至少 2」是包含它的。',
    update: '把库存检查也放进 WHERE，才不会扣成负数。',
    rollback: '先写入，再由我拉闸：看「待提交」的纸条怎么被收回。',
    sales: '两笔请求抢最后一件：条件放进 UPDATE，同键重试别重复扣。',
    hotel: '两晚同一间房：拒订要留记录，回滚什么都不留。',
  };

  let cache = {};
  try { cache = JSON.parse(localStorage.getItem(KEY + 'practice') || '{}') || {}; } catch { }
  cache.drafts = cache.drafts || {}; cache.done = cache.done || {};
  const write = () => { try { localStorage.setItem(KEY + 'practice', JSON.stringify(cache)); return true; } catch { return false; } };

  function option(el, value, label) { const o = node('option', label); o.value = value; el.add(o); }
  function showNotice(message = '') { const el = $('notice'); el.textContent = message; el.hidden = !message; }
  async function json(path, data) {
    const r = await fetch(path, { cache: 'no-store', ...(data ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) } : {}) });
    S.httpStatus = r.status;
    let o; try { o = await r.json(); } catch { throw Error('没有收到课程服务的 JSON 响应（HTTP ' + r.status + '）。'); }
    if (!r.ok) throw Error(txt(o.detail) || 'HTTP ' + r.status);
    return o;
  }

  /* ---------- QDuck：只在开场/提示/通过/回滚露脸，一次一句 ---------- */
  function mascot(moment) {
    const lines = {
      hint: '提示打开了：读一遍，再动手改那几行。',
      pass: '通过！数据库的行和预期对上了，去下一关吧。',
      rollback: '看，临时纸条被收回了：回滚后的表和写入前一模一样。',
    };
    const line = moment === 'open' ? (MASCOT_OPEN[S.meta?.id] || '先跑一遍起始代码，看它怎么失败。') : lines[moment];
    if (!line) return;
    $('mascot-line').textContent = line;
    const el = $('mascot');
    el.hidden = false;
    S.mascotMoment = moment;
    el.style.animation = 'none'; void el.offsetWidth; el.style.animation = '';
  }
  function mascotHide() { $('mascot').hidden = true; S.mascotMoment = null; }

  /* ---------- 目录与选题 ---------- */
  function nav() {
    const nav = $('nav'); nav.replaceChildren();
    const navGroups = [];
    S.lessons.forEach((l, i) => {
      const group = l.chapter || (l.mode === 'capstone' ? '综合应用' : 'SQL 基础');
      let g = navGroups.find(x => x.name === group);
      if (!g) { g = { name: group, items: [] }; navGroups.push(g); }
      g.items.push([l, i]);
    });
    navGroups.forEach(g => {
      nav.append(node('p', g.name, 'rail-group'));
      g.items.forEach(([l, i]) => {
      const b = node('button', null, 'nav-item'); b.type = 'button';
      if (l.id === S.meta?.id) b.setAttribute('aria-current', 'step');
      const done = cache.done[l.id] === true;
      if (done) b.classList.add('is-done');
      b.append(node('span', done ? '✓' : String(i + 1).padStart(2, '0'), 'nav-num'), node('span', l.title));
      b.setAttribute('aria-label', '第 ' + (i + 1) + ' 关 ' + l.title + (done ? '，已通过' : ''));
      b.onclick = () => select(i, true);
      nav.append(b);
      });
    });
    const n = S.lessons.filter(l => cache.done[l.id] === true).length;
    $('course-progress').max = S.lessons.length || 1;
    $('course-progress').value = n;
    $('progress-copy').textContent = n + ' / ' + S.lessons.length + ' 关已通过';
  }

  function code() { return S.cm ? S.cm.getValue() : $('editor').value; }
  function draftKey() { return S.meta.id + ':' + S.lang; }
  function remember() {
    if (!S.meta || S.setting) return;
    if (S.mascotMoment === 'open' || S.mascotMoment === 'hint') mascotHide();
    cache.drafts[draftKey()] = code();
    const ok = write();
    $('draft-note').textContent = ok ? '草稿已保存' : '仅当前页面';
    if (S.result && S.saved !== code()) status('代码已改 · 上次结果', 'wait');
    else if (S.result) resultStatus();
  }
  function setCode(v) {
    S.setting = true;
    const val = txt(v);
    if (S.cm) { S.cm.setOption('mode', (S.lang === 'sql' || S.lang === 'schema') ? 'text/x-sql' : 'python'); S.cm.setValue(val); S.cm.clearHistory(); S.cm.scrollTo(0, 0); }
    else $('editor').value = val;
    S.setting = false;
  }
  function models() {
    if (S.cap) return arr(S.lang === 'python' ? S.cap.presets : S.cap.sql_presets).map(p => ({ id: p.id, label: p.name, code: p.code }));
    const l = S.meta;
    if (l.build_presets) return arr(l.build_presets[S.lang]).map(p => ({ id: p.id, label: p.name, code: p.code }));
    return [{ id: 'starter', label: '起始代码', code: l.starter }, ...(typeof l.solution === 'string' ? [{ id: 'solution', label: '参考解法', code: l.solution }] : [])];
  }
  function editorMode() {
    const list = models(), p = $('preset');
    p.replaceChildren(); list.forEach(d => option(p, d.id, d.label));
    const d = cache.drafts[draftKey()];
    setCode(d ?? list[0]?.code ?? '');
    $('draft-note').textContent = d == null ? '起始代码' : '已恢复草稿';
  }
  function status(label, tone = '') { const el = $('result-status'); el.textContent = label; el.dataset.tone = tone; }

  function exampleTable(name, rows, columns) {
    const wrap = node('div', null, 'example-table');
    wrap.append(node('div', '例子 · ' + name, 'small'));
    const box = node('div', null, 'tablebox');
    const t = node('table'); const head = t.createTHead().insertRow();
    const cols = arr(columns).length ? columns : ['sku', 'qty'];
    cols.forEach(c => { const th = node('th', c); th.scope = 'col'; head.append(th); });
    const body = t.createTBody();
    rows.forEach(r => { const tr = body.insertRow(); arr(r).forEach(v => tr.insertCell().textContent = v); });
    box.append(t); wrap.append(box);
    return wrap;
  }
  function renderPredict() {
    const p = S.meta?.predict;
    $('predict-wrap').hidden = !p;
    if (!p) return;
    $('predict-q').textContent = p.q;
    const opts = $('predict-opts'); opts.replaceChildren();
    arr(p.options).forEach((label, i) => {
      const b = node('button', label, 'pill');
      b.setAttribute('aria-pressed', String(S.guess[S.meta.id] === i));
      b.onclick = () => { S.guess[S.meta.id] = i; renderPredict(); };
      opts.append(b);
    });
  }
  function predictVerdict() {
    const el = $('predict-verdict');
    const p = S.meta?.predict;
    if (!p || !S.result) { el.hidden = true; return; }
    el.hidden = false;
    const g = S.guess[S.meta.id];
    if (g == null) { el.textContent = '你还没猜：回题面点一个选项，再对照下面的实际结果。'; return; }
    const ok = g === p.answer;
    el.textContent = (ok ? '猜对了 ✓ ' : '猜错了 × ') + '你的猜测：' + p.options[g] + ' · 概念答案：' + p.options[p.answer] + '（实际数据看画板）';
  }

  function showHome() {
    stop();
    $('home').hidden = false;
    $('workspace').hidden = true;
    document.querySelector('.board-head').style.display = 'none';
    const done = S.lessons.filter(l => cache.done[l.id] === true).length;
    const totalMin = S.lessons.reduce((a, l) => a + (l.minutes || 4), 0);
    $('home-progress').textContent = `已通过 ${done} / ${S.lessons.length} 关 · 全程预计 ${totalMin} 分钟 · 每关 3–5 分钟一个概念`;
    const firstUndone = S.lessons.findIndex(l => cache.done[l.id] !== true);
    const nextIdx = firstUndone < 0 ? 0 : firstUndone;
    $('home-start').onclick = () => select(0, true);
    $('home-continue').onclick = () => {
      const li = S.lessons.findIndex(l => l.id === cache.last);
      select(li >= 0 ? li : nextIdx, true);
    };
    $('home-next').onclick = () => select(nextIdx, true);
    $('home-next').textContent = '推荐下一关：' + (S.lessons[nextIdx]?.title || '');
    const map = $('home-map'); map.replaceChildren();
    const groups = [];
    S.lessons.forEach((l, i) => {
      const ch = l.chapter || '其他';
      let g = groups.find(x => x.ch === ch);
      if (!g) { g = { ch, items: [] }; groups.push(g); }
      g.items.push([l, i]);
    });
    groups.forEach(g => {
      map.append(node('h3', g.ch, 'home-ch'));
      g.items.forEach(([l, i]) => {
        const row = node('button', null, 'home-row' + (cache.done[l.id] === true ? ' is-done' : ''));
        row.append(node('span', cache.done[l.id] === true ? '✓' : String(i + 1).padStart(2, '0'), 'nav-num'),
                   node('span', l.title, 'home-title'),
                   node('span', (l.minutes || 4) + ' 分钟', 'small'));
        row.onclick = () => select(i, true);
        map.append(row);
      });
    });
    try { history.replaceState(null, '', '#home'); } catch {}
  }
  function select(i, navigating = false) {
    const l = S.lessons[i]; if (!l) return;
    S.seq++; S.busy = false; stop(); clearResult();
    cache.last = l.id; write();
    $('home').hidden = true;
    document.querySelector('.board-head').style.display = '';
    S.meta = l;
    S.cap = l.mode === 'capstone' ? S.problems.find(p => p.id === l.problem) : null;
    const cap = l.mode === 'capstone';
    S.lang = cap ? 'python' : (l.build_presets ? 'service' : 'sql');
    nav();
    $('workspace').hidden = false;
    $('prev').disabled = i === 0; $('next').disabled = i === S.lessons.length - 1;
    $('prev').onclick = () => select(i - 1, true);
    $('next').onclick = () => select(i + 1, true);
    $('chapter').textContent = 'LESSON ' + String(i + 1).padStart(2, '0') + ' / ' + String(S.lessons.length).padStart(2, '0');
    $('crumb').textContent = l.chapter || (cap ? '综合应用' : 'SQL 基础');
    $('engine-badge').textContent = cap ? 'SQLite · 原实验室运行器 · POST /api/run' : (l.engine || '').startsWith('pg_') ? '真实 PostgreSQL · POST /api/course/run' : (l.engine || '').startsWith('cpp_') ? '教学 C++ · 预编译回放 / 本地编译' : 'SQLite · POST /api/course/run';
    $('kind').textContent = cap ? '业务综合' : '基础练习';
    $('title').textContent = l.title;
    $('summary').textContent = txt(l.summary || S.cap?.subtitle);
    $('minutes').textContent = '预计 ' + (l.minutes || 4) + ' 分钟 · 一个概念 · 先猜再改再运行';
    $('goal').textContent = txt(l.goal);
    const ex = $('example'); ex.replaceChildren();
    if (cap) {
      const d = node('details'); d.append(node('summary', '完整题面与四个场景'));
      d.append(node('div', txt(S.cap?.statement), 'prose'));
      ex.append(d);
    } else if (l.example) {
      ex.append(exampleTable(l.example.name, l.example.rows, l.example.columns));
    } else {
      const fx = arr(l.fixtures);
      if (fx.length) ex.append(exampleTable(fx[0][0], fx[0][1]));
      else ex.append(node('p', '本关没有示例数据。', 'small'));
    }
    $('schema').textContent = txt(l.schema || S.cap?.initial_description) || '题目未提供字段说明。';
    $('hint').textContent = Array.isArray(l.hint) ? l.hint.join('\n') : txt(l.hint || arr(S.cap?.hints).join('\n'));
    $('hint-wrap').open = false;
    $('hint-wrap').hidden = !$('hint').textContent;
    $('principle').textContent = txt(l.principle || arr(S.cap?.simplifications).join('\n'));
    $('principle-wrap').hidden = !$('principle').textContent;
    $('principle-wrap').open = false;
    $('contract').textContent = txt(l.contract);
    $('contract-wrap').hidden = !$('contract').textContent;
    $('contract-wrap').open = false;
    renderPredict();
    const ch = $('choice'); ch.replaceChildren();
    arr(l.choices).forEach(c => option(ch, c.id, c.label));
    $('choice-wrap').hidden = !ch.options.length;
    const lang = $('lang'); lang.replaceChildren();
    if (cap) option(lang, 'python', 'Python');
    if (l.build_presets) Object.keys(l.build_presets).forEach(k => option(lang, k, { schema: 'schema.sql', service: 'service.py', tests: 'tests_extra.py' }[k] || k));
    else option(lang, 'sql', 'SQL');
    lang.value = S.lang; lang.disabled = !cap && !l.build_presets;
    const sc = $('scenario'); sc.replaceChildren();
    arr(cap ? S.cap?.scenarios : l.scenarios).forEach(a => option(sc, a.id, a.name));
    $('scenario-wrap').hidden = !sc.options.length;
    chooseScenario();
    editorMode();
    renderAssist();
    renderReadingOrder(l);
    dgReset();
    renderDiagram();
    $('run').disabled = cap && !S.cap;
    $('cpp-local').hidden = l.engine !== 'cpp_locks';
    showNotice(cap && !S.cap ? '未能读取原版综合题数据。可用右上角「原版实验室」继续；刷新课程页可重试。' : '');
    try { history.replaceState(null, '', '#' + encodeURIComponent(l.id)); } catch { }
    if (navigating) { $('lesson').focus({ preventScroll: true }); $('lesson').scrollIntoView({ block: 'start', behavior: 'auto' }); }
    mascot('open');
    requestAnimationFrame(() => S.cm?.refresh());
  }
  function chooseScenario() {
    const list = S.cap ? arr(S.cap.scenarios) : arr(S.meta?.scenarios);
    const a = list.find(s => s.id === $('scenario').value);
    $('scenario-note').textContent = a?.description || '';
  }

  /* ---------- 运行与结果 ---------- */
  function clearResult() {
    stop();
    S.result = null; S.cases = []; S.idx = 0; S.step = 0; S.capTimeline = []; S.rollbackSeen = false;
    $('result-body').hidden = true; $('db-body').hidden = true;
    $('results-empty').hidden = false; $('db-empty').hidden = false;
    $('results-empty').textContent = '点「运行测试」：先跑一遍起始代码看它怎么失败，再改几行跑通。';
    $('db-empty').textContent = '运行后切到这里：命中的行会亮起来，写入先标「待提交」，提交看旧值 → 新值，回滚看临时行被收回。';
    $('response-wrap').hidden = true;
    $('predict-verdict').hidden = true;
    $('http-line').hidden = true;
    $('case-extra').replaceChildren();
    status('未运行');
  }
  function resultStatus() {
    if (!S.result) return;
    const p = S.result.passed ?? S.result.judge?.passed;
    if (p == null) {
      const why = arr(S.result.cases)[0]?.extra?.not_measured || '跳过：依赖未配置';
      status(why.split('。')[0], 'wait');
      return;
    }
    const count = S.cases.filter(c => c.passed === true).length;
    status((p === true ? '通过' : '未通过') + (S.cases.length ? ' · ' + count + '/' + S.cases.length : ''), p === true ? 'ok' : 'bad');
  }
  function objRows(rows, columns) {
    return arr(rows).map(r => Array.isArray(r) ? Object.fromEntries(arr(columns).map((c, i) => [c, r[i]])) : r);
  }
  function foundationTimeline(kase) {
    return arr(kase.steps).map((st, i) => ({
      seq: i, kind: 'snapshot', actor: st.actor || '本连接', label: st.label, phase: st.phase,
      note: st.note, sql: st.sql, ms: st.ms,
      tables: st.tables || { [st.table || 'inventory']: objRows(st.rows, st.columns || ['sku', 'qty']) },
      columns: st.columns,
    }));
  }
  function capstoneTimeline(res) {
    const items = [
      ...arr(res.events).map(e => ({ ...e, _snap: false })),
      ...arr(res.snapshots).map(s => ({ ...s, kind: 'snapshot', label: '快照 · ' + s.label, _snap: true })),
    ];
    items.sort((a, b) => a.seq - b.seq);
    return items;
  }
  async function run() {
    if (!S.meta || S.busy || $('run').disabled) return;
    stop();
    const seq = ++S.seq, lid = S.meta.id, value = code(), cap = S.meta.mode === 'capstone';
    S.busy = true; clearResult(); mascotHide(); S.saved = value;
    $('run').disabled = true; status('运行中…', 'wait');
    $('results-empty').textContent = '正在本机 SQLite 执行，请稍候。';
    showNotice();
    try {
      const payload = cap ? { problem: S.meta.problem, mode: S.lang, scenario: $('scenario').value, code: value } : { lesson_id: S.meta.id, code: S.meta.build_presets ? '' : value, choice: $('choice').value, scenario: $('scenario').value };
      if (S.meta.build_presets) {
        payload.files = {};
        for (const k of Object.keys(S.meta.build_presets)) {
          payload.files[k] = k === S.lang ? value : (cache.drafts[S.meta.id + ':' + k] ?? arr(S.meta.build_presets[k])[0]?.code ?? '');
        }
      }
      const res = await json(cap ? '/api/run' : '/api/course/run', payload);
      if (seq !== S.seq || S.meta.id !== lid) return;
      S.result = res;
      predictVerdict();
      if (cap) {
        S.capTimeline = capstoneTimeline(res);
        S.cases = arr(res.judge?.checks).map(c => ({ ...c }));
        if (!S.cases.length && res.error) S.cases.push({ name: '执行错误', passed: false, error: txt(res.error) });
        if (!S.cases.length) S.cases.push({ name: '本次运行', passed: null });
      } else {
        S.cases = arr(res.cases).map(c => ({ ...c, timeline: foundationTimeline(c) }));
      }
      if ((res.passed ?? res.judge?.passed) === true) { cache.done[S.meta.id] = true; write(); nav(); mascot('pass'); }
      resultStatus();
      $('result-body').hidden = false; $('results-empty').hidden = true;
      $('run-label').textContent = res.run_id ? '本次运行 · ' + res.run_id : '本次运行';
      const hl = $('http-line');
      hl.hidden = false;
      hl.textContent = '本页请求 POST /api/course/run → HTTP ' + S.httpStatus + '（课程服务）。回放里的「业务响应」是业务侧模拟记录，与本次请求状态不是同一层。';
      renderResponses();
      if (!S.cases.length) { $('case-detail').replaceChildren(node('p', txt(res.error) || '本次返回没有测试明细。', 'small')); }
      else chooseCase(0);
      renderDiagram();
      if (S.saved !== code()) status('代码已改 · 上次结果', 'wait');
    } catch (e) {
      if (seq !== S.seq || S.meta.id !== lid) return;
      clearResult(); status('未完成', 'bad');
      $('results-empty').textContent = '本次未获得测试结果：' + e.message;
      showNotice('请检查课程服务是否已启动，再运行一次。');
    } finally { if (seq === S.seq && S.meta.id === lid) { S.busy = false; $('run').disabled = false; } }
  }
  function chooseCase(i) {
    stop(); S.idx = i; S.step = 0;
    const list = $('case-list'); list.replaceChildren();
    S.cases.forEach((c, j) => {
      const b = node('button', (c.passed === true ? '✓ ' : c.passed === false ? '× ' : '⊘ ') + (c.name || '用例 ' + (j + 1)), 'pill');
      b.setAttribute('aria-pressed', String(j === i));
      b.onclick = () => chooseCase(j);
      list.append(b);
    });
    const c = S.cases[i], d = $('case-detail'); d.replaceChildren();
    if (c.passed == null && c.extra?.not_measured) d.append(node('div', c.extra.not_measured, 'notice'));
    d.append(node('div', '输入：' + (S.meta.build_presets ? 'schema.sql + service.py + tests_extra.py（草稿）' : code()) + (S.meta.scenarios?.length || S.cap ? ' · 场景 ' + $('scenario').value : ''), 'small'));
    if (c.error) d.append(node('div', txt(c.error), 'error'));
    if (Object.hasOwn(c, 'expected') || Object.hasOwn(c, 'actual')) {
      const values = node('div', null, 'readout');
      for (const [key, title] of [['expected', '预期值'], ['actual', '实际值']]) {
        const cell = node('div');
        cell.append(node('strong', title), node('pre', Object.hasOwn(c, key) ? JSON.stringify(c[key], null, 2) ?? 'null' : '未返回'));
        values.append(cell);
      }
      d.append(values);
    }
    if (S.cap && arr(S.result?.judge?.notes).length) {
      arr(S.result.judge.notes).forEach(n => d.append(node('div', n, 'notice')));
    }
    renderExtra(c);
    renderDB();
  }
  function renderExtra(c) {
    const root = $('case-extra'); root.replaceChildren();
    const x = c.extra || {};
    const card = (title) => { const el = node('div', null, 'resp-card'); el.append(node('div', title, 'resp-head')); root.append(el); return el; };
    if (x.connection) {
      const el = card('当前连接（真实观测）');
      el.append(node('pre', JSON.stringify(x.connection, null, 1)));
      el.append(node('div', '表列表：' + (arr(x.tables_list).length ? arr(x.tables_list).join('、') : '（空）'), 'frontend-line'));
    }
    if (arr(x.layers).length) {
      const el = card('本次链路（四层）');
      const line = node('div', null, 'resp-head');
      x.layers.forEach((l, i) => { line.append(node('span', l, 'actor')); if (i < x.layers.length - 1) line.append(node('span', '→', 'small')); });
      el.append(line);
    }
    if (arr(x.plan_before).length || arr(x.plan_after).length) {
      root.append(node('h3', '计划树 · EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)'));
      const wrap = node('div', null, 'tracks');
      for (const [label, key] of [['建索引前', 'plan_before'], ['建索引后', 'plan_after']]) {
        const tr = node('div', null, 'track');
        tr.append(node('h4', label));
        const ol = node('ol');
        arr(x[key]).forEach(n => {
          const li = node('li');
          li.style.paddingLeft = (n.depth * 14) + 'px';
          li.append(node('span', `${n.type} · 估计 ${n.est} / 实际 ${n.act} · 缓冲 hit ${n.hit ?? 0} read ${n.read ?? 0}`, ''));
          ol.append(li);
        });
        tr.append(ol); wrap.append(tr);
      }
      root.append(wrap);
    }
    if (arr(x.connections).length) {
      const el = card('连接前后对照（同一文件）');
      x.connections.forEach(cn => {
        const line = node('div', null, 'resp-head');
        line.append(node('span', cn.label, 'actor'), node('span', cn.state, 'biz-chip'));
        el.append(line);
        if (cn.rows) el.append(node('pre', JSON.stringify(cn.rows)));
        if (cn.error) el.append(node('div', cn.error, 'error'));
      });
    }
    if (arr(x.schema).length) {
      root.append(node('h3', '字段结构 · PRAGMA table_info 真实返回'));
      root.append(tableCard('inventory', x.schema, null, null, false));
    }
    if (x.file) {
      const el = card('练习文件复核');
      el.append(node('pre', JSON.stringify(x.file, null, 1)));
    }
    if (x.timelines) {
      root.append(node('h3', '两条连接的时间轴（真实毫秒）'));
      const tracks = node('div', null, 'tracks');
      for (const [name, ops] of Object.entries(x.timelines)) {
        const tr = node('div', null, 'track');
        tr.append(node('h4', name));
        const ol = node('ol');
        arr(ops).forEach(o => {
          const li = node('li');
          li.append(node('span', '+' + o.ms + 'ms', 'msbadge'), node('span', o.op, o.tone || ''));
          ol.append(li);
        });
        tr.append(ol); tracks.append(tr);
      }
      root.append(tracks);
    }
    if (arr(x.graph).length) {
      const el = card('等待关系图');
      x.graph.forEach(g => el.append(node('div', g, 'graph-line')));
      if (x.not_measured) el.append(node('div', x.not_measured, 'error'));
    }
    if (arr(x.contrast).length) {
      root.append(node('h3', '同处对照'));
      x.contrast.forEach(ct => {
        const el = node('div', null, 'resp-card');
        const head = node('div', null, 'resp-head');
        head.append(node('span', ct.name, 'actor'), node('span', ct.result, 'biz-chip'));
        el.append(head, node('div', ct.note, 'small'));
        root.append(el);
      });
    }
  }
  function bizCode(body) {
    if (!body) return '未确认';
    if (body.status) return body.status;
    if (body.error?.code) return body.error.code;
    if (body.order_id) return 'PLACED';
    return '—';
  }
  function renderResponses() {
    const wrap = $('response-wrap');
    if (!S.cap || !S.result) { wrap.hidden = true; return; }
    wrap.hidden = false;
    const frontend = {};
    arr(S.result.events).forEach(e => { if (e.kind === 'frontend') frontend[e.actor] = e.label; });
    const list = $('response-list'); list.replaceChildren();
    arr(S.result.responses).forEach(r => {
      const card = node('div', null, 'resp-card');
      const head = node('div', null, 'resp-head');
      head.append(node('span', r.actor, 'actor'));
      head.append(node('span', 'HTTP ' + r.status, 'http-chip http-' + r.status));
      head.append(node('span', '业务 ' + bizCode(r.body), 'biz-chip biz-' + bizCode(r.body)));
      card.append(head);
      if (frontend[r.actor]) card.append(node('div', '页面显示：' + frontend[r.actor], 'frontend-line'));
      const det = node('details'); det.append(node('summary', '响应体 JSON'));
      det.append(node('pre', JSON.stringify(r.body, null, 1)));
      card.append(det);
      list.append(card);
    });
    if (arr(S.result.events).some(e => e.kind === 'process_exit_observed' || e.kind === 'process_exit')) {
      const card = node('div', null, 'resp-card');
      const head = node('div', null, 'resp-head');
      head.append(node('span', 'crash', 'actor'), node('span', '无 HTTP 响应', 'http-chip http-none'), node('span', '业务 未确认', 'biz-chip biz-ROLLED_BACK'));
      card.append(head, node('div', '页面显示：结果未确认——子进程提交后退出，客户端没收到回复；用原请求键重试找回。', 'frontend-line'));
      list.append(card);
    }
  }

  /* ---------- 回放画板 ---------- */
  function steps() { return S.cap ? S.capTimeline : arr(S.cases[S.idx]?.timeline); }
  function rowKey(table, row) {
    let pk = (PK[table] || []).filter(k => row && k in row);
    if (!pk.length) pk = Object.keys(row || {});
    return pk.map(k => String(row?.[k])).join('|');
  }
  function tableOfSql(sql) {
    const m = /insert\s+into\s+([a-z_]+)/i.exec(sql || '') || /update\s+([a-z_]+)/i.exec(sql || '') || /delete\s+from\s+([a-z_]+)/i.exec(sql || '') || /from\s+([a-z_]+)/i.exec(sql || '');
    return m ? m[1].toLowerCase() : null;
  }
  function stateAt(i) {
    const all = steps(); const entry = all[i];
    if (!entry) return null;
    let snap = null, pos = -1;
    for (let k = 0; k <= i; k++) if (all[k]._snap || all[k].kind === 'snapshot') { snap = all[k]; pos = k; }
    const next = all[i + 1];
    if (next && (next._snap || next.kind === 'snapshot') && ['sql', 'txn_commit', 'txn_rollback'].includes(entry.kind)) { snap = next; pos = i + 1; }
    if (!snap) return { entry, snap: null, prev: null };
    let prev = null;
    for (let k = pos - 1; k >= 0; k--) if (all[k]._snap || all[k].kind === 'snapshot') { prev = all[k]; break; }
    return { entry, snap, prev };
  }
  function diffTables(prev, cur) {
    const out = {};
    for (const t of Object.keys(cur || {})) {
      const before = new Map(arr(prev?.[t]).map(r => [rowKey(t, r), r]));
      const changes = new Map();
      for (const r of arr(cur[t])) {
        const k = rowKey(t, r);
        if (!before.has(k)) changes.set(k, { status: 'added', row: r });
        else if (JSON.stringify(before.get(k)) !== JSON.stringify(r)) changes.set(k, { status: 'changed', row: r, old: before.get(k) });
      }
      for (const [k, r] of before) if (!arr(cur[t]).some(x => rowKey(t, x) === k)) changes.set(k, { status: 'removed', row: r });
      out[t] = changes;
    }
    return out;
  }
  function cellHtml(name, row, change, showOldNew) {
    const cols = Object.keys(row);
    return cols.map(c => {
      const v = row[c] === null ? 'NULL' : String(row[c]);
      if (showOldNew && (change?.status === 'changed' || change?.status === 'restored') && JSON.stringify(change.old[c]) !== JSON.stringify(row[c])) {
        return `<td><span class="old">${esc(change.old[c] === null ? 'NULL' : change.old[c])}</span><span class="arrow">→</span><span class="new">${esc(v)}</span></td>`;
      }
      return `<td>${esc(v)}</td>`;
    }).join('');
  }
  function tableCard(name, rows, changes, readKeys, showOldNew, extraNote) {
    const card = node('div', null, 'db-table');
    const h = node('h4'); h.append(node('span', name), node('span', arr(rows).length + ' 行', 'count'));
    card.append(h);
    if (extraNote) card.append(node('div', extraNote, 'hit-note padless'));
    const box = node('div', null, 'tablebox'); box.tabIndex = 0;
    const t = node('table');
    const cols = arr(rows).length ? Object.keys(rows[0]) : [];
    const head = t.createTHead().insertRow();
    cols.forEach(c => { const th = node('th', c); th.scope = 'col'; head.append(th); });
    const body = t.createTBody();
    arr(rows).forEach(r => {
      const k = rowKey(name, r);
      const ch = changes?.get(k);
      const tr = body.insertRow();
      if (ch) tr.className = 'row-' + ch.status;
      else if (readKeys?.has(k)) tr.className = 'row-read';
      tr.innerHTML = cellHtml(name, r, ch, showOldNew);
    });
    if (!arr(rows).length) { const tr = body.insertRow(); const td = tr.insertCell(); td.colSpan = Math.max(1, cols.length); td.textContent = '（空表）'; td.className = 'small'; }
    box.append(t); card.append(box);
    return card;
  }
  function renderDB() {
    const all = steps();
    const info = stateAt(S.step);
    $('db-body').hidden = !info; $('db-empty').hidden = Boolean(info);
    if (!info) { $('db-empty').textContent = '此用例未返回可回放的数据库快照。'; return; }
    const { entry, snap, prev } = info;
    const n = all.length;
    $('step-count').textContent = (S.step + 1) + ' / ' + n;
    $('seek').max = n - 1; $('seek').value = S.step; $('seek').disabled = n < 2;
    $('back').disabled = S.step === 0; $('forward').disabled = S.step === n - 1; $('play').disabled = n < 2;
    const phase = snap?.phase || entry.phase;
    $('phase').textContent = PHASE[phase] || phase || '快照';
    $('phase').dataset.phase = phase || '';
    $('step-title').textContent = entry.label || snap?.label || '数据快照';
    $('step-actor').textContent = entry.actor || '';
    $('step-actor').hidden = !entry.actor;
    $('step-ms').textContent = entry.ms != null ? '+' + entry.ms + 'ms' : '';
    $('step-note').textContent = txt(entry.note || (snap && snap !== entry ? snap.label : ''));
    if (phase === 'rolled_back' && !S.rollbackSeen) { S.rollbackSeen = true; mascot('rollback'); }

    const jumps = $('actor-jumps'); jumps.replaceChildren();
    if (S.cap) {
      const seen = new Set();
      all.forEach((e, i) => {
        if (e.kind === 'input' && e.actor && !seen.has(e.actor) && e.actor !== 'holder') {
          seen.add(e.actor);
          const target = e.actor === 'crash' ? all.findIndex(x => x.kind === 'process_exit') : i;
          const b = node('button', ACTOR_LABEL[e.actor] || e.actor, 'pill');
          b.onclick = () => { stop(); S.step = Math.max(0, target); renderDB(); };
          jumps.append(b);
        }
      });
      jumps.hidden = !jumps.children.length;
    } else jumps.hidden = true;
    const ci = all.findIndex(e => e.kind === 'txn_commit' || e.phase === 'committed');
    const ri = all.findIndex(e => e.kind === 'txn_rollback' || e.phase === 'rolled_back');
    $('jump-commit').disabled = ci < 0; $('jump-rollback').disabled = ri < 0;
    $('jump-commit').onclick = () => { stop(); S.step = ci; renderDB(); };
    $('jump-rollback').onclick = () => { stop(); S.step = ri; renderDB(); };

    const view = $('table-view'); view.replaceChildren();
    const revoked = $('revoked-box'); revoked.replaceChildren(); revoked.hidden = true;
    if (!snap) { view.append(node('p', '这一步还没有数据库快照。', 'small')); renderDetail(entry); return; }
    const changes = diffTables(prev?.tables, snap.tables);
    const showOldNew = phase === 'committed' || phase === 'final';
    const readKeys = new Map();
    if (entry.kind === 'sql' && arr(entry.rows_read).length) {
      const t = tableOfSql(entry.sql);
      if (t) readKeys.set(t, new Set(entry.rows_read.map(r => rowKey(t, r))));
    }
    if (phase === 'rolled_back' && prev) {
      const lines = [];
      for (const [t, ch] of Object.entries(changes)) for (const { status, row } of ch.values()) {
        if (status === 'removed') lines.push([t, row]);
        if (status === 'changed') lines.push([t, ch.get(rowKey(t, row))?.old ?? row]);
      }
      if (lines.length) {
        revoked.hidden = false;
        revoked.append(node('h4', '被收回的临时写入（事务内曾可见，回滚后已不存在）'));
        lines.forEach(([t, row]) => revoked.append(node('div', t + '  ' + JSON.stringify(row))));
      }
      for (const ch of Object.values(changes)) for (const [k, c] of ch) if (c.status === 'changed') ch.set(k, { ...c, status: 'restored' });
    }
    if (phase === 'read' && prev?.tables?.inventory) {
      const resultRows = snap.tables[Object.keys(snap.tables)[0]] || [];
      const hit = new Set(resultRows.map(r => String(r[Object.keys(r)[0]])));
      const hitKeys = new Set(prev.tables.inventory.filter(r => hit.has(String(r.sku))).map(r => rowKey('inventory', r)));
      view.append(tableCard('inventory', prev.tables.inventory, null, hitKeys, false, '蓝行 = 本次查询命中的行'));
      for (const [name, rows] of Object.entries(snap.tables)) view.append(tableCard(name, rows, null, null, false));
    } else {
      for (const [name, rows] of Object.entries(snap.tables)) {
        view.append(tableCard(name, rows, changes[name], readKeys.get(name), showOldNew || phase === 'rolled_back'));
      }
    }
    renderDetail(entry);
  }
  function renderDetail(entry) {
    const d = $('step-detail'); d.replaceChildren();
    const has = [];
    if (entry.sql) {
      const pre = node('pre', entry.sql + (entry.params && (Array.isArray(entry.params) ? entry.params.length : Object.keys(entry.params).length) ? '\n参数: ' + JSON.stringify(entry.params) : ''));
      if (entry.error) pre.className = 'sql-error';
      d.append(pre); has.push(1);
      if (entry.rowcount !== undefined && entry.rowcount !== null) d.append(node('div', '影响 / 读到行数：' + entry.rowcount, 'muted-line'));
      if (entry.error) d.append(node('div', '报错：' + entry.error, 'muted-line'));
    }
    if (arr(entry.internal_sql).length) {
      const det = node('details'); det.append(node('summary', '触发器等内部 SQL（真实观测，' + entry.internal_sql.length + ' 条）'));
      det.append(node('pre', entry.internal_sql.join('\n'))); d.append(det); has.push(1);
    }
    if (arr(entry.rows_read).length) {
      const det = node('details'); det.append(node('summary', '读到的行（' + entry.rows_read.length + '）'));
      det.append(node('pre', JSON.stringify(entry.rows_read, null, 1))); d.append(det); has.push(1);
    }
    if (entry.request) {
      const det = node('details'); det.append(node('summary', '请求体'));
      det.append(node('pre', JSON.stringify(entry.request, null, 1))); d.append(det); has.push(1);
    }
    if (entry.body) {
      const det = node('details'); det.append(node('summary', '响应体（业务返回）'));
      det.append(node('pre', JSON.stringify(entry.body, null, 1))); d.append(det); has.push(1);
    }
    if (entry.kind === 'unobserved') d.append(node('div', '未观测：' + entry.label, 'muted-line'));
    if (entry.kind === 'illustrative') d.append(node('div', '教学示意：' + entry.label, 'muted-line'));
    if (!has.length && !['unobserved', 'illustrative'].includes(entry.kind)) d.append(node('div', '这一步没有 SQL（快照 / 对照点 / 事务边界）。', 'muted-line'));
    $('step-detail-wrap').open = false;
  }
  function stepAt(i) { const all = steps(); S.step = Math.max(0, Math.min(all.length - 1, i)); renderDB(); }
  function stop() { clearInterval(S.timer); S.timer = null; $('play').textContent = '播放'; $('play').setAttribute('aria-label', '播放回放'); }
  function playback() {
    if (S.timer) { stop(); return; }
    if (steps().length < 2) return;
    if (S.step === steps().length - 1) stepAt(0);
    $('play').textContent = '暂停'; $('play').setAttribute('aria-label', '暂停回放');
    S.timer = setInterval(() => { stepAt(S.step + 1); if (S.step >= steps().length - 1) stop(); }, 950);
  }
  function switchTab(which) {
    const replay = which === 'replay';
    const assist = which === 'assist';
    $('tab-replay').classList.toggle('active', replay);
    $('tab-results').classList.toggle('active', !replay && !assist);
    $('tab-assist').classList.toggle('active', assist);
    $('tab-replay').setAttribute('aria-selected', String(replay));
    $('tab-results').setAttribute('aria-selected', String(!replay && !assist));
    $('tab-assist').setAttribute('aria-selected', String(assist));
    $('pane-replay').hidden = !replay;
    $('pane-results').hidden = replay || assist;
    $('pane-assist').hidden = !assist;
    if (replay) renderDB();
    if (assist) renderAssist();
  }
  function failureSummary() {
    if (!S.result) return '';
    let failed = arr(S.result.cases).filter(c => c.passed === false);
    if (!failed.length && S.result.judge) failed = arr(S.result.judge.checks).filter(c => c.passed === false).map(c => ({ name: c.name, error: c.actual }));
    if (!failed.length && S.result.responses) failed = arr(S.result.responses).filter(r => r.status >= 400).map(r => ({ name: r.actor, error: (r.body.error || {}).code || r.status }));
    return failed.map(c => (c.name || '') + ': ' + String(c.error || '实际与预期不符').replace(/[A-Z]:\\[^\s]*/g, '<path>').slice(0, 120)).join(' | ').slice(0, 600);
  }
  async function askAssist(mode) {
    const lid = S.meta.id, seq = ++S.assistSeq;
    const body = $('assist-body');
    body.replaceChildren(node('p', '请求中…', 'small'));
    try {
      const res = await json('/api/course/assist', { lesson_id: lid, mode, code: code(), failure_summary: failureSummary(), scenario: $('scenario').value || '' });
      if (seq !== S.assistSeq || S.meta.id !== lid) return;
      S.assist = S.assist || {};
      S.assist[S.meta.id] = res;
      renderAssist();
    } catch (e) {
      if (seq !== S.assistSeq || S.meta.id !== lid) return;
      body.replaceChildren(node('div', '参考答案不可用：' + e.message, 'error'));
    }
  }
  function renderAssist() {
    const body = $('assist-body');
    body.replaceChildren();
    const res = S.assist && S.assist[S.meta?.id];
    if (!res) { body.append(node('p', '点上面三个按钮之一获取帮助；切换关卡不会串答案。', 'small')); return; }
    const head = node('div', null, 'resp-head');
    head.append(node('span', res.label, 'biz-chip'));
    head.append(node('span', res.verified ? '已验证' : '未验证', 'http-chip ' + (res.verified ? 'http-200' : 'http-409')));
    if (res.evidence) head.append(node('span', res.evidence, 'small'));
    body.append(head);
    if (res.hint) body.append(node('div', res.hint, 'prose'));
    if (res.explanation) body.append(node('div', res.explanation, 'prose'));
    if (res.solution) {
      const text = typeof res.solution === 'string' ? res.solution : JSON.stringify(res.solution, null, 2);
      body.append(node('pre', text, 'sql'));
      const load = node('button', '载入到编辑器（需确认）', 'pill');
      load.onclick = () => {
        if (!window.confirm('把参考解载入编辑器会替换当前草稿。继续？')) return;
        if (typeof res.solution === 'string') { setCode(res.solution); remember(); }
        else {
          for (const [k, v] of Object.entries(res.solution)) cache.drafts[S.meta.id + ':' + k] = v;
          write(); editorMode();
        }
      };
      body.append(load);
    }
  }

  /* ---------- 绑定与启动 ---------- */
  const e = $('editor');
  if (window.CodeMirror) {
    S.cm = CodeMirror.fromTextArea(e, { mode: 'text/x-sql', lineNumbers: true, lineWrapping: true, viewportMargin: 8, indentUnit: 2, tabSize: 2, extraKeys: { Tab: false, 'Shift-Tab': false, 'Ctrl-Enter': run, 'Cmd-Enter': run }, screenReaderLabel: '编辑本题代码' });
    S.cm.on('change', remember);
  } else e.addEventListener('input', remember);
  $('run').onclick = run;
  $('lang').onchange = () => { if (S.busy) { S.seq++; S.busy = false; $('run').disabled = false; } clearResult(); S.lang = $('lang').value; editorMode(); };
  $('scenario').onchange = () => { if (S.busy) { S.seq++; S.busy = false; $('run').disabled = false; } clearResult(); chooseScenario(); };
  $('apply').onclick = () => {
    const a = models().find(m => m.id === $('preset').value); if (!a) return;
    const v = code(), known = models().some(m => m.code === v);
    if (v !== a.code && !known && !window.confirm('载入预设会替换本题当前草稿。继续吗？')) return;
    setCode(a.code); remember();
    if (S.cm) S.cm.focus(); else e.focus();
  };
  $('hint-wrap').addEventListener('toggle', () => { if ($('hint-wrap').open) mascot('hint'); });
  $('back').onclick = () => { stop(); stepAt(S.step - 1); };
  $('rp-reset').onclick = () => { stop(); stepAt(0); };
  $('forward').onclick = () => { stop(); stepAt(S.step + 1); };
  $('play').onclick = playback;
  $('seek').oninput = () => { stop(); stepAt(Number($('seek').value)); };
  $('tab-results').onclick = () => switchTab('results');
  $('tab-replay').onclick = () => switchTab('replay');
  $('tab-assist').onclick = () => switchTab('assist');
  $('assist-hint').onclick = () => askAssist('hint');
  $('assist-explain').onclick = () => askAssist('explain');
  $('assist-solution').onclick = () => askAssist('solution');
  $('cpp-local').onclick = async () => {
    const lid = S.meta.id, seq = ++S.cppSeq;
    try {
      const res = await json('/api/course/cpp-replay?scenario=' + encodeURIComponent($('scenario').value || 'conflict') + '&dir=mine');
      if (seq !== S.cppSeq || S.meta.id !== lid) return;
      const steps = arr(res.events).map(e => ({ label: e.op, phase: e.ok === false ? 'error' : 'committed', tables: e.locks ? { locks: e.locks } : (e.frames ? { frames: e.frames } : (e.records ? { records: e.records } : (e.nodes ? { nodes: e.nodes } : (e.rows ? { rows: e.rows } : {})))), note: e.reason || e.note || '', actor: '教学引擎（本地编译）' }));
      S.cases = [{ name: '本地编译回放 · ' + res.dir, passed: null, steps, timeline: steps }];
      S.idx = 0; S.step = 0;
      $('case-list').replaceChildren();
      renderDB();
    } catch (e) { if (seq === S.cppSeq && S.meta.id === lid) showNotice(e.message); }
  };
  document.addEventListener('keydown', ev => { if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter' && !ev.defaultPrevented) { ev.preventDefault(); run(); } });
  window.addEventListener('hashchange', () => {
    let hash; try { hash = decodeURIComponent(location.hash.slice(1)); } catch { return; }
    if (!hash || hash === 'home') { showHome(); return; }
    const i = S.lessons.findIndex(l => l.id === hash);
    if (i >= 0 && S.lessons[i] !== S.meta) select(i, true);
  });
  $('home-link').onclick = (e) => { e.preventDefault(); showHome(); };
  bindDiagram();
  /* ===== 阅读顺序 + 因果链图（SQL→行变化→归宿） ===== */
  function renderReadingOrder(l) {
    const c = l.content || {};
    $('problem-title').textContent = c.problem || l.title;
    const g = $('glossary'); g.replaceChildren();
    (c.gloss || []).forEach(term => {
      const def = (l.glossary || {})[term];
      if (!def) return;
      const chip = node('span', null, 'gloss-chip');
      chip.append(node('b', term), node('span', def));
      g.append(chip);
    });
    const wc = $('why-cards'); wc.replaceChildren();
    const titles = ['发生了什么', '为什么会这样', '怎么亲手验证'];
    (c.why || []).forEach((t, i) => {
      const card = node('div', null, 'why-card w' + (i + 1));
      card.append(node('h4', (i + 1) + ' · ' + titles[i]), node('p', t));
      wc.append(card);
    });
    const ref = models().find(m => m.id === 'reference' || m.id === 'solution');
    $('full-sql').textContent = ref ? ref.code : (l.solution || l.starter || '');
    $('boundary').textContent = (c.checklist ? '用例：' + c.checklist.cases + '\n' : '') +
      arr(l.scenarios).map(s => s.name + '：' + s.description).join('\n');
    $('why-final').replaceChildren();
  }

  function chainNodes() {
    const all = steps(); const out = [];
    all.forEach((e, i) => {
      const info = stateAt(i);
      const snap = info && info.snap, prev = info && info.prev;
      const delta = [];
      if (snap) {
        const d = diffTables(prev && prev.tables, snap.tables);
        for (const [t, m] of Object.entries(d)) for (const [k, v] of m) {
          if (delta.length < 3) delta.push({ t, k, status: v.status, old: v.old, row: v.row });
        }
      }
      out.push({ i, actor: e.actor || '系统', label: e.label, sql: e.sql, rowcount: e.rowcount,
                 phase: (snap && snap.phase) || e.phase, delta });
    });
    return out;
  }
  function fateBadge(phase) {
    const map = { committed: ['✓', '已提交持久'], final: ['✓', '最终持久'], rolled_back: ['↺', '已回滚撤销'],
                  pending: ['…', '事务内暂时'], in_txn: ['…', '事务内暂时'], read: ['👁', '只读'],
                  error: ['✗', '被拒绝'], open: ['→', '打开'], close: ['→', '关闭'] };
    const m = map[phase] || ['·', phase || '快照'];
    return node('span', m[0] + ' ' + m[1], 'fate ' + (map[phase] ? phase : 'read'));
  }
  function dnodeEl(n) {
    const el = node('div', null, 'dnode' + (n.i === S.step ? ' current' : ''));
    const head = node('div', null, 'dhead');
    head.append(node('span', '#' + (n.i + 1), 'nav-num'), node('span', n.actor, 'actor'), fateBadge(n.phase));
    if (n.rowcount != null) head.append(node('span', '影响 ' + n.rowcount + ' 行', 'small'));
    el.append(head);
    if (n.sql) el.append(node('div', n.sql, 'dsql'));
    n.delta.forEach(d => {
      const line = node('div', null, 'ddelta');
      const oldv = d.old ? Object.values(d.old).join(',') : '';
      const newv = Object.values(d.row).join(',');
      line.append(node('span', d.t + '[' + d.k + '] ', ''));
      if (d.status === 'removed') line.append(node('span', newv, 'old'));
      else if (d.status === 'added') line.append(node('span', '+ ' + newv, 'new'));
      else { line.append(node('span', oldv, 'old')); line.append(node('span', ' → ', 'arrow')); line.append(node('span', newv, 'new')); }
      el.append(line);
    });
    el.onclick = () => { stop(); S.step = n.i; renderDB(); };
    return el;
  }
  function renderGrid(snap) {
    const wrap = node('div', null, 'cellgrid');
    const cal = (snap && snap.tables && snap.tables.room_calendar) || null;
    const act = (snap && snap.tables && snap.tables.active_room_nights) || [];
    if (!cal) {
      wrap.append(node('div', '示意：S101 在 12-08 被占；12-12 两间均封锁。交集为空→拒订。', 'small'));
      return wrap;
    }
    const nights = [...new Set(cal.map(r => r.night))].sort();
    const rooms = [...new Set(cal.map(r => r.room_id))].sort();
    const occ = new Set(act.map(r => r.room_id + '|' + r.night));
    const head = node('div', null, 'cg-row'); head.append(node('span', '夜\\房', 'cg-label'));
    rooms.forEach(r => head.append(node('span', r, 'cg-label')));
    wrap.append(head);
    const bn = (snap && snap.tables && snap.tables.booking_nights) || [];
    let stay = [...new Set(bn.filter(r => r.request_id === 'R-ok').map(r => r.night))];
    if (!stay.length) stay = ['2026-12-07', '2026-12-08'];
    const freeAll = rooms.filter(r => stay.every(n => {
      const c = cal.find(x => x.room_id === r && x.night === n);
      return c && !c.blocked && !occ.has(r + '|' + n);
    }));
    nights.forEach(n => {
      const row = node('div', null, 'cg-row'); row.append(node('span', n, 'cg-label'));
      rooms.forEach(r => {
        const c = cal.find(x => x.room_id === r && x.night === n);
        const cell = node('span', null, 'cg-cell');
        if (c && c.blocked) { cell.classList.add('blocked'); cell.textContent = '封锁'; }
        else if (occ.has(r + '|' + n)) { cell.classList.add('occupied'); cell.textContent = '已占'; }
        else { cell.classList.add('free'); cell.textContent = '可用'; }
        if (freeAll.includes(r)) cell.classList.add('intersect');
        row.append(cell);
      });
      wrap.append(row);
    });
    wrap.append(node('div', '入住晚 ' + stay.join(',') + ' 全程可用同一间（交集）：' + (freeAll.join(',') || '无→整单拒订'), 'small'));
    return wrap;
  }
  function renderCells(snap) {
    const wrap = node('div', null, 'cellgrid');
    const t = snap && snap.tables;
    const rows = (t && (t.records || t.frames || t.inventory)) || [];
    const head = node('div', null, 'cg-row');
    const cols = rows.length ? Object.keys(rows[0]) : ['(空)'];
    cols.forEach(c => head.append(node('span', c, 'cg-label')));
    wrap.append(head);
    rows.forEach(r => {
      const row = node('div', null, 'cg-row');
      cols.forEach(c => row.append(node('span', String(r[c]), 'cg-cell free')));
      wrap.append(row);
    });
    if (!rows.length) wrap.append(node('div', '（空）', 'small'));
    return wrap;
  }
  function renderTree(extra) {
    const ul = node('ul', null, 'ptree');
    const plans = (extra && (extra.plan_after || extra.plan_before)) || null;
    if (plans) {
      plans.forEach(n => {
        const li = node('li');
        li.append(node('span', (n.type || 'node') + ' · est ' + n.est + ' · act ' + n.act + ' · buf hit ' + (n.hit || 0) + ' read ' + (n.read || 0), 'pnode'));
        ul.append(li);
      });
    } else {
      ul.append(node('li', '示意：根 → 叶 → 叶链（运行后展开真实计划/节点）', 'small'));
    }
    return ul;
  }
  function renderDiagram() {
    const canvas = $('dg-canvas'); canvas.replaceChildren();
    const c = (S.meta && S.meta.content) || {};
    const aux = c.aux || 'flow';
    const hasRun = S.result && steps().length;
    const info = hasRun ? stateAt(S.step) : null;
    const snap = info && info.snap;
    if (!hasRun) canvas.append(node('span', '示意 · 未运行（运行后显示真实因果链）', 'schematic-tag'));
    if (aux === 'grid') canvas.append(renderGrid(snap));
    else if (aux === 'cells') canvas.append(renderCells(snap));
    else if (aux === 'tree') canvas.append(renderTree(hasRun ? (S.cases[S.idx] || {}).extra : null));
    const nodes = hasRun ? chainNodes() : [
      { i: 0, actor: '请求', label: '发起请求', phase: 'open', delta: [] },
      { i: 1, actor: '后端', label: '条件 UPDATE', sql: 'UPDATE ... WHERE 条件', phase: 'pending', delta: [] },
      { i: 2, actor: '后端', label: '看影响行数', phase: 'read', delta: [] },
      { i: 3, actor: '后端', label: '订单 / 流水', phase: 'pending', delta: [] },
      { i: 4, actor: '后端', label: '提交 或 回滚', phase: 'committed', delta: [] },
    ];
    if (aux === 'lanes' && hasRun) {
      const lanes = {};
      nodes.forEach(n => { (lanes[n.actor] = lanes[n.actor] || []).push(n); });
      const grid = node('div', null, 'chain lanes');
      Object.entries(lanes).forEach(([actor, ns]) => {
        const lane = node('div', null, 'lane');
        lane.append(node('div', actor, 'lane-head'));
        ns.forEach(n => lane.append(dnodeEl(n)));
        grid.append(lane);
      });
      canvas.append(grid);
    } else {
      const chain = node('div', null, 'chain');
      nodes.forEach(n => chain.append(dnodeEl(n)));
      canvas.append(chain);
    }
    const wf = $('why-final'); wf.replaceChildren();
    if (hasRun) {
      const committed = nodes.filter(n => (n.phase === 'committed' || n.phase === 'final') && n.rowcount > 0);
      const rolled = nodes.filter(n => n.phase === 'rolled_back');
      const li = stateAt(steps().length - 1);
      const fin = li && li.snap && li.snap.tables;
      let summary = '';
      if (fin) {
        if (fin.inventory) summary = '最终 inventory=' + JSON.stringify(fin.inventory.map(r => r.sku + ':' + r.qty));
        else if (fin.booking_nights) summary = '最终房晚 ' + fin.booking_nights.length + ' 行';
        else if (fin.t) summary = '最终 t=' + JSON.stringify(fin.t.map(r => r.id + ':' + r.qty));
      }
      const b = node('div', null, 'why-final');
      b.append(node('b', '为什么最后留下这个结果：'));
      b.append(document.createTextNode(
        (committed.length ? committed.length + ' 笔写入命中并提交（持久）；' : '无命中提交；') +
        (rolled.length ? rolled.length + ' 笔事务回滚，其临时写入全部撤销；' : '') +
        (summary || '') + '。点任一节点可核对它改的行与归宿。'));
      wf.append(b);
    } else {
      wf.append(node('div', '运行后，这里用本次真实数据解释「为什么最后留下这个结果」。', 'small'));
    }
    applyDgTransform();
  }
  const DG = { scale: 1, tx: 0, ty: 0 };
  function applyDgTransform() {
    $('dg-canvas').style.transform = 'translate(' + DG.tx + 'px,' + DG.ty + 'px) scale(' + DG.scale + ')';
  }
  function dgReset() { DG.scale = 1; DG.tx = 0; DG.ty = 0; applyDgTransform(); }
  function dgZoom(f) { DG.scale = Math.min(2.5, Math.max(0.5, DG.scale * f)); applyDgTransform(); }
  function bindDiagram() {
    $('dg-prev').onclick = () => { stop(); stepAt(S.step - 1); };
    $('dg-next').onclick = () => { stop(); stepAt(S.step + 1); };
    $('dg-zoom-in').onclick = () => dgZoom(1.2);
    $('dg-zoom-out').onclick = () => dgZoom(1 / 1.2);
    $('dg-reset').onclick = dgReset;
    const vp = $('dg-viewport'); vp.tabIndex = 0;
    let drag = null;
    vp.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, tx: DG.tx, ty: DG.ty }; vp.classList.add('dragging'); vp.setPointerCapture(e.pointerId); });
    vp.addEventListener('pointermove', e => { if (!drag) return; DG.tx = drag.tx + (e.clientX - drag.x); DG.ty = drag.ty + (e.clientY - drag.y); applyDgTransform(); });
    vp.addEventListener('pointerup', () => { drag = null; vp.classList.remove('dragging'); });
    vp.addEventListener('wheel', e => { e.preventDefault(); dgZoom(e.deltaY < 0 ? 1.1 : 1 / 1.1); }, { passive: false });
    vp.addEventListener('keydown', e => {
      if (e.key === 'ArrowRight') { stop(); stepAt(S.step + 1); e.preventDefault(); }
      else if (e.key === 'ArrowLeft') { stop(); stepAt(S.step - 1); e.preventDefault(); }
      else if (e.key === '+' || e.key === '=') dgZoom(1.2);
      else if (e.key === '-') dgZoom(1 / 1.2);
      else if (e.key === '0') dgReset();
    });
  }  (async () => {
    try {
      const [c, p] = await Promise.all([json('/api/course'), json('/api/problems').catch(() => ({ problems: [] }))]);
      S.lessons = arr(c.lessons); S.problems = arr(p.problems);
      if (!S.lessons.length) throw Error('课程目录为空。');
      let hash; try { hash = decodeURIComponent(location.hash.slice(1)); } catch { }
      if (!hash || hash === 'home') { showHome(); return; }
      const i = S.lessons.findIndex(l => l.id === hash);
      if (i < 0) showHome(); else select(i);
      if (new URLSearchParams(location.search).get('autorun')) {
        const pr = $('preset');
        const ref = [...pr.options].find(o => o.value === 'reference' || o.value === 'solution');
        if (ref) { pr.value = ref.value; pr.dispatchEvent(new Event('change')); $('apply').click(); }
        setTimeout(() => $('run').click(), 300);
      }
    } catch (err) {
      $('title').textContent = '课程未加载';
      $('progress-copy').textContent = '目录未加载';
      $('prev').disabled = $('next').disabled = true;
      showNotice(err.message + ' 请从本机课程服务的 /course 打开本页，刷新后重试。');
    }
  })();
})();
// 教学引擎（C++）：存储页 / 缓冲池 / B+ 树 / 查询执行 / 并发控制。
// 不等同于 PostgreSQL；每步打印一行 JSON 事件供画板回放。
// 用法：teach.exe <page|buffer|btree|exec|locks> <scenario> [value] [dir]
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <map>
#include <algorithm>
#include <fstream>
using namespace std;

static string jesc(const string& s) {
  string o;
  for (char c : s) {
    if (c == '"' || c == '\\') { o += '\\'; o += c; }
    else if (c == '\n') o += "\\n";
    else o += c;
  }
  return o;
}
static string qs(const string& k, const string& v) { return "\"" + k + "\":\"" + jesc(v) + "\""; }
static string qi(const string& k, long long v) { return "\"" + k + "\":" + to_string(v); }
static string qb(const string& k, bool v) { return "\"" + k + "\":" + (v ? "true" : "false"); }
static void emit(const string& body) { printf("{%s}\n", body.c_str()); fflush(stdout); }

// ---------------- 存储页 ----------------
struct Page {
  int id = 1, capacity = 60;
  vector<pair<int, string>> recs;
  int used() const { int u = 8; for (auto& r : recs) u += 4 + (int)r.second.size(); return u; }
  string json() const {
    string a = "[";
    for (size_t i = 0; i < recs.size(); i++) {
      if (i) a += ",";
      a += "{" + qi("key", recs[i].first) + "," + qs("val", recs[i].second) + "}";
    }
    return a + "]";
  }
};
static void page_state(const char* op, const Page& p, bool ok, const string& reason) {
  emit(qs("op", op) + "," + qi("page_id", p.id) + "," + qi("capacity", p.capacity) + "," +
       qi("used", p.used()) + "," + qi("free", p.capacity - p.used()) + "," +
       "\"records\":" + p.json() + "," + qb("ok", ok) + "," + qs("reason", reason));
}
static int cmd_page(const string& sc, const string& value, const string& dir) {
  Page p;
  if (sc == "exact") { p.capacity = 60; p.recs.push_back({1, string(20, 'a')}); }
  if (sc == "overflow") { p.capacity = 60; p.recs.push_back({1, string(20, 'a')}); p.recs.push_back({2, string(24, 'b')}); }
  page_state("open", p, true, "");
  if (sc == "empty") {
    auto it = find_if(p.recs.begin(), p.recs.end(), [](const pair<int, string>& r) { return r.first == 99; });
    page_state("read_missing", p, it != p.recs.end(), "key 99 not found");
  }
  bool fits = p.used() + 4 + (int)value.size() <= p.capacity;
  if (fits) p.recs.push_back({3, value});
  page_state("insert", p, fits, fits ? "" : "page_full: reject whole record");
  string path = dir + "/page.bin";
  ofstream out(path, ios::binary);
  out.write((char*)&p.id, 4); out.write((char*)&p.capacity, 4);
  int n = (int)p.recs.size(); out.write((char*)&n, 4);
  for (auto& r : p.recs) { out.write((char*)&r.first, 4); int l = (int)r.second.size(); out.write((char*)&l, 4); out.write(r.second.data(), l); }
  out.close();
  emit(qs("op", "close") + "," + qs("file", path));
  Page q; ifstream in(path, ios::binary);
  in.read((char*)&q.id, 4); in.read((char*)&q.capacity, 4);
  int m = 0; in.read((char*)&m, 4);
  for (int i = 0; i < m; i++) { int k, l; in.read((char*)&k, 4); in.read((char*)&l, 4); string v(l, '\0'); in.read(&v[0], l); q.recs.push_back({k, v}); }
  in.close();
  page_state("reopen_read", q, true, "records reread from disk after close");
  return 0;
}

// ---------------- 缓冲池 ----------------
struct Frame { int page = -1; bool dirty = false; int pin = 0; int clock = 0; int val = 0; };
struct Pool {
  int cap; vector<Frame> f; map<int, int> disk; int t = 0;
  Pool(int c) : cap(c), f(c) {}
  string json() const {
    string a = "[";
    for (size_t i = 0; i < f.size(); i++) {
      if (i) a += ",";
      a += "{" + qi("frame", (int)i) + "," + qi("page", f[i].page) + "," + qb("dirty", f[i].dirty) + "," + qi("pin", f[i].pin) + "," + qi("val", f[i].val) + "}";
    }
    return a + "]";
  }
  int find(int pid) { for (int i = 0; i < cap; i++) if (f[i].page == pid) return i; return -1; }
  void state(const char* op, int pid, const string& note) {
    emit(qs("op", op) + "," + qi("page", pid) + "," + qs("note", note) + ",\"frames\":" + json());
  }
  int fetch(int pid) {
    int i = find(pid);
    if (i >= 0) { f[i].clock = ++t; state("hit", pid, "frame reused"); return i; }
    i = -1;
    for (int k = 0; k < cap; k++) if (f[k].page == -1) { i = k; break; }
    if (i < 0) {
      int best = -1;
      for (int k = 0; k < cap; k++) if (f[k].pin == 0 && (best < 0 || f[k].clock < f[best].clock)) best = k;
      if (best < 0) { state("evict_blocked", pid, "all frames pinned"); return -1; }
      if (f[best].dirty) { disk[f[best].page] = f[best].val; state("writeback", f[best].page, "dirty frame flushed to disk"); }
      state("evict", f[best].page, "LRU unpinned frame chosen");
      i = best;
    }
    f[i].page = pid; f[i].dirty = false; f[i].pin = 0; f[i].clock = ++t;
    f[i].val = disk.count(pid) ? disk[pid] : pid * 10;
    state("miss", pid, "loaded from disk");
    return i;
  }
};
static int cmd_buffer(const string& sc, const string& flag) {
  Pool pool(sc == "shared" ? 2 : 1);
  pool.disk[1] = 10; pool.disk[2] = 20;
  if (sc == "cap1") { pool.fetch(1); pool.fetch(2); pool.fetch(1); }
  else if (sc == "pinned") { int i = pool.fetch(1); if (flag != "unpin") { pool.f[i].pin = 1; pool.state("pin", 1, "frame pinned"); } pool.fetch(2); if (flag != "unpin") { pool.f[i].pin = 0; pool.state("unpin", 1, "pin released"); pool.fetch(2); } }
  else { int i = pool.fetch(1); pool.f[i].val = 99; pool.f[i].dirty = true; pool.state("write", 1, "frame marked dirty"); pool.fetch(2); int j = pool.fetch(1); pool.state("reread", 1, "value after writeback = " + to_string(pool.f[j].val)); }
  return 0;
}

// ---------------- B+ 树 ----------------
struct BNode { int id; bool leaf; vector<int> keys; vector<int> ch; vector<string> vals; int next = -1; };
struct BTree {
  vector<BNode> nodes; int root = 0; int nextid = 1;
  BTree() { nodes.push_back(BNode{0, true, {}, {}, {}, -1}); }
  string json() const {
    string a = "[";
    for (size_t i = 0; i < nodes.size(); i++) {
      if (i) a += ",";
      const BNode& n = nodes[i];
      a += "{" + qi("id", n.id) + "," + qb("leaf", n.leaf) + ",\"keys\":[";
      for (size_t k = 0; k < n.keys.size(); k++) { if (k) a += ","; a += to_string(n.keys[k]); }
      a += "]," + qi("next", n.next) + "}";
    }
    return a + "]";
  }
  int leaf_for(int key, vector<int>& path) {
    int cur = root; path.clear();
    while (!nodes[cur].leaf) {
      path.push_back(cur);
      int i = 0; while (i < (int)nodes[cur].keys.size() && key >= nodes[cur].keys[i]) i++;
      cur = nodes[cur].ch[i];
    }
    path.push_back(cur);
    return cur;
  }
  void insert(int key, const string& val) {
    vector<int> path; int l = leaf_for(key, path);
    for (int k : nodes[l].keys) if (k == key) { emit(qs("op", "duplicate_reject") + "," + qi("key", key) + ",\"nodes\":" + json()); return; }
    nodes[l].keys.push_back(key); nodes[l].vals.push_back(val);
    auto& ks = nodes[l].keys; auto& vs = nodes[l].vals;
    for (size_t i = 0; i < ks.size(); i++) for (size_t j = i + 1; j < ks.size(); j++) if (ks[j] < ks[i]) { swap(ks[i], ks[j]); swap(vs[i], vs[j]); }
    string pathstr = "["; for (size_t i = 0; i < path.size(); i++) { if (i) pathstr += ","; pathstr += to_string(path[i]); } pathstr += "]";
    emit(qs("op", "insert") + "," + qi("key", key) + ",\"path\":" + pathstr + ",\"nodes\":" + json());
    if ((int)nodes[l].keys.size() > 3) split(l, path);
  }
  void split(int l, vector<int>& path) {
    BNode r{nextid++, true, {}, {}, {}, nodes[l].next};
    int mid = 2;
    r.keys.assign(nodes[l].keys.begin() + mid, nodes[l].keys.end());
    r.vals.assign(nodes[l].vals.begin() + mid, nodes[l].vals.end());
    nodes[l].keys.resize(mid); nodes[l].vals.resize(mid);
    nodes[l].next = r.id;
    nodes.push_back(r);
    int up = r.keys[0];
    if (path.size() >= 2) {
      int par = path[path.size() - 2];
      nodes[par].keys.push_back(up); nodes[par].ch.push_back(r.id);
      auto& pk = nodes[par].keys; auto& pc = nodes[par].ch;
      for (size_t i = 0; i < pk.size(); i++) for (size_t j = i + 1; j < pk.size(); j++) if (pk[j] < pk[i]) { swap(pk[i], pk[j]); swap(pc[i + 1], pc[j + 1]); }
      emit(qs("op", "split") + "," + qi("leaf", l) + "," + qi("new_leaf", r.id) + "," + qi("promoted", up) + ",\"nodes\":" + json());
    } else {
      BNode nr{nextid++, false, {up}, {l, r.id}, {}, -1};
      nodes.push_back(nr); root = nr.id;
      emit(qs("op", "split_root") + "," + qi("new_root", nr.id) + "," + qi("promoted", up) + ",\"nodes\":" + json());
    }
  }
  void search(int key) {
    vector<int> path; int l = leaf_for(key, path);
    bool found = find(nodes[l].keys.begin(), nodes[l].keys.end(), key) != nodes[l].keys.end();
    string pathstr = "["; for (size_t i = 0; i < path.size(); i++) { if (i) pathstr += ","; pathstr += to_string(path[i]); } pathstr += "]";
    emit(qs("op", "search") + "," + qi("key", key) + "," + qb("found", found) + ",\"path\":" + pathstr + ",\"nodes\":" + json());
  }
  void scan(int lo, int hi) {
    vector<int> path; int l = leaf_for(lo, path);
    string got = "["; bool first = true;
    for (int cur = l; cur != -1; cur = nodes[cur].next)
      for (size_t i = 0; i < nodes[cur].keys.size(); i++)
        if (nodes[cur].keys[i] >= lo && nodes[cur].keys[i] <= hi) { if (!first) got += ","; first = false; got += to_string(nodes[cur].keys[i]); }
    got += "]";
    emit(qs("op", "range_scan") + "," + qi("lo", lo) + "," + qi("hi", hi) + ",\"keys\":" + got + ",\"nodes\":" + json());
  }
};
static int cmd_btree(const string& sc, const string& value) {
  BTree t;
  int key = atoi(value.c_str());
  if (sc == "empty") { t.search(key); return 0; }
  if (sc == "missing") { t.insert(1, "a"); t.insert(3, "c"); t.search(key); return 0; }
  if (sc == "dup") { t.insert(1, "a"); t.insert(1, "b"); return 0; }
  for (int k = 1; k <= 5; k++) t.insert(k, string(1, 'a' + k - 1));
  if (sc == "scan") t.scan(2, 4);
  return 0;
}

// ---------------- 查询执行 ----------------
struct Row { int id; string kind; int qty; };
static string rowsjson(const vector<Row>& rs, bool project) {
  string a = "[";
  for (size_t i = 0; i < rs.size(); i++) {
    if (i) a += ",";
    a += project ? "{" + qi("id", rs[i].id) + "," + qi("qty", rs[i].qty) + "}"
                 : "{" + qi("id", rs[i].id) + "," + qs("kind", rs[i].kind) + "," + qi("qty", rs[i].qty) + "}";
  }
  return a + "]";
}
static int cmd_exec(const string& sc, const string& value) {
  int threshold = atoi(value.c_str());
  vector<Row> base = {{1, "rare", 2}, {2, "common", 5}, {3, "rare", 1}, {4, "common", 2}, {5, "rare", 3}, {6, "common", 0}};
  emit(qs("op", "Scan") + "," + qi("received", 0) + "," + qi("emitted", (int)base.size()) + ",\"rows\":" + rowsjson(base, false));
  vector<Row> filtered;
  for (auto& r : base) {
    bool keep = sc == "empty" ? r.kind == "none" : (sc == "boundary" ? (r.kind == "rare" && r.qty >= threshold) : r.kind == "rare");
    if (keep) filtered.push_back(r);
  }
  emit(qs("op", "Filter") + "," + qi("received", (int)base.size()) + "," + qi("emitted", (int)filtered.size()) + ",\"rows\":" + rowsjson(filtered, false));
  emit(qs("op", "Project") + "," + qi("received", (int)filtered.size()) + "," + qi("emitted", (int)filtered.size()) + ",\"rows\":" + rowsjson(filtered, true));
  return 0;
}

// ---------------- 并发控制（教学锁管理器） ----------------
#ifdef USE_STUDENT_LOCKS
#include "lockcore_student.h"
#else
static bool compatible(const string& held, const string& req) {
  if (held == "W") return false;
  return req == "R";
}
#endif
struct LockMgr {
  map<int, pair<int, string>> held;             // resource -> (txn, mode)
  map<int, vector<pair<int, string>>> waiters;  // resource -> queue
  string json() const {
    string a = "[";
    for (auto& h : held) {
      if (a.size() > 1) a += ",";
      a += "{" + qi("res", h.first) + "," + qi("txn", h.second.first) + "," + qs("mode", h.second.second) + "}";
    }
    return a + "]";
  }
  void request(int txn, int res, const string& mode) {
    auto it = held.find(res);
    if (it == held.end() || compatible(it->second.second, mode)) {
      if (it == held.end()) held[res] = {txn, mode};
      emit(qs("op", "grant") + "," + qi("txn", txn) + "," + qi("res", res) + "," + qs("mode", mode) + ",\"locks\":" + json());
    } else {
      waiters[res].push_back({txn, mode});
      emit(qs("op", "wait") + "," + qi("txn", txn) + "," + qi("res", res) + "," + qs("mode", mode) + "," + qi("holder", it->second.first) + ",\"locks\":" + json());
    }
  }
  void release(int txn) {
    for (auto it = held.begin(); it != held.end();) {
      if (it->second.first == txn) {
        int res = it->first;
        it = held.erase(it);
        emit(qs("op", "release") + "," + qi("txn", txn) + "," + qi("res", res) + ",\"locks\":" + json());
        if (!waiters[res].empty()) {
          auto w = waiters[res].front(); waiters[res].erase(waiters[res].begin());
          held[res] = {w.first, w.second};
          emit(qs("op", "grant") + "," + qi("txn", w.first) + "," + qi("res", res) + "," + qs("mode", w.second) + ",\"locks\":" + json());
        }
      } else ++it;
    }
  }
};
static int cmd_locks(const string& sc) {
  LockMgr m;
  if (sc == "conflict") {
    m.request(1, 1, "W"); m.request(2, 1, "W");
    emit(qs("op", "commit") + "," + qi("txn", 1)); m.release(1);
    emit(qs("op", "commit") + "," + qi("txn", 2)); m.release(2);
  } else if (sc == "rollback") {
    m.request(1, 1, "W"); m.request(2, 1, "W");
    emit(qs("op", "rollback") + "," + qi("txn", 1)); m.release(1);
    emit(qs("op", "commit") + "," + qi("txn", 2)); m.release(2);
  } else {
    m.request(1, 1, "R"); m.request(2, 1, "R");
    emit(qs("op", "commit") + "," + qi("txn", 1)); m.release(1);
    emit(qs("op", "commit") + "," + qi("txn", 2)); m.release(2);
  }
  emit(qs("op", "end") + ",\"locks\":" + m.json());
  return 0;
}

int main(int argc, char** argv) {
  if (argc < 3) { fprintf(stderr, "usage: teach <module> <scenario> [value] [dir]\n"); return 2; }
  string mod = argv[1], sc = argv[2];
  string value = argc > 3 ? argv[3] : "";
  string dir = argc > 4 ? argv[4] : ".";
  if (mod == "page") return cmd_page(sc, value, dir);
  if (mod == "buffer") return cmd_buffer(sc, value);
  if (mod == "btree") return cmd_btree(sc, value);
  if (mod == "exec") return cmd_exec(sc, value);
  if (mod == "locks") return cmd_locks(sc);
  fprintf(stderr, "unknown module\n");
  return 2;
}
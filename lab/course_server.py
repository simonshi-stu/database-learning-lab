"""Read-only/guided SQL foundations, using disposable in-memory SQLite."""
import json
import math
import re
import sqlite3
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .server import ROOT, app
from . import course_engines, course_pg, course_build, course_cpp, course_cloud, course_k8s  # noqa: F401  注册全部引擎


def lesson(id, title, summary, goal, starter="", hint="", solution="", problem=None,
           chapter="SQL 基础", engine=None, predict=None, principle="", example=None,
           scenarios=None, choices=None, txn_mode=None, contract='', build_presets=None, minutes=4, guide_kind=None, guide_stage=None, cloud_case=None, k8s_case=None):
    return dict(id=id, title=title, summary=summary, goal=goal,
                schema="inventory(sku TEXT, qty INTEGER)" if not problem else "",
                starter=starter, hint=hint, solution=solution,
                mode="capstone" if problem else "sql", problem=problem,
                chapter=chapter, engine=engine, predict=predict, principle=principle,
                example=example, scenarios=scenarios or [], choices=choices or [],
                txn_mode=txn_mode, contract=contract, build_presets=build_presets,
                minutes=minutes, guide_kind=guide_kind, guide_stage=guide_stage,
                cloud_case=cloud_case, k8s_case=k8s_case)


CH0 = "第 0 章 · 空库到第一条记录"
CH1 = "第 1 章 · 事务与订单"
CH2 = "第 2 章 · 四种容易混淆的情况"
CH_PG = "路线 A · 业务后端（PostgreSQL）"
CH_CPP = "路线 B · 数据库内核（C++ 教学）"
CH_END = "章末 · 综合应用"

LESSONS = [
    lesson("00a", "00A 打开空库", "文件、连接、表各是什么",
           "跑一次目录查询，确认空库：有文件、有连接、0 张表。",
           "SELECT name\nFROM sqlite_master\nWHERE type = '____';",
           "sqlite_master 是 SQLite 自带的目录表：库里每个对象占一行；空库就是 0 行。",
           "SELECT name FROM sqlite_master WHERE type = 'table';",
           chapter=CH0, engine="observe",
           predict=dict(q="先猜：空库里有几张表？", options=["0 张", "1 张", "3 张"], answer=0),
           principle="数据库文件是磁盘上的仓库；连接是你的程序打开仓库的通道；表是仓库里的货架。空仓库 = 文件在、通道通、货架 0 个。",
           example=dict(name="空库", columns=["对象"], rows=[])),
    lesson("00b", "00B 建第一张表", "字段、类型与主键",
           "补一个关键词，让 sku 成为 inventory 的主键。",
           "CREATE TABLE inventory (\n  sku TEXT ____ KEY,\n  qty INTEGER NOT NULL CHECK (typeof(qty)='integer' AND qty >= 0)\n);",
           "主键保证同一个 sku 只出现一次；第二个用例会故意插两次同 sku。",
           "CREATE TABLE inventory (\n  sku TEXT PRIMARY KEY,\n  qty INTEGER NOT NULL CHECK (typeof(qty)='integer' AND qty >= 0)\n);",
           chapter=CH0, engine="ddl",
           predict=dict(q="先猜：连插两次相同 sku 会怎样？", options=["成功并覆盖", "报错拒绝", "静默忽略"], answer=1),
           principle="类型决定能存什么；主键决定「哪行是哪行」。重复主键由数据库拒绝，不靠你的代码记得检查。",
           example=dict(name="目标表结构", columns=["字段", "类型", "角色"], rows=[["sku", "TEXT", "主键"], ["qty", "INTEGER", "非负"]])),
    lesson("00c", "00C 插一笔商品", "一条 INSERT 的前后对照",
           "补一个数字，把商品 A 库存 1 插进空表。",
           "INSERT INTO inventory(sku, qty)\nVALUES ('A', __);",
           "列顺序和 VALUES 顺序要一致；第二个用例把同一条语句再跑一次试主键。",
           "INSERT INTO inventory(sku, qty) VALUES ('A', 1);",
           chapter=CH0, engine="insert",
           predict=dict(q="先猜：跑完表里几行？", options=["0 行", "1 行", "2 行"], answer=1),
           principle="INSERT 是「新增一行」的唯一入口：写前空表、写后多一行，画板的前后对照就是真实变化。"),
    lesson("00d", "00D 关掉再连接", "用真实连接证明持久化",
           "补一个商品编号；平台用两个连接各查一次，再关掉复核。",
           "SELECT sku, qty\nFROM inventory\nWHERE sku = '__';",
           "那行是 00C 写进练习文件的；关连接只是挂电话，不是拆仓库。",
           "SELECT sku, qty FROM inventory WHERE sku = 'A';",
           chapter=CH0, engine="persist",
           predict=dict(q="先猜：两个连接都关掉后，那行还在吗？", options=["没了", "还在", "看运气"], answer=1),
           principle="持久化 = 数据住在文件里，不住在连接里。任何新连接打开同一个文件，都能读到同一行。"),
    lesson("select", "读懂一张表", "认识表、行与字段",
           "从 inventory 查出商品编号 sku 和库存 qty，按 sku 排序。",
           "SELECT sku\nFROM inventory\nORDER BY sku;",
           "SELECT 后可以写多个字段，用逗号分开。点击运行，看看查询有没有改变库存。",
           "SELECT sku, qty FROM inventory ORDER BY sku;",
           chapter=CH0,
           principle="SELECT 只读不写：查询前后库存一模一样，这是「读」和「写」的第一道分界。"),
    lesson("where", "筛出有货商品", "用 WHERE 判断一行是否符合条件",
           "查出库存至少 2 件的商品，返回 sku、qty，并按 sku 排序。",
           "SELECT sku, qty\nFROM inventory\nWHERE qty > 2\nORDER BY sku;",
           "至少 2 件包含刚好 2 件。用边界测试区分 > 和 >=。",
           "SELECT sku, qty FROM inventory WHERE qty >= 2 ORDER BY sku;",
           chapter=CH0,
           principle="WHERE 逐行判断：边界值（恰好 2）是最容易错的一行，回放里命中的行会亮蓝。"),
    lesson("fk", "外键拦住无效编号", "让数据库替你查引用",
           "补一个关键词，让 orders.sku 引用 products.sku。",
           "CREATE TABLE orders (\n  id INTEGER PRIMARY KEY,\n  sku TEXT NOT NULL ____ products(sku),\n  qty INTEGER NOT NULL CHECK (qty >= 1)\n);",
           "第二个用例故意插 sku=Z 的订单：Z 不在 products 里。",
           "CREATE TABLE orders (\n  id INTEGER PRIMARY KEY,\n  sku TEXT NOT NULL REFERENCES products(sku),\n  qty INTEGER NOT NULL CHECK (qty >= 1)\n);",
           chapter=CH0, engine="fk",
           predict=dict(q="先猜：插指向不存在商品的订单？", options=["能插进去", "被外键拒绝", "变成 NULL"], answer=1),
           principle="外键让数据库在写入时检查「被引用的行在不在」：无效编号当场被拒，而不是查询时才发现脏数据。"),
    lesson("join", "JOIN 商品与订单", "只保留匹配上的行",
           "补一个字段名，让 orders 通过 sku 连上 products。",
           "SELECT o.id, p.name, o.qty\nFROM orders o\nJOIN products p ON p.sku = o.____\nORDER BY o.id;",
           "3 号订单 sku=Z，而 Z 不在 products：看它会不会出现在结果里。",
           "SELECT o.id, p.name, o.qty FROM orders o JOIN products p ON p.sku = o.sku ORDER BY o.id;",
           chapter=CH0, engine="join",
           predict=dict(q="先猜：3 笔订单 1 笔孤儿，JOIN 出几行？", options=["3 行", "2 行", "1 行"], answer=1),
           principle="INNER JOIN 只保留两边都匹配的行；孤儿行被滤掉是连接的语义，不是 bug。"),
    lesson("stock", "先查库存", "存在但售完 ≠ 不存在",
           "查出商品 A 的当前库存：返回 sku、qty。",
           "SELECT sku, qty\nFROM inventory\nWHERE sku = '__';",
           "三个用例：库存恰好够、库存为 0、商品不在表里。后两种返回不一样。",
           "SELECT sku, qty FROM inventory WHERE sku = 'A';",
           chapter=CH1,
           predict=dict(q="先猜：库存为 0 时查询返回几行？", options=["1 行（qty=0）", "0 行"], answer=0),
           principle="查到 qty=0 的行 = 商品存在但售完；一行都查不到 = 商品不存在。两种失败语义不同。"),
    lesson("decrement", "条件扣减", "把判断放进 WHERE",
           "补全 WHERE 里的库存条件，让扣减永远不会扣成负数。",
           "UPDATE inventory\nSET qty = qty - 1\nWHERE sku = 'A' AND qty >= __;",
           "影响行数 0 = 条件不满足 = 库存不足；不要先查再无条件扣。",
           "UPDATE inventory SET qty = qty - 1 WHERE sku = 'A' AND qty >= 1;",
           chapter=CH1,
           predict=dict(q="先猜：A=0 时条件扣减影响几行？", options=["0 行", "1 行"], answer=0),
           principle="条件扣减把「够不够」交给数据库在同一语句里判断；影响行数是业务层唯一可信的信号。"),
    lesson("commit", "提交成功", "COMMIT 让写入持久化",
           "给 A 加 1 并由平台提交：看 A 从 2 变 3。",
           "UPDATE inventory\nSET qty = qty + __\nWHERE sku = 'A';",
           "回放里先看到「待提交」的 A=3，提交后才成为最终状态。",
           "UPDATE inventory SET qty = qty + 1 WHERE sku = 'A';",
           chapter=CH1, engine="txn", txn_mode="commit",
           predict=dict(q="先猜：提交后 A 是几？", options=["2", "3"], answer=1),
           principle="COMMIT 之前写入只在本事务内可见；COMMIT 之后任何连接都读到新值。"),
    lesson("rollback", "写入后出错并回滚", "临时 A=3 ≠ 最终 A=2",
           "给 A 加 1；平台在写入后模拟异常。分清事务内临时值与最终库存。",
           "UPDATE inventory\nSET qty = qty + __\nWHERE sku = 'A';",
           "结果区会同时给出「事务内 A=3」和「回滚后 A=2」；判题只用后者。",
           "UPDATE inventory SET qty = qty + 1 WHERE sku = 'A';",
           chapter=CH1, engine="txn", txn_mode="rollback",
           predict=dict(q="先猜：回滚后 A 的最终库存是几？", options=["2", "3"], answer=0),
           principle="回滚撤销的是事务内写入：你看到的 A=3 只是暂时可见；最终库存以独立连接复核为准。"),
    lesson("retry", "同键重试不重复扣减", "重试是找回结果",
           "给扣减语句加一个同键守卫，让 r1 的重试影响 0 行。",
           "UPDATE inventory\nSET qty = qty - 1\nWHERE sku = 'A' AND qty >= 1;",
           "守卫长这样：AND NOT EXISTS (SELECT 1 FROM orders WHERE request_key = 'r1')。",
           "UPDATE inventory SET qty = qty - 1 WHERE sku = 'A' AND qty >= 1 AND NOT EXISTS (SELECT 1 FROM orders WHERE request_key = 'r1');",
           chapter=CH1, engine="retry",
           predict=dict(q="先猜：加守卫后重试扣减影响几行？", options=["0 行", "1 行"], answer=0),
           principle="重试的目的是找回上一次的结果：先看同键订单在不在，再决定碰不碰库存。"),
    lesson("choice", "自己选提交或回滚", "事务边界在你手里",
           "写完加 1 后自己选择 COMMIT 或 ROLLBACK；最终库存须与选择一致。",
           "UPDATE inventory\nSET qty = qty + __\nWHERE sku = 'A';",
           "编辑器旁的「你的选择」决定平台执行 COMMIT 还是 ROLLBACK。",
           "UPDATE inventory SET qty = qty + 1 WHERE sku = 'A';",
           chapter=CH1, engine="choice",
           choices=[dict(id="commit", label="COMMIT · 提交"), dict(id="rollback", label="ROLLBACK · 回滚")],
           predict=dict(q="先猜：选 ROLLBACK 时最终 A 是几？", options=["2", "3"], answer=0),
           principle="同一条写入，COMMIT 与 ROLLBACK 给出两种最终状态：边界选择就是事务语义本身。"),
    lesson("order", "完整下单", "一条订单链走到底",
           "补全扣减条件，跑通下单链：输入 → 判断 → SQL → 数据库 → HTTP → 前端。",
           "UPDATE inventory\nSET qty = qty - 1\nWHERE sku = 'A' AND qty >= __;",
           "用「测试场景」切换成功 / 库存不足 / 商品不存在，对照响应与最终表。",
           "UPDATE inventory SET qty = qty - 1 WHERE sku = 'A' AND qty >= 1;",
           chapter=CH1, engine="order",
           scenarios=[dict(id="success", name="成功", description="A=1 买 1：200 PLACED，库存 0，订单 1 笔。"),
                      dict(id="insufficient", name="库存不足", description="A=0 买 1：409 INSUFFICIENT_STOCK，整单回滚。"),
                      dict(id="missing", name="商品不存在", description="没有 A：404 PRODUCT_NOT_FOUND，零写入。")],
           predict=dict(q="先猜：库存不足场景的 HTTP 状态？", options=["200", "409", "503"], answer=1),
           principle="下单是一条链：任何一步失败整单回滚；HTTP 状态与业务结果是两层，前端反馈由响应体生成。"),
    lesson("wait", "普通等待：B 等 A 释放", "等待不是失败",
           "补全扣减条件，观察 A 持锁时 B 等待、A 提交后 B 继续。",
           "UPDATE inventory\nSET qty = qty - 1\nWHERE sku = 'A' AND qty >= __;",
           "回放有两条连接的时间轴：B 的 BEGIN 会停在「等待写锁」直到 A COMMIT。",
           "UPDATE inventory SET qty = qty - 1 WHERE sku = 'A' AND qty >= 1;",
           chapter=CH2, engine="wait",
           predict=dict(q="先猜：A 持锁期间 B 在干什么？", options=["立刻报错", "等待锁", "也同时写入"], answer=1),
           principle="BEGIN IMMEDIATE 把写入串行化：B 不是失败，是在排队；A 释放后 B 用同一条语句继续。"),
    lesson("busy", "锁忙 vs 库存不足", "两种失败的恢复路径",
           "同一条扣减语句两种命运：持锁时 B 锁忙零写入、释放后同键重试；对照库存不足的业务拒绝。",
           "UPDATE inventory\nSET qty = qty - 1\nWHERE sku = 'A' AND qty >= __;",
           "结果区把两种失败放在同一处对照：零写入可重试 vs 整单回滚。",
           "UPDATE inventory SET qty = qty - 1 WHERE sku = 'A' AND qty >= 1;",
           chapter=CH2, engine="busy",
           predict=dict(q="先猜：锁忙失败后数据库里有什么？", options=["半笔数据", "什么都没有", "一笔订单"], answer=1),
           principle="锁忙 = 事务没开始、零写入、同键重试可恢复；库存不足 = 事务开始但条件不满足、整单回滚。恢复路径不同。"),
    lesson("pg-connect", "连接与建表（真 PG）", "浏览器→服务→客户端→服务端",
           "补一个关键词让 sku 成为主键：连接实验库、建表、插入、查询、断开重连。",
           "CREATE TABLE inventory (\n  sku TEXT ____ KEY,\n  qty INTEGER NOT NULL CHECK (qty >= 0)\n);",
           "画板标明四层：浏览器、Python 服务、psycopg 客户端、PostgreSQL 服务端。每次运行独立 schema。",
           "CREATE TABLE inventory (sku TEXT PRIMARY KEY, qty INTEGER NOT NULL CHECK (qty >= 0));",
           chapter=CH_PG, engine="pg_ddl",
           predict=dict(q="先猜：连插两次同 sku，PostgreSQL 报什么？", options=["23505 unique_violation", "静默覆盖", "40P01"], answer=0),
           principle="连接是通道、schema 是隔离边界、主键约束在服务端执行：重复键在写入时被服务端拒绝。"),
    lesson("pg-iso", "双连接隔离级别", "Read Committed vs Repeatable Read",
           "补 B 读的字段，切换隔离级别，预测 B 在 A 提交前后各看到什么。",
           "SELECT ____ FROM t WHERE id = 1;",
           "场景 rc = Read Committed，rr = Repeatable Read；画板并排两条事务边界与读取结果。",
           "SELECT qty FROM t WHERE id = 1;",
           chapter=CH_PG, engine="pg_iso",
           scenarios=[dict(id="rc", name="Read Committed", description="每条语句取新快照：A 提交后 B 见新值。"),
                      dict(id="rr", name="Repeatable Read", description="事务开始时的快照贯穿：A 提交后 B 仍见旧值。")],
           predict=dict(q="先猜：Repeatable Read 下 A 提交后 B 读到？", options=["旧值 2", "新值 3"], answer=0),
           principle="隔离级别决定快照何时建立：RC 每条语句取新快照；RR 的快照由事务内第一条获取快照的语句建立，单独执行 BEGIN 不会立即固定快照；未提交永远不可见，回滚无残留。"),
    lesson("pg-lock", "行锁与锁等待", "同行等待 / 异行并行 / 超时无残留",
           "填 B 要更新的行 id：同一行会等待，不同行可并行，超时失败不留部分写入。",
           "UPDATE t SET qty = qty - 1\nWHERE id = __ AND qty >= 1;",
           "场景 same 填 1、different 填 2、timeout 填 1。顺序用同步事件安排，判题看顺序与最终状态。",
           "UPDATE t SET qty = qty - 1 WHERE id = 1 AND qty >= 1;",
           chapter=CH_PG, engine="pg_lock",
           scenarios=[dict(id="same", name="同一行", description="B 等 A 释放行锁后继续。"),
                      dict(id="different", name="不同行", description="锁粒度是行：互不等待。"),
                      dict(id="timeout", name="锁超时", description="lock_timeout 300ms：55P03，整笔回滚。")],
           predict=dict(q="先猜：锁超时失败后 B 的事务里有什么？", options=["半笔更新", "什么都没有", "已提交"], answer=1),
           principle="行锁只锁被改的行；等待不是失败，超时才是；超时回滚保证没有部分写入。"),
    lesson("deadlock", "死锁：互相等待（真 PG）", "40P01 与统一锁顺序",
           "观察交叉加锁的等待图与真实 40P01，再按统一顺序重跑对照。",
           "-- 平台用两个独立连接演示：先各锁资源 1、2，再请求对方资源。\n-- 连接走环境配置 COURSE_PG_DSN，每次运行独立 schema。",
           "统一锁顺序 = 都先锁 1 再锁 2：等待图不成环。",
           "-- 统一锁顺序对照由平台重跑。",
           chapter=CH_PG, engine="pg_deadlock",
           predict=dict(q="先猜：死锁发生后哪笔事务被中止？", options=["两笔都提交", "一笔被数据库中止", "两笔永远等"], answer=1),
           principle="死锁 = 等待图成环；PostgreSQL 检测后中止一笔（40P01）并回滚它，另一笔继续；统一加锁顺序消除环。"),
    lesson("pg-plan", "索引与执行计划", "EXPLAIN (ANALYZE, BUFFERS)",
           "补一个过滤值，对照建索引前后的计划树与结果：估计/实际行数、缓冲区读写。",
           "SELECT id, kind FROM __TABLE__\nWHERE kind = '__VALUE__';",
           "场景 high=高选择性、low=低选择性、small=小表。只断言结果不变，不要求必须走索引。",
           "SELECT id, kind FROM __TABLE__ WHERE kind = 'rare';",
           chapter=CH_PG, engine="pg_plan",
           scenarios=[dict(id="high", name="高选择性", description="20000 行里 50 行 rare。"),
                      dict(id="low", name="低选择性", description="19950 行 common：索引未必划算。"),
                      dict(id="small", name="小表", description="20 行：顺序扫描往往就够。")],
           predict=dict(q="先猜：建索引后查询结果会变吗？", options=["不变", "变多", "变少"], answer=0),
           principle="索引是访问路径不是语义：计划可以变，结果必须不变；估计行数与实际行数的差距提示统计信息质量。"),
    lesson("exit", "进程退出：提交前 vs 提交后", "先预测再查实际表",
           "先猜哪次退出会留下记录，再查实际表；提交后用原请求键找回。",
           "INSERT INTO booking_nights\nVALUES ('r1', 'S101', '____', 0, 0);",
           "两个用例各用独立文件与子进程：提交前退出 / 提交后退出（os._exit(73)）。",
           "INSERT INTO booking_nights VALUES ('r1', 'S101', '2026-12-09', 0, 0);",
           chapter=CH2, engine="exit",
           predict=dict(q="先猜：哪次退出会留下记录？", options=["提交前退出", "提交后退出"], answer=1),
           principle="提交前退出 = 事务随连接断开被回滚，什么都不留；提交后退出 = 数据已在磁盘，同键找回原记录。进程、连接、锁是三样东西。"),
    lesson("pg-contrast", "等待 / 锁忙 / 死锁同例对照", "同一张两行表的三种命运",
           "跑一次观察：普通等待会成功、锁超时零写入、交叉加锁一笔被中止。",
           "-- 平台在同一张 t(id,qty) 两行表上演示三种锁命运。",
           "看两条连接的时间轴：等待不是失败，超时和死锁才是。",
           "-- 统一锁顺序（都先 1 后 2）可消除死锁。",
           chapter=CH_PG, engine="pg_contrast", minutes=5,
           predict=dict(q="先猜：B 等 A 释放锁后会怎样？", options=["继续成功", "永远失败", "自动跳过"], answer=0),
           principle="等待=排队；锁超时=放弃且零写入；死锁=成环后数据库中止一笔。三者恢复路径不同。"),
    lesson("pg-guide-sales-schema", "销售引导 1 · 建商品和订单表", "补主键：让 inventory.sku 成为主键。",
           "补主键：让 inventory.sku 成为主键。",
           course_build.GUIDE_STARTERS[('sales', 'schema')],
           "主键防重复商品；四张表齐了才能下单。",
           course_build.REF_SERVICE_SALES,
           chapter=CH_PG, engine="pg_guide", guide_kind='sales', guide_stage='schema', minutes=4,
           contract=course_build.CONTRACT_SALES if 'sales' == 'sales' else course_build.CONTRACT_HOTEL,
           build_presets={
               'schema': [dict(id='starter', name='本关草稿', code=course_build.GUIDE_STARTERS[('sales', 'schema')]), dict(id='reference', name='参考表结构', code=course_build.REF_SCHEMA_SALES)],
               'service': [dict(id='starter', name='本关草稿', code=course_build.REF_SERVICE_SALES), dict(id='reference', name='参考服务', code=course_build.REF_SERVICE_SALES)],
               'tests': [dict(id='starter', name='不加额外测试', code='')],
           },
           principle="主键防重复商品；四张表齐了才能下单。"),
    lesson("pg-guide-sales-single", "销售引导 2 · 单次下单", "补插入订单行：一次下单写订单+扣库存+流水。",
           "补插入订单行：一次下单写订单+扣库存+流水。",
           course_build.REF_SCHEMA_SALES,
           "一个事务里三张表一起写。",
           course_build.REF_SERVICE_SALES,
           chapter=CH_PG, engine="pg_guide", guide_kind='sales', guide_stage='single', minutes=4,
           contract=course_build.CONTRACT_SALES if 'sales' == 'sales' else course_build.CONTRACT_HOTEL,
           build_presets={
               'schema': [dict(id='starter', name='本关草稿', code=course_build.REF_SCHEMA_SALES), dict(id='reference', name='参考表结构', code=course_build.REF_SCHEMA_SALES)],
               'service': [dict(id='starter', name='本关草稿', code=course_build.GUIDE_STARTERS[('sales', 'single')]), dict(id='reference', name='参考服务', code=course_build.REF_SERVICE_SALES)],
               'tests': [dict(id='starter', name='不加额外测试', code='')],
           },
           principle="一个事务里三张表一起写。"),
    lesson("pg-guide-sales-insufficient", "销售引导 3 · 库存不足回滚", "补影响行数检查：不足就回滚并拒绝。",
           "补影响行数检查：不足就回滚并拒绝。",
           course_build.REF_SCHEMA_SALES,
           "rowcount==0 是业务拒绝信号。",
           course_build.REF_SERVICE_SALES,
           chapter=CH_PG, engine="pg_guide", guide_kind='sales', guide_stage='insufficient', minutes=4,
           contract=course_build.CONTRACT_SALES if 'sales' == 'sales' else course_build.CONTRACT_HOTEL,
           build_presets={
               'schema': [dict(id='starter', name='本关草稿', code=course_build.REF_SCHEMA_SALES), dict(id='reference', name='参考表结构', code=course_build.REF_SCHEMA_SALES)],
               'service': [dict(id='starter', name='本关草稿', code=course_build.GUIDE_STARTERS[('sales', 'insufficient')]), dict(id='reference', name='参考服务', code=course_build.REF_SERVICE_SALES)],
               'tests': [dict(id='starter', name='不加额外测试', code='')],
           },
           principle="rowcount==0 是业务拒绝信号。"),
    lesson("pg-guide-sales-retry", "销售引导 4 · 重复请求", "补同键检查：同内容回放、不同内容冲突。",
           "补同键检查：同内容回放、不同内容冲突。",
           course_build.REF_SCHEMA_SALES,
           "幂等键绑定内容摘要。",
           course_build.REF_SERVICE_SALES,
           chapter=CH_PG, engine="pg_guide", guide_kind='sales', guide_stage='retry', minutes=4,
           contract=course_build.CONTRACT_SALES if 'sales' == 'sales' else course_build.CONTRACT_HOTEL,
           build_presets={
               'schema': [dict(id='starter', name='本关草稿', code=course_build.REF_SCHEMA_SALES), dict(id='reference', name='参考表结构', code=course_build.REF_SCHEMA_SALES)],
               'service': [dict(id='starter', name='本关草稿', code=course_build.GUIDE_STARTERS[('sales', 'retry')]), dict(id='reference', name='参考服务', code=course_build.REF_SERVICE_SALES)],
               'tests': [dict(id='starter', name='不加额外测试', code='')],
           },
           principle="幂等键绑定内容摘要。"),
    lesson("pg-guide-sales-race", "销售引导 5 · 两笔抢最后一件", "补库存条件：让并发扣减不超卖。",
           "补库存条件：让并发扣减不超卖。",
           course_build.REF_SCHEMA_SALES,
           "条件放进 UPDATE 的 WHERE。",
           course_build.REF_SERVICE_SALES,
           chapter=CH_PG, engine="pg_guide", guide_kind='sales', guide_stage='race', minutes=4,
           contract=course_build.CONTRACT_SALES if 'sales' == 'sales' else course_build.CONTRACT_HOTEL,
           build_presets={
               'schema': [dict(id='starter', name='本关草稿', code=course_build.REF_SCHEMA_SALES), dict(id='reference', name='参考表结构', code=course_build.REF_SCHEMA_SALES)],
               'service': [dict(id='starter', name='本关草稿', code=course_build.GUIDE_STARTERS[('sales', 'race')]), dict(id='reference', name='参考服务', code=course_build.REF_SERVICE_SALES)],
               'tests': [dict(id='starter', name='不加额外测试', code='')],
           },
           principle="条件放进 UPDATE 的 WHERE。"),
    lesson("pg-guide-hotel-schema", "酒店引导 1 · 建房间和房晚表", "补主键：让 booking_nights(request_id,night) 成为主键。",
           "补主键：让 booking_nights(request_id,night) 成为主键。",
           course_build.GUIDE_STARTERS[('hotel', 'schema')],
           "主键防同一请求重复占同一晚。",
           course_build.REF_SERVICE_HOTEL,
           chapter=CH_PG, engine="pg_guide", guide_kind='hotel', guide_stage='schema', minutes=4,
           contract=course_build.CONTRACT_SALES if 'hotel' == 'sales' else course_build.CONTRACT_HOTEL,
           build_presets={
               'schema': [dict(id='starter', name='本关草稿', code=course_build.GUIDE_STARTERS[('hotel', 'schema')]), dict(id='reference', name='参考表结构', code=course_build.REF_SCHEMA_HOTEL)],
               'service': [dict(id='starter', name='本关草稿', code=course_build.REF_SERVICE_HOTEL), dict(id='reference', name='参考服务', code=course_build.REF_SERVICE_HOTEL)],
               'tests': [dict(id='starter', name='不加额外测试', code='')],
           },
           principle="主键防同一请求重复占同一晚。"),
    lesson("pg-guide-hotel-single", "酒店引导 2 · 单晚预订", "补房晚占用写入：订一晚写一行占用。",
           "补房晚占用写入：订一晚写一行占用。",
           course_build.REF_SCHEMA_HOTEL,
           "占用表是库存真相。",
           course_build.REF_SERVICE_HOTEL,
           chapter=CH_PG, engine="pg_guide", guide_kind='hotel', guide_stage='single', minutes=4,
           contract=course_build.CONTRACT_SALES if 'hotel' == 'sales' else course_build.CONTRACT_HOTEL,
           build_presets={
               'schema': [dict(id='starter', name='本关草稿', code=course_build.REF_SCHEMA_HOTEL), dict(id='reference', name='参考表结构', code=course_build.REF_SCHEMA_HOTEL)],
               'service': [dict(id='starter', name='本关草稿', code=course_build.GUIDE_STARTERS[('hotel', 'single')]), dict(id='reference', name='参考服务', code=course_build.REF_SERVICE_HOTEL)],
               'tests': [dict(id='starter', name='不加额外测试', code='')],
           },
           principle="占用表是库存真相。"),
    lesson("pg-guide-hotel-twonights", "酒店引导 3 · 两晚必须同一间房", "补交集：求每晚可用房间的交集。",
           "补交集：求每晚可用房间的交集。",
           course_build.REF_SCHEMA_HOTEL,
           "逐晚各挑会让客人换房。",
           course_build.REF_SERVICE_HOTEL,
           chapter=CH_PG, engine="pg_guide", guide_kind='hotel', guide_stage='twonights', minutes=4,
           contract=course_build.CONTRACT_SALES if 'hotel' == 'sales' else course_build.CONTRACT_HOTEL,
           build_presets={
               'schema': [dict(id='starter', name='本关草稿', code=course_build.REF_SCHEMA_HOTEL), dict(id='reference', name='参考表结构', code=course_build.REF_SCHEMA_HOTEL)],
               'service': [dict(id='starter', name='本关草稿', code=course_build.GUIDE_STARTERS[('hotel', 'twonights')]), dict(id='reference', name='参考服务', code=course_build.REF_SERVICE_HOTEL)],
               'tests': [dict(id='starter', name='不加额外测试', code='')],
           },
           principle="逐晚各挑会让客人换房。"),
    lesson("pg-guide-hotel-conflict", "酒店引导 4 · 第二晚冲突整单回滚", "补注入失败：第二晚写前抛异常让整单回滚。",
           "补注入失败：第二晚写前抛异常让整单回滚。",
           course_build.REF_SCHEMA_HOTEL,
           "事务边界覆盖整单。",
           course_build.REF_SERVICE_HOTEL,
           chapter=CH_PG, engine="pg_guide", guide_kind='hotel', guide_stage='conflict', minutes=4,
           contract=course_build.CONTRACT_SALES if 'hotel' == 'sales' else course_build.CONTRACT_HOTEL,
           build_presets={
               'schema': [dict(id='starter', name='本关草稿', code=course_build.REF_SCHEMA_HOTEL), dict(id='reference', name='参考表结构', code=course_build.REF_SCHEMA_HOTEL)],
               'service': [dict(id='starter', name='本关草稿', code=course_build.GUIDE_STARTERS[('hotel', 'conflict')]), dict(id='reference', name='参考服务', code=course_build.REF_SERVICE_HOTEL)],
               'tests': [dict(id='starter', name='不加额外测试', code='')],
           },
           principle="事务边界覆盖整单。"),
    lesson("pg-guide-hotel-retry", "酒店引导 5 · 同键重试", "补同键检查：回放原预订不重复占房。",
           "补同键检查：回放原预订不重复占房。",
           course_build.REF_SCHEMA_HOTEL,
           "重试是找回结果。",
           course_build.REF_SERVICE_HOTEL,
           chapter=CH_PG, engine="pg_guide", guide_kind='hotel', guide_stage='retry', minutes=4,
           contract=course_build.CONTRACT_SALES if 'hotel' == 'sales' else course_build.CONTRACT_HOTEL,
           build_presets={
               'schema': [dict(id='starter', name='本关草稿', code=course_build.REF_SCHEMA_HOTEL), dict(id='reference', name='参考表结构', code=course_build.REF_SCHEMA_HOTEL)],
               'service': [dict(id='starter', name='本关草稿', code=course_build.GUIDE_STARTERS[('hotel', 'retry')]), dict(id='reference', name='参考服务', code=course_build.REF_SERVICE_HOTEL)],
               'tests': [dict(id='starter', name='不加额外测试', code='')],
           },
           principle="重试是找回结果。"),
    lesson("pg-final-sales", "销售无提示综合", "独立补齐 schema.sql 与 service.py",
           "不给提示：按契约自己写表结构与服务，判题核对业务返回与新连接最终数据。",
           "", "", "",
           chapter=CH_PG, engine="pg_build_sales", minutes=15,
           contract=course_build.CONTRACT_SALES,
           build_presets={
               'schema': [dict(id='starter', name='空目录', code=course_build.STARTER_SCHEMA_SALES)],
               'service': [dict(id='starter', name='骨架（待实现）', code=course_build.STARTER_SERVICE_SALES)],
               'tests': [dict(id='starter', name='可写额外测试', code='')],
           }),
    lesson("pg-final-hotel", "酒店无提示综合", "独立补齐 schema.sql 与 service.py",
           "不给提示：按契约自己写两晚同房与整单撤回，判题核对业务返回与新连接最终数据。",
           "", "", "",
           chapter=CH_PG, engine="pg_build_hotel", minutes=15,
           contract=course_build.CONTRACT_HOTEL,
           build_presets={
               'schema': [dict(id='starter', name='空目录', code=course_build.STARTER_SCHEMA_HOTEL)],
               'service': [dict(id='starter', name='骨架（待实现）', code=course_build.STARTER_SERVICE_HOTEL)],
               'tests': [dict(id='starter', name='可写额外测试', code='')],
           }),
    lesson("pg-build-sales", "开写综合 · 销售服务", "从空目录写表结构/接口/事务/测试",
           "按契约自己写 schema.sql 与 service.py：两笔抢最后一件、同键重试、同键不同内容冲突。",
           "", "", "",
           chapter=CH_PG, engine="pg_build_sales", contract=course_build.CONTRACT_SALES,
           build_presets={
               'schema': [dict(id='starter', name='空目录', code=course_build.STARTER_SCHEMA_SALES),
                          dict(id='reference', name='参考表结构', code=course_build.REF_SCHEMA_SALES)],
               'service': [dict(id='starter', name='骨架（待实现）', code=course_build.STARTER_SERVICE_SALES),
                           dict(id='reference', name='参考服务', code=course_build.REF_SERVICE_SALES)],
               'tests': [dict(id='starter', name='不加额外测试', code=''),
                         dict(id='example', name='示例额外测试', code='def test_stock_never_negative(svc, conn):\n    c = conn()\n    assert c.execute("SELECT COUNT(*) FROM inventory WHERE qty < 0").fetchone()[0] == 0\n    c.close()\n')],
           },
           predict=dict(q="先猜：同键不同内容的重试会怎样？", options=["复用旧单", "冲突拒绝", "新建第二单"], answer=1),
           principle="幂等键绑定内容摘要：同键同内容回放、同键不同内容冲突；条件扣减 + 事务边界保证不超卖。"),
    lesson("pg-build-hotel", "开写综合 · 酒店预订服务", "从空目录写两晚同房与整单撤回",
           "按契约自己写 schema.sql 与 service.py：两晚同房、第二晚写失败整单撤回、同键回放。",
           "", "", "",
           chapter=CH_PG, engine="pg_build_hotel", contract=course_build.CONTRACT_HOTEL,
           build_presets={
               'schema': [dict(id='starter', name='空目录', code=course_build.STARTER_SCHEMA_HOTEL),
                          dict(id='reference', name='参考表结构', code=course_build.REF_SCHEMA_HOTEL)],
               'service': [dict(id='starter', name='骨架（待实现）', code=course_build.STARTER_SERVICE_HOTEL),
                           dict(id='reference', name='参考服务', code=course_build.REF_SERVICE_HOTEL)],
               'tests': [dict(id='starter', name='不加额外测试', code=''),
                         dict(id='example', name='示例额外测试', code='def test_no_checkout_occupation(svc, conn):\n    c = conn()\n    assert c.execute("SELECT COUNT(*) FROM booking_nights WHERE night = %s", ("2026-12-10",)).fetchone()[0] == 0\n    c.close()\n')],
           },
           predict=dict(q="先猜：第二晚写入后抛异常，第一晚的占用会留下吗？", options=["留下", "整单撤回"], answer=1),
           principle="事务边界覆盖整单：任何一晚失败都回滚全部占用；半开区间让离店日不占房。"),
    lesson("cpp-page", "存储页", "页头 / 记录槽 / 剩余空间",
           "补插入记录的值：观察空页、恰好装满、满页拒绝与关后重读。",
           'insert(3, "____");',
           "场景 empty/exact/overflow；exact 需要值长 24 字符才恰好装满（页容量 60）。",
           'insert(3, "abcdefghijklmnopqrstuvwx");',
           chapter=CH_CPP, engine="cpp_page",
           scenarios=[dict(id="empty", name="空页", description="插入成功、读不存在键失败、关后重读。"),
                      dict(id="exact", name="恰好装满", description="值长 24 字符：free=0。"),
                      dict(id="overflow", name="超容量", description="满页拒绝整条记录。")],
           predict=dict(q="先猜：满页再插一条会怎样？", options=["整条拒绝", "覆盖最旧记录", "自动分裂页"], answer=0),
           principle="页 = 页头 + 记录槽 + 剩余空间；写入前检查剩余空间，不够就整条拒绝。教学引擎，不等同于 PostgreSQL。"),
    lesson("cpp-buffer", "缓冲池", "命中 / 未命中 / 固定 / 淘汰 / 写回",
           "改一处 pin/unpin，观察小容量缓存的命中、淘汰阻塞与脏页写回。",
           'pin(1);',
           "把 pin(1) 改成 unpin(1) 再看 pinned 场景：固定页不可淘汰。",
           'pin(1);',
           chapter=CH_CPP, engine="cpp_buffer",
           scenarios=[dict(id="cap1", name="容量 1", description="命中/未命中/淘汰交替。"),
                      dict(id="pinned", name="固定页", description="被固定的页不可淘汰。"),
                      dict(id="writeback", name="脏页写回", description="写回后重新读到新值。")],
           predict=dict(q="先猜：被固定的页能被淘汰吗？", options=["能", "不能"], answer=1),
           principle="缓冲池用帧缓存页：pin 计数保护正在用的页；淘汰只选未固定页；脏页淘汰前必须写回。教学引擎。"),
    lesson("cpp-btree", "B+ 树", "点查 / 插入分裂 / 叶链范围扫描",
           "补点查的键：观察查找路径、节点分裂与跨叶扫描；重复键被拒绝。",
           'search(__);',
           "场景 empty/missing/dup/split/scan；missing 用 search(2) 看失败路径。",
           'search(2);',
           chapter=CH_CPP, engine="cpp_btree",
           scenarios=[dict(id="empty", name="空树", description="点查不存在。"),
                      dict(id="missing", name="不存在的键", description="查找路径到叶但失败。"),
                      dict(id="dup", name="重复键", description="策略：拒绝。"),
                      dict(id="split", name="节点分裂", description="叶满插入触发分裂与提升。"),
                      dict(id="scan", name="跨叶扫描", description="沿叶链扫 2..4。")],
           predict=dict(q="先猜：叶节点满了再插会怎样？", options=["节点分裂", "拒绝插入", "覆盖旧键"], answer=0),
           principle="B+ 树数据都在叶、叶间有链：点查走根到叶路径，范围扫描沿叶链；重复键策略显式拒绝。教学引擎。"),
    lesson("cpp-exec", "查询执行", "Scan → Filter → Project",
           "补过滤阈值：看每个算子接收与交出哪些行。",
           'Filter: kind = rare AND qty >= __',
           "场景 empty/boundary/project；boundary 的 >=2 必须包含恰好 2 的行。",
           'Filter: kind = rare AND qty >= 2',
           chapter=CH_CPP, engine="cpp_exec",
           scenarios=[dict(id="empty", name="空结果", description="Filter/Project 交 0 行。"),
                      dict(id="boundary", name="边界过滤", description="qty>=2 含恰好 2。"),
                      dict(id="project", name="输出列", description="Project 只留 id/qty。")],
           predict=dict(q="先猜：qty>=2 包含恰好 2 的行吗？", options=["包含", "不包含"], answer=0),
           principle="执行是算子流水线：每个算子接收上游行、交出下游行；索引减少的是 Scan 交出的行数。教学引擎。"),
    lesson("cpp-locks", "并发控制", "教学锁管理器：冲突 / 等待 / 释放",
           "读参考锁管理器草稿；本地编译你的版本并读取回放，观察等待与释放。",
           '''static bool compatible(const string& held, const string& req) {
  if (held == "W") return false;
  return req == "R";
}''',
           "Web 运行预编译教学二进制；你的草稿用 scripts\\cpp_run.cmd 本地编译后点「读取本地回放」。",
           '''static bool compatible(const string& held, const string& req) {
  if (held == "W") return false;
  return req == "R";
}''',
           chapter=CH_CPP, engine="cpp_locks",
           scenarios=[dict(id="conflict", name="写写冲突", description="等待 → 提交释放 → 获锁。"),
                      dict(id="rollback", name="回滚释放", description="回滚也释放锁。"),
                      dict(id="shared", name="读读兼容", description="共享锁不等待。")],
           predict=dict(q="先猜：T1 回滚后它持有的锁会怎样？", options=["释放、等待者获锁", "永久持有", "转给 T2"], answer=0),
           principle="锁管理器保证冲突写入串行、事务结束（提交或回滚）释放锁。这是教学模型，不等同于 PostgreSQL 的实现。"),
    lesson("sales", "销售：两笔订单抢一件", "练条件扣减、失败语义和同键重试",
           "先运行参考解，找出成功和失败请求。再用自己的题解保护库存，并让同键重试不会重复扣减。",
           problem="sales-last-unit", chapter=CH_END),
    lesson("hotel", "酒店：连续两晚同一间", "练业务拒绝、真正回滚和提交后找回",
           "对照拒订、写入后回滚、提交后退出三种情况。检查真实房晚和订单记录。",
           problem="hotel-two-nights", chapter=CH_END),
]

CH_CLOUD = "路线 C · 云后端实战"
CLOUD_LESSONS = [
    lesson("c1", "单机起点·发一次请求", "跑起来并查表确认", "发一次下单请求，查表确认库存与订单。", "SELECT sku, qty FROM products;", "先跑通再讲名词。", "SELECT sku, qty FROM products;", chapter=CH_CLOUD, engine="cloud", cloud_case="c1", minutes=4),
    lesson("c2", "单机起点·退款改了哪些数据", "对照前后快照", "取消/退款后看订单状态、库存、退款行与退款事件的变化。", "SELECT * FROM orders;", "对照退款前后快照。", "SELECT * FROM orders;", chapter=CH_CLOUD, engine="cloud", cloud_case="c2", minutes=4),
    lesson("d1", "分布式·拆成两个进程", "API 与 worker 分离", "把下单和后台处理拆成两个本地进程，看 worker 独立 pid 消费。", "", "单机多进程≠跨机器高可用。", "", chapter=CH_CLOUD, engine="cloud", cloud_case="d1", minutes=5),
    lesson("d2", "分布式·提交后丢回复", "同键重试只留一单", "数据库已提交但回复丢失，用相同请求键重试：只留一单只扣一次。", "SELECT request_key FROM orders WHERE request_key=%s", "改这一处幂等检查；删掉它会重复建单。", "SELECT request_key FROM orders WHERE request_key=%s", chapter=CH_CLOUD, engine="cloud", cloud_case="d2", minutes=5),
    lesson("d3", "分布式·Outbox 补发", "同事务落业务+事件", "业务行与待发送事件同事务落库；worker 停了再启动也能补发。", "", "Outbox 保证不丢事件。", "", chapter=CH_CLOUD, engine="cloud", cloud_case="d3", minutes=5),
    lesson("d4", "分布式·重复事件只写一次结果", "唯一键幂等消费", "消费者写结果后确认前退出；重启收重复事件，唯一键保证结果只写一次（消息可重复）。", "", "消息可重复，结果不可重复。", "", chapter=CH_CLOUD, engine="cloud", cloud_case="d4", minutes=5),
    lesson("d5", "分布式·下游不可用", "超时有限重试退避", "注入下游不可用，练超时、有限重试、退避与失败记录。", "", "失败要记录而非静默丢。", "", chapter=CH_CLOUD, engine="cloud", cloud_case="d5", minutes=5),
    lesson("l1", "数据湖·原始 vs 业务库", "各能回答什么", "保存原始 JSON/CSV，比较业务库与原始文件各能回答什么。", "", "本地目录=对象存储模拟。", "", chapter=CH_CLOUD, engine="cloud", cloud_case="l1", minutes=5),
    lesson("l2", "数据湖·校验坏记录", "坏行单独列原因", "校验字段，坏记录单独列出并说明原因。", "", "坏行不進报表。", "", chapter=CH_CLOUD, engine="cloud", cloud_case="l2", minutes=5),
    lesson("l3", "数据湖·Parquet 分区", "改一条 DuckDB SQL", "有效事件按业务日期写 Parquet；改一条 DuckDB SQL 看读了哪些分区/文件。", "SELECT biz_date, SUM(net_cents) FROM dedup GROUP BY biz_date", "改这一条 SQL。", "SELECT biz_date, SUM(net_cents) FROM dedup GROUP BY biz_date", chapter=CH_CLOUD, engine="cloud", cloud_case="l3", minutes=5),
    lesson("l4", "数据湖·重跑不重复计入", "去重+迟到+跨午夜", "加重复投递、次日迟到退款、跨午夜入住；重跑对应日期并对账。", "rn = 1", "改这一处去重条件；改成 rn>=1 会重复计入。", "rn = 1", chapter=CH_CLOUD, engine="cloud", cloud_case="l4", minutes=6),
    lesson("l5", "数据湖·改口径重建", "来源/行数/坏/重复", "改报表口径从原始重建，展示来源、输入输出行数、重复/坏行数量。", "SELECT biz_date, SUM(net_cents) AS net_cents, SUM(room_nights) AS room_nights FROM dedup GROUP BY biz_date", "净营收=下单-退款；酒店退房当天不计间夜。", "SELECT biz_date, SUM(net_cents) AS net_cents, SUM(room_nights) AS room_nights FROM dedup GROUP BY biz_date", chapter=CH_CLOUD, engine="cloud", cloud_case="l5", minutes=6),
    lesson("k1", "K8s·镜像容器端口", "本机容器跑起来", "在本机用容器运行，解释镜像、容器、端口。", "", "用本地 postgres 镜像演示容器概念。", "", chapter=CH_CLOUD, engine="k8s", k8s_case="k1", minutes=5),
    lesson("k2", "K8s·kind 集群与副本", "Pod/Deployment/Service", "用 kind 起隔离集群，扩到两个副本。", "replicas: 2", "改副本数。", "replicas: 2", chapter=CH_CLOUD, engine="k8s", k8s_case="k2", minutes=6),
    lesson("k3", "K8s·删Pod看补副本", "新UID+成功失败数", "删一个 Pod 观察新 UID，持续请求展示成功/失败数，不预设零中断。", "replicas: 2", "改副本/探针。", "replicas: 2", chapter=CH_CLOUD, engine="k8s", k8s_case="k3", minutes=6),
    lesson("k4", "K8s·readiness/liveness", "移出流量 vs 重启", "readiness 不通过被移出 endpoints；liveness 失败触发重启。", "readinessProbe: {}\nlivenessProbe: {}", "加/删探针。", "readinessProbe: {}\nlivenessProbe: {}", chapter=CH_CLOUD, engine="k8s", k8s_case="k4", minutes=6),
    lesson("k5", "K8s·坏配置回滚", "rollout卡住→回滚", "上线坏配置观察 rollout 卡住、手动回滚，再查订单仍在。", "image: wrong", "改回正确镜像即回滚。", "image: postgres:17-alpine", chapter=CH_CLOUD, engine="k8s", k8s_case="k5", minutes=6),
    lesson("f1", "结课挑战·同一订单贯穿", "超时→重试→重复→报表→换Pod", "从半成品补齐去重/清洗/探针；沿同一订单 ID 回放全链路。", "rn = 1", "补齐去重条件。", "rn = 1", chapter=CH_CLOUD, engine="cloud", cloud_case="f1", minutes=10),
]
LESSONS = LESSONS + CLOUD_LESSONS

BY_ID = {item["id"]: item for item in LESSONS}
FIXTURES = {
    "select": [("乱序录入", [("B", 0), ("A", 3)]), ("空表", []), ("只剩一行", [("C", 1)])],
    "where": [("边界恰好 2", [("A", 2), ("B", 1), ("C", 3)]), ("都不满足", [("A", 0), ("B", 1)])],
    "stock": [("库存恰好够", [("A", 1), ("B", 4)]), ("库存为 0", [("A", 0), ("B", 4)]),
              ("商品不存在", [("B", 2)])],
    "decrement": [("库存恰好够", [("A", 1), ("B", 4)]), ("库存为 0", [("A", 0), ("B", 4)]),
                  ("商品不存在", [("B", 2)])],
}


class CourseRun(BaseModel):
    lesson_id: str = Field(max_length=30)
    code: str = Field(max_length=1600)
    choice: str = Field(default="", max_length=20)
    scenario: str = Field(default="", max_length=40)
    files: dict = Field(default_factory=dict)


def _one(id, name, seed, code):
    reading = id in ("select", "where", "stock")
    original = sorted([list(row) for row in seed])
    desired = [row[:] for row in original]
    if id == "where":
        desired = [row for row in desired if row[1] >= 2]
    if id == "stock":
        desired = [row for row in desired if row[0] == "A"]
    if id in ("update", "decrement", "rollback"):
        for row in desired:
            if row[0] == "A" and (id == "rollback" or row[1] > 0):
                row[1] += 1 if id == "rollback" else -1
    expected_changes = sum(a != b for a, b in zip(original, desired)) if not reading else 0
    cols = ["sku", "qty"]
    stages = []
    error = None
    actual = None

    def step(label, phase, rows, sql=None, note=None, columns=None, table="inventory"):
        stages.append(dict(label=label, phase=phase, rows=rows, table=table,
                           columns=cols if columns is None else columns, sql=sql, note=note))

    def snap(db):
        return [list(row) for row in db.execute("SELECT sku, qty FROM inventory ORDER BY sku")]

    def auth(action, a, b, db_name, caller):
        if action == sqlite3.SQLITE_READ and a == "inventory" and b in ("", "sku", "qty"):
            return sqlite3.SQLITE_OK
        if reading and action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if not reading and action == sqlite3.SQLITE_UPDATE and a == "inventory" and b == "qty":
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    db = sqlite3.connect(":memory:", isolation_level=None, cached_statements=0)
    try:
        db.execute("CREATE TABLE inventory(sku TEXT PRIMARY KEY, qty INTEGER NOT NULL CHECK (typeof(qty)='integer' AND qty>=0))")
        db.executemany("INSERT INTO inventory VALUES(?,?)", seed)
        step("初始库存", "before", snap(db))
        if not re.match(r"^\s*" + ("SELECT" if reading else "UPDATE") + r"\b", code, re.I):
            return dict(name=name, passed=False, expected=desired, actual=None,
                        error="本关只接受一条 " + ("SELECT。" if reading else "UPDATE。"), steps=stages)
        if not reading:
            db.execute("BEGIN")
            step("开始事务", "before", snap(db), "BEGIN",
                 "接下来写入的行暂时只在当前事务中可见。")
        recorded = []
        db.set_trace_callback(recorded.append)
        db.set_authorizer(auth)
        budget = 0
        def timed():
            nonlocal budget
            budget += 1
            return budget > 90
        db.set_progress_handler(timed, 100)
        actual_cols = []
        changed = None
        try:
            cursor = db.execute(code)
            if reading:
                actual_cols = [col[0] for col in cursor.description]
                actual = [[(v.hex() if isinstance(v, bytes) else str(v) if isinstance(v, float) and not math.isfinite(v) else v) for v in row] for row in cursor.fetchall()]
            else:
                changed = cursor.rowcount
        except sqlite3.Error as exc:
            error = str(exc)
        finally:
            db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
            db.set_progress_handler(None, 0)
            db.set_trace_callback(None)
        sent_sql = recorded[-1] if recorded else None
        if reading:
            if actual is not None:
                step("查询返回结果", "read", actual, sent_sql, "SELECT 只读这张表。", actual_cols, "查询结果")
            step("读完后的库存", "committed", snap(db), note="库存没有被查询改写。")
            passed = error is None and actual_cols == cols and actual == desired and snap(db) == original
        else:
            actual = snap(db)
            if error is None:
                step("执行写入", "pending", actual, sent_sql, f"影响 {changed} 行；此刻还没有提交。")
            if id == "rollback" or error is not None:
                issued_rollback = db.in_transaction
                if issued_rollback:
                    db.execute("ROLLBACK")
                step("异常后回滚", "rolled_back", snap(db), "ROLLBACK" if issued_rollback else None,
                     "撤销事务内写入；回滚后的库存是下方这张表。")
                final_wanted = original
            else:
                db.commit()
                step("提交后库存", "committed", snap(db), "COMMIT", "本次库存变更已提交。")
                final_wanted = desired
            passed = error is None and actual == desired and changed == expected_changes and snap(db) == final_wanted
        return dict(name=name, passed=passed, expected=desired, actual=actual, error=error, steps=stages)
    finally:
        db.close()


@app.get("/course")
def course_page():
    return FileResponse(ROOT / "static" / "course.html")


@app.get("/api/course")
def syllabus():
    return {"lessons": [dict(item, fixtures=FIXTURES.get(item["id"], [])) for item in LESSONS]}


@app.post("/api/course/run")
def run_foundation(body: CourseRun):
    id = body.lesson_id
    if id not in BY_ID:
        raise HTTPException(404, "no such lesson")
    item = BY_ID[id]
    if item.get("engine"):
        from . import course_engines
        if not course_engines.supports(item["engine"]):
            raise HTTPException(501, "engine not implemented yet")
        if sum(len(v) for v in body.files.values()) > 40000:
            raise HTTPException(413, "files too large")
        return course_engines.run(item, body.code, body.choice, body.scenario, body.files)
    if id not in FIXTURES:
        raise HTTPException(404, "no such SQL lesson")
    cases = [_one(id, name, seed, body.code) for name, seed in FIXTURES[id]]
    return {"run_id": uuid4().hex[:12], "passed": all(case["passed"] for case in cases), "cases": cases}


class AssistRequest(BaseModel):
    lesson_id: str = Field(max_length=30)
    mode: str = Field(pattern='^(hint|explain|solution)$')
    code: str = Field(default='', max_length=20000)
    failure_summary: str = Field(default='', max_length=2000)
    scenario: str = Field(default='', max_length=40)


def _builtin_assist(item, mode, failure_summary, scenario=''):
    hints = item.get('hint') or ''
    if isinstance(hints, list):
        hints = '\n'.join(hints)
    principle = item.get('principle') or ''
    explanation = principle
    if failure_summary:
        explanation = '上次失败摘要：' + failure_summary + '\n\n' + principle
    solution = None
    if item.get('build_presets'):
        if any(p['id'] == 'reference' for v in item['build_presets'].values() for p in v):
            solution = {k: next((p['code'] for p in v if p['id'] == 'reference'), v[0]['code'])
                        for k, v in item['build_presets'].items()}
        elif item.get('engine') == 'pg_build_sales':
            solution = dict(schema=course_build.REF_SCHEMA_SALES, service=course_build.REF_SERVICE_SALES)
        elif item.get('engine') == 'pg_build_hotel':
            solution = dict(schema=course_build.REF_SCHEMA_HOTEL, service=course_build.REF_SERVICE_HOTEL)
    elif item.get('solution'):
        solution = item['solution']
    verified, evidence = False, ''
    report_path = ROOT / 'runs' / 'course-verify-report.json'
    if report_path.exists():
        try:
            for entry in json.loads(report_path.read_text(encoding='utf-8')):
                if entry['lesson'].split(':')[0] != item['id'] or not entry.get('correct_passed'):
                    continue
                if entry.get('scenario', '') != scenario:
                    continue
                if isinstance(solution, dict):
                    fg = entry.get('files_good') or {}
                    match = bool(fg) and all(fg.get(k) == v for k, v in solution.items())
                else:
                    match = entry.get('good_code') == solution
                if match:
                    verified, evidence = True, 'HTTP 回归 run ' + entry.get('run_id', '') + '（同答案同场景）'
                    break
        except (ValueError, KeyError):
            pass
    if item['id'] == 'cpp-locks':
        verified, evidence = True, '本地命令行编译实测 runs/cpp/locks-ref/'
    return dict(source='builtin', label='内置参考解（来源：课程内置，非 Agent）',
                hint=hints, explanation=explanation, solution=solution,
                verified=verified, evidence=evidence)


@app.post('/api/course/assist')
def assist(body: AssistRequest):
    import os
    import subprocess
    if body.lesson_id not in BY_ID:
        raise HTTPException(404, 'no such lesson')
    item = BY_ID[body.lesson_id]
    agent_cmd = os.environ.get('COURSE_AGENT_CMD')
    if agent_cmd:
        payload = json.dumps(dict(goal=item.get('goal', ''), mode=body.mode,
                                  code=body.code, failure_summary=body.failure_summary),
                             ensure_ascii=False)
        try:
            proc = subprocess.run(agent_cmd, input=payload.encode('utf-8'), shell=True,
                                  capture_output=True, timeout=20)
            data = json.loads(proc.stdout.decode('utf-8', 'replace'))
            return dict(source='agent', label='教学 Agent（已配置）',
                        hint=data.get('hint', ''), explanation=data.get('explanation', ''),
                        solution=data.get('solution'), verified=False,
                        evidence='Agent 输出未经测试验证', agent=True)
        except Exception:
            out = _builtin_assist(item, body.mode, body.failure_summary, body.scenario)
            out['source'] = 'builtin-fallback'
            out['label'] = '内置参考解（Agent 超时/不可用，已回退）'
            return out
    return _builtin_assist(item, body.mode, body.failure_summary, body.scenario)


@app.get('/api/course/cpp-replay')
def cpp_replay(scenario: str = 'conflict', dir: str = 'mine'):
    import re as _re
    if scenario not in ('conflict', 'rollback', 'shared') or not _re.fullmatch(r'[A-Za-z0-9_-]{1,40}', dir):
        raise HTTPException(400, 'bad scenario/dir')
    path = ROOT / 'runs' / 'cpp' / dir / (scenario + '.jsonl')
    if not path.is_file():
        raise HTTPException(404, '本地回放不存在：先运行 scripts\\cpp_run.cmd <草稿.cpp> runs\\cpp\\' + dir)
    events = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return dict(events=events, source='local-compile', dir=dir)

# 45 关核对表

每关一行：学习目标｜适用引擎与版本｜关键结论｜对应代码或官方资料｜正常/边界/错误用例｜回放证据步｜未验证项。
页面文案与判题共用 lab/course_content.py 结构化数据；结论区分 SQLite / PostgreSQL / 教学 C++，不跨引擎套用。

## 00a · 00A 打开空库
- 学习目标：空库长什么样：文件、连接、表各是什么？
- 引擎与版本：SQLite 3.37
- 关键结论：空库=文件存在+连接成功+目录表0行；查询不改数据。
- 代码/资料：lab/course_engines.py _observe；SQLite sqlite_master 目录表
- 用例：正常:空库0行；边界:重跑仍0行；错误:查 type=index 意图不符
- 回放证据：回放"查询表列表"步的 sqlite_master 行数

## 00b · 00B 建第一张表
- 学习目标：为什么重复的商品编号插不进去？
- 引擎与版本：SQLite 3.37
- 关键结论：PRIMARY KEY 拒绝重复键；类型 CHECK 拒绝非法值。
- 代码/资料：lab/course_engines.py _ddl；PRAGMA table_info
- 用例：正常:建表+首插；边界:恰好重复主键；错误:去掉 PRIMARY KEY
- 回放证据：回放"第二次插入同主键"步的 error 与行数

## 00c · 00C 插一笔商品
- 学习目标：一条 INSERT 到底改了什么？
- 引擎与版本：SQLite 3.37
- 关键结论：一条 INSERT 增一行；列序与值序必须对应。
- 代码/资料：lab/course_engines.py _insert
- 用例：正常:空表→1行；边界:再插同主键；错误:列序颠倒触发 CHECK
- 回放证据：回放插入前后快照的行差

## 00d · 00D 关掉再连接
- 学习目标：关掉连接，数据还在吗？
- 引擎与版本：SQLite 3.37
- 关键结论：提交过的数据在连接关闭后仍可由新连接读到。
- 代码/资料：lab/course_engines.py _persist
- 用例：正常:两连接同读；边界:关后复核；错误:查不存在的 sku
- 回放证据：回放"两个连接都关闭后复核"步

## select · 读懂一张表
- 学习目标：查询会不会偷偷改库存？
- 引擎与版本：SQLite 3.37
- 关键结论：SELECT 不改变数据；返回列由投影决定。
- 代码/资料：lab/course_server.py _one(select)
- 用例：正常:两列；边界:空表/单行；错误:只选 one 列
- 回放证据：回放"读完后的库存"步与运行前一致

## where · 筛出有货商品
- 学习目标：「至少2件」包不包含恰好2件？
- 引擎与版本：SQLite 3.37
- 关键结论：>= 包含边界，> 不包含。
- 代码/资料：lab/course_server.py _one(where)
- 用例：正常:>=2；边界:恰好2；错误:>2
- 回放证据：回放读取步命中行

## fk · 外键拦住无效编号
- 学习目标：订单指向不存在的商品会怎样？
- 引擎与版本：SQLite 3.37
- 关键结论：REFERENCES 在写入时拒绝悬空引用。
- 代码/资料：lab/course_engines.py _fk；PRAGMA foreign_keys=ON
- 用例：正常:有效 sku；边界:无效 Z；错误:去掉 REFERENCES
- 回放证据：回放"插入订单 sku=Z 被拒绝"步

## join · JOIN 商品与订单
- 学习目标：孤儿订单为什么不在 JOIN 结果里？
- 引擎与版本：SQLite 3.37
- 关键结论：INNER JOIN 过滤无匹配行。
- 代码/资料：lab/course_engines.py _join
- 用例：正常:匹配2行；边界:孤儿；错误:ON 错列
- 回放证据：回放 JOIN 步结果行

## stock · 先查库存
- 学习目标：「库存为0」和「商品不存在」一样吗？
- 引擎与版本：SQLite 3.37
- 关键结论：查到 qty=0 ≠ 查不到行。
- 代码/资料：lab/course_server.py _one(stock)
- 用例：正常:恰好够；边界:0；错误:不存在
- 回放证据：回放各用例读取步

## decrement · 条件扣减
- 学习目标：为什么扣减要把条件放进 WHERE？
- 引擎与版本：SQLite 3.37
- 关键结论：条件 UPDATE + rowcount 检查防负库存。
- 代码/资料：lab/course_server.py _one(decrement)；refs commerce-ledger service.place_order
- 用例：正常:够；边界:0；错误:无条件
- 回放证据：回放执行写入步 rowcount

## commit · 提交成功
- 学习目标：提交之后别人才能看见？
- 引擎与版本：SQLite 3.37
- 关键结论：COMMIT 前仅本事务可见，COMMIT 后持久。
- 代码/资料：lab/course_engines.py _txn(commit)
- 用例：正常:提交；边界:pending；错误:no-op 写入
- 回放证据：回放 pending→committed 步

## rollback · 写入后出错并回滚
- 学习目标：回滚后到底留下什么？
- 引擎与版本：SQLite 3.37
- 关键结论：ROLLED_BACK 不留任何写入；判题用最终快照。
- 代码/资料：lab/course_engines.py _txn(rollback)
- 用例：正常:回滚；边界:pending；错误:no-op
- 回放证据：回放 rolled_back 步与对照卡

## retry · 同键重试不重复扣减
- 学习目标：重试为什么会重复扣库存？
- 引擎与版本：SQLite 3.37
- 关键结论：同键守卫使重试影响0行。
- 代码/资料：lab/course_engines.py _retry
- 用例：正常:守卫；边界:重试；错误:无守卫
- 回放证据：回放尝试2扣减步 rowcount

## choice · 自己选提交或回滚
- 学习目标：提交还是回滚，谁说了算？
- 引擎与版本：SQLite 3.37
- 关键结论：最终状态由 COMMIT/ROLLBACK 选择决定。
- 代码/资料：lab/course_engines.py _txn via choice
- 用例：正常:commit；边界:rollback；错误:no-op
- 回放证据：回放最终步

## order · 完整下单
- 学习目标：一笔订单失败时留下半笔吗？
- 引擎与版本：SQLite 3.37
- 关键结论：整单原子；409 业务拒绝 vs 200 成功。
- 代码/资料：lab/course_engines.py _order；示意 POST /orders 非真实接口
- 用例：正常:success；边界:insufficient；错误:missing/无条件
- 回放证据：回放回滚步与 http/frontend 步

## wait · 普通等待：B 等 A 释放
- 学习目标：B 等 A 的时候算失败吗？
- 引擎与版本：SQLite 3.37
- 关键结论：BEGIN IMMEDIATE 串行化；等待后继续。
- 代码/资料：lab/course_engines.py _wait；sqlite.org/isolation.html
- 用例：正常:等待后成功；边界:持锁700ms；错误:no-op
- 回放证据：回放 B 等待→获锁步

## busy · 锁忙 vs 库存不足
- 学习目标：锁忙和库存不足，恢复路径一样吗？
- 引擎与版本：SQLite 3.37
- 关键结论：DATABASE_BUSY 零写入可重试；INSUFFICIENT_STOCK 回滚。
- 代码/资料：lab/course_engines.py _busy
- 用例：正常:重试成功；边界:超时；错误:no-op
- 回放证据：回放锁忙失败步零写入

## pg-connect · 连接与建表（真 PG）
- 学习目标：真 PostgreSQL 的空库和重复键？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：23505 unique_violation；重连持久。
- 代码/资料：lab/course_pg.py _pg_ddl；PG docs unique constraints
- 用例：正常:建表；边界:重复键；错误:无主键
- 回放证据：回放重复主键步 sqlstate

## pg-iso · 双连接隔离级别
- 学习目标：A 提交了 B 就一定看见吗？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：RC 每语句新快照；RR 首条取快照语句固定；BEGIN 不固定。
- 代码/资料：lab/course_pg.py _pg_iso；postgresql.org/docs/18/transaction-iso.html
- 用例：正常:rc/rr；边界:begin_only；错误:查 id=2 空结果
- 回放证据：回放四读取步

## pg-lock · 行锁与锁等待
- 学习目标：锁住一行会锁住整张表吗？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：行锁不锁表；55P03 lock_not_available 零写入。
- 代码/资料：lab/course_pg.py _pg_lock；postgresql.org/docs/18/explicit-locking.html
- 用例：正常:same；边界:different；错误:timeout 无 WHERE
- 回放证据：回放 B 等待/超时步

## deadlock · 死锁：互相等待（真 PG）
- 学习目标：死锁时谁被牺牲？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：40P01 deadlock_detected；被中止者回滚。
- 代码/资料：lab/course_pg.py _pg_deadlock；PG explicit-locking
- 用例：正常:uniform；边界:cross；错误:—
- 回放证据：回放被中止步 sqlstate

## pg-plan · 索引与执行计划
- 学习目标：加了索引查询结果会变吗？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：结果不变；计划可变；不强制走索引。
- 代码/资料：lab/course_pg.py _pg_plan；PG EXPLAIN
- 用例：正常:high；边界:low/small；错误:absent 0行
- 回放证据：回放前后计划步与基准行数

## exit · 进程退出：提交前 vs 提交后
- 学习目标：进程退出和回滚是一回事吗？
- 引擎与版本：SQLite 3.37
- 关键结论：退出时点决定留存；提交后同键回放。
- 代码/资料：lab/course_exit_child.py；refs hotel audit_sqlite_crashes
- 用例：正常:提交后；边界:提交前；错误:插错表
- 回放证据：回放退出后查实际表步

## pg-contrast · 等待 / 锁忙 / 死锁同例对照
- 学习目标：等待、锁忙、死锁怎么区分？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：等待≠失败；55P03 零写入；40P01 中止一笔。
- 代码/资料：lab/course_pg.py _pg_contrast；PG explicit-locking
- 用例：正常:对照；边界:超时；错误:交叉
- 回放证据：回放三组泳道步

## pg-guide-sales-schema · 销售引导 1 · 建商品和订单表
- 学习目标：销售引导：建表为什么这样做？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：销售 建表 的最小正确实现。
- 代码/资料：lab/course_build.py GUIDE_STARTERS；lab/build_harness.py
- 用例：正常:参考；边界:stage 用例；错误:留空草稿
- 回放证据：回放 harness 测试步

## pg-guide-sales-single · 销售引导 2 · 单次下单
- 学习目标：销售引导：单次写入为什么这样做？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：销售 单次写入 的最小正确实现。
- 代码/资料：lab/course_build.py GUIDE_STARTERS；lab/build_harness.py
- 用例：正常:参考；边界:stage 用例；错误:留空草稿
- 回放证据：回放 harness 测试步

## pg-guide-sales-insufficient · 销售引导 3 · 库存不足回滚
- 学习目标：销售引导：不足回滚为什么这样做？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：销售 不足回滚 的最小正确实现。
- 代码/资料：lab/course_build.py GUIDE_STARTERS；lab/build_harness.py
- 用例：正常:参考；边界:stage 用例；错误:留空草稿
- 回放证据：回放 harness 测试步

## pg-guide-sales-retry · 销售引导 4 · 重复请求
- 学习目标：销售引导：同键重试为什么这样做？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：销售 同键重试 的最小正确实现。
- 代码/资料：lab/course_build.py GUIDE_STARTERS；lab/build_harness.py
- 用例：正常:参考；边界:stage 用例；错误:留空草稿
- 回放证据：回放 harness 测试步

## pg-guide-sales-race · 销售引导 5 · 两笔抢最后一件
- 学习目标：销售引导：并发抢最后一件为什么这样做？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：销售 并发抢最后一件 的最小正确实现。
- 代码/资料：lab/course_build.py GUIDE_STARTERS；lab/build_harness.py
- 用例：正常:参考；边界:stage 用例；错误:留空草稿
- 回放证据：回放 harness 测试步

## pg-guide-hotel-schema · 酒店引导 1 · 建房间和房晚表
- 学习目标：酒店引导：建表为什么这样做？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：酒店 建表 的最小正确实现。
- 代码/资料：lab/course_build.py GUIDE_STARTERS；lab/build_harness.py
- 用例：正常:参考；边界:stage 用例；错误:留空草稿
- 回放证据：回放 harness 测试步

## pg-guide-hotel-single · 酒店引导 2 · 单晚预订
- 学习目标：酒店引导：单晚为什么这样做？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：酒店 单晚 的最小正确实现。
- 代码/资料：lab/course_build.py GUIDE_STARTERS；lab/build_harness.py
- 用例：正常:参考；边界:stage 用例；错误:留空草稿
- 回放证据：回放 harness 测试步

## pg-guide-hotel-twonights · 酒店引导 3 · 两晚必须同一间房
- 学习目标：酒店引导：两晚同房为什么这样做？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：酒店 两晚同房 的最小正确实现。
- 代码/资料：lab/course_build.py GUIDE_STARTERS；lab/build_harness.py
- 用例：正常:参考；边界:stage 用例；错误:留空草稿
- 回放证据：回放 harness 测试步

## pg-guide-hotel-conflict · 酒店引导 4 · 第二晚冲突整单回滚
- 学习目标：酒店引导：冲突整单回滚为什么这样做？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：酒店 冲突整单回滚 的最小正确实现。
- 代码/资料：lab/course_build.py GUIDE_STARTERS；lab/build_harness.py
- 用例：正常:参考；边界:stage 用例；错误:留空草稿
- 回放证据：回放 harness 测试步

## pg-guide-hotel-retry · 酒店引导 5 · 同键重试
- 学习目标：酒店引导：同键重试为什么这样做？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：酒店 同键重试 的最小正确实现。
- 代码/资料：lab/course_build.py GUIDE_STARTERS；lab/build_harness.py
- 用例：正常:参考；边界:stage 用例；错误:留空草稿
- 回放证据：回放 harness 测试步

## pg-final-sales · 销售无提示综合
- 学习目标：不给提示，你能写对销售服务吗？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：满足契约的最小可用销售服务。
- 代码/资料：lab/course_build.py CONTRACT_SALES；build_harness
- 用例：正常:参考；边界:race/retry；错误:骨架
- 回放证据：回放 harness 全测试步
- 未验证：同时同键并发提交未在本课程验证

## pg-final-hotel · 酒店无提示综合
- 学习目标：不给提示，你能写对酒店预订吗？
- 引擎与版本：PostgreSQL 18.6
- 关键结论：满足契约的最小可用酒店预订。
- 代码/资料：lab/course_build.py CONTRACT_HOTEL；build_harness
- 用例：正常:参考；边界:conflict/retry；错误:骨架
- 回放证据：回放 harness 全测试步

## pg-build-sales · 开写综合 · 销售服务
- 学习目标：开写销售：从空目录到可用服务
- 引擎与版本：PostgreSQL 18.6
- 关键结论：契约满足即通过。
- 代码/资料：lab/course_build.py；build_harness
- 用例：正常:参考；边界:race；错误:骨架
- 回放证据：回放 harness 步
- 未验证：同时同键并发提交未在本课程验证

## pg-build-hotel · 开写综合 · 酒店预订服务
- 学习目标：开写酒店：从空目录到可用服务
- 引擎与版本：PostgreSQL 18.6
- 关键结论：契约满足即通过。
- 代码/资料：lab/course_build.py；build_harness
- 用例：正常:参考；边界:conflict；错误:骨架
- 回放证据：回放 harness 步

## cpp-page · 存储页
- 学习目标：一页装不下时会发生什么？
- 引擎与版本：教学 C++（不等同 PostgreSQL）
- 关键结论：满页拒绝整条；关闭后磁盘重读。
- 代码/资料：lab/cpp/teach.cpp cmd_page
- 用例：正常:empty；边界:exact；错误:overflow
- 回放证据：回放 insert/reopen_read 步 free 与 records

## cpp-buffer · 缓冲池
- 学习目标：被固定的页能被淘汰吗？
- 引擎与版本：教学 C++（不等同 PostgreSQL）
- 关键结论：pin 不可淘汰；脏页写回。
- 代码/资料：lab/cpp/teach.cpp cmd_buffer
- 用例：正常:cap1；边界:pinned；错误:unpin
- 回放证据：回放 evict_blocked/writeback 步

## cpp-btree · B+ 树
- 学习目标：叶满了再插会怎样？
- 引擎与版本：教学 C++（不等同 PostgreSQL）
- 关键结论：分裂+叶链扫描；重复键拒绝。
- 代码/资料：lab/cpp/teach.cpp cmd_btree
- 用例：正常:split；边界:scan/missing；错误:dup
- 回放证据：回放 split/scan 步 nodes

## cpp-exec · 查询执行
- 学习目标：每个算子交出哪些行？
- 引擎与版本：教学 C++（不等同 PostgreSQL）
- 关键结论：算子输入输出可核对；>= 含边界。
- 代码/资料：lab/cpp/teach.cpp cmd_exec
- 用例：正常:boundary；边界:empty；错误:阈值3
- 回放证据：回放 Filter/Project 步 rows

## cpp-locks · 并发控制
- 学习目标：回滚会释放锁吗？
- 引擎与版本：教学 C++（不等同 PostgreSQL）
- 关键结论：冲突串行；结束释放。
- 代码/资料：lab/cpp/teach.cpp cmd_locks
- 用例：正常:conflict；边界:rollback/shared；错误:恒兼容草稿
- 回放证据：回放 wait/release/end 步

## sales · 销售：两笔订单抢一件
- 学习目标：两个人同时买最后一件，为什么只卖出一件？
- 引擎与版本：SQLite 3.37（BEGIN IMMEDIATE）
- 关键结论：不超卖靠条件 UPDATE+rowcount+事务；同键同内容回放、同键不同内容冲突。
- 代码/资料：lab/problems/sales.py place_order；refs commerce-ledger@0659bd5 app/service.py
- 用例：正常:race 一成功一不足；边界:同键重试；错误:同键不同内容/先查后写/漏幂等
- 回放证据：回放各 UPDATE 步 rowcount 与 commit/rollback 步、最终 inventory/orders/movements
- 未验证：同时同键并发提交未在本课程验证（顺序重试通过不代表并发同键已验证）

## hotel · 酒店：连续两晚同一间
- 学习目标：两晚必须同一间房，第二晚失败时第一晚去哪了？
- 引擎与版本：SQLite 3.37（BEGIN IMMEDIATE）
- 关键结论：交集保证同房；整单回滚不留半笔；REJECTED 持久化。
- 代码/资料：lab/problems/hotel.py reserve；refs hotel-reservation-lab@7151ff8 sqlite_store.reserve
- 用例：正常:两晚同房；边界:无交集拒订/第二晚失败；错误:逐晚挑房/拒订不落库
- 回放证据：回放 booking_nights/active_room_nights 增删步与 rolled_back 步、新连接复核

## 术语白话（首次出现先白话后术语）
- 事务：一组要么全做、要么全不做的操作；提交前别人看不见，回滚后当作没发生。
- 幂等：同一个请求重复发多次，效果和实施一次相同；靠请求键+内容摘要识别"是同一次"。
- 锁：数据库给正在改的数据挂的"占用牌"；别人要改同一处就得等或放弃。
- 快照：某一时刻数据库的一致性照片；隔离级别决定何时拍、拍多久。
- 主键：一张表里唯一标识一行的字段；重复主键会被数据库拒绝。
- 外键：指向另一张表主键的字段；指向不存在的行会被拒绝。
- 死锁：两笔事务互相等对方释放锁，形成环；数据库检测后中止一笔。
- 执行计划：数据库打算怎么执行一条查询的步骤树；估计行数与实际行数可能不同。

## 官方参考
- https://www.postgresql.org/docs/18/transaction-iso.html
- https://www.postgresql.org/docs/18/explicit-locking.html
- https://www.sqlite.org/isolation.html
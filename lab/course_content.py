"""结构化课程内容：阅读顺序、三张为什么卡、术语白话、核对表。

页面文案与判题共用同一份契约/参考解/harness 来源，避免各写一套。
diagram.aux 指定辅助视图：lanes(并发双泳道)/grid(房晚交集)/tree(计划或B+树)/cells(页或缓冲帧)。
因果链主视图由真实运行事件+快照生成，不在此硬编码结果。
"""

GLOSSARY = {
    '事务': '一组要么全做、要么全不做的操作；提交前别人看不见，回滚后当作没发生。',
    '幂等': '同一个请求重复发多次，效果和实施一次相同；靠请求键+内容摘要识别"是同一次"。',
    '锁': '数据库给正在改的数据挂的"占用牌"；别人要改同一处就得等或放弃。',
    '快照': '某一时刻数据库的一致性照片；隔离级别决定何时拍、拍多久。',
    '主键': '一张表里唯一标识一行的字段；重复主键会被数据库拒绝。',
    '外键': '指向另一张表主键的字段；指向不存在的行会被拒绝。',
    '死锁': '两笔事务互相等对方释放锁，形成环；数据库检测后中止一笔。',
    '执行计划': '数据库打算怎么执行一条查询的步骤树；估计行数与实际行数可能不同。',
}

# 每关：problem(业务问题标题) / why[发生什么,为什么,怎么验证] / aux / glossary / checklist
C = {}


def add(id, problem, why, aux='flow', gloss=(), engine='SQLite 3.37', conclusion='', refs='', cases='', evidence='', unverified=()):
    C[id] = dict(problem=problem, why=why, aux=aux, gloss=list(gloss),
                 checklist=dict(goal=problem, engine=engine, conclusion=conclusion,
                                refs=refs, cases=cases, evidence=evidence,
                                unverified=list(unverified)))


add('00a', '空库长什么样：文件、连接、表各是什么？',
    ['打开一个全新数据库文件，目录表 sqlite_master 里 0 行。',
     '文件是仓库、连接是通道、表是货架；空仓库=文件在、通道通、货架0个。',
     '运行后看连接卡与表列表；重跑仍是 0 行，证明每次都是新文件。'],
    aux='flow', gloss=(), engine='SQLite 3.37',
    conclusion='空库=文件存在+连接成功+目录表0行；查询不改数据。',
    refs='lab/course_engines.py _observe；SQLite sqlite_master 目录表',
    cases='正常:空库0行；边界:重跑仍0行；错误:查 type=index 意图不符',
    evidence='回放"查询表列表"步的 sqlite_master 行数')
add('00b', '为什么重复的商品编号插不进去？',
    ['建表后连插两次同 sku，第二次被拒绝。',
     '主键唯一性由数据库在写入时强制，不靠代码记得检查。',
     '看第二次插入的报错 UNIQUE constraint failed 与表里仍1行。'],
    aux='flow', gloss=('主键',), engine='SQLite 3.37',
    conclusion='PRIMARY KEY 拒绝重复键；类型 CHECK 拒绝非法值。',
    refs='lab/course_engines.py _ddl；PRAGMA table_info',
    cases='正常:建表+首插；边界:恰好重复主键；错误:去掉 PRIMARY KEY',
    evidence='回放"第二次插入同主键"步的 error 与行数')
add('00c', '一条 INSERT 到底改了什么？',
    ['空表执行一条 INSERT 后多出恰好一行。',
     'INSERT 是新增一行的唯一入口；前后对照即真实变化。',
     '对比"插入前空表"与"执行你的INSERT"两步的行；再跑一次被主键拒。'],
    aux='flow', gloss=('主键',), engine='SQLite 3.37',
    conclusion='一条 INSERT 增一行；列序与值序必须对应。',
    refs='lab/course_engines.py _insert',
    cases='正常:空表→1行；边界:再插同主键；错误:列序颠倒触发 CHECK',
    evidence='回放插入前后快照的行差')
add('00d', '关掉连接，数据还在吗？',
    ['两个连接先后读同一行，都关闭后第三个连接仍读到。',
     '持久化=数据住在文件里不住在连接里；关连接只是挂电话。',
     '看连接1/2/3 的读取一致与文件复核卡 size>0。'],
    aux='flow', gloss=(), engine='SQLite 3.37',
    conclusion='提交过的数据在连接关闭后仍可由新连接读到。',
    refs='lab/course_engines.py _persist',
    cases='正常:两连接同读；边界:关后复核；错误:查不存在的 sku',
    evidence='回放"两个连接都关闭后复核"步')
add('select', '查询会不会偷偷改库存？',
    ['SELECT 前后库存完全一样。',
     'SELECT 只读不写；读与写的第一道分界。',
     '对比运行前与读完后快照；命中行亮蓝。'],
    aux='flow', gloss=(), engine='SQLite 3.37',
    conclusion='SELECT 不改变数据；返回列由投影决定。',
    refs='lab/course_server.py _one(select)',
    cases='正常:两列；边界:空表/单行；错误:只选 one 列',
    evidence='回放"读完后的库存"步与运行前一致')
add('where', '「至少2件」包不包含恰好2件？',
    ['qty>2 漏掉恰好2的行；qty>=2 才包含。',
     'WHERE 逐行判断；边界值是最易错的一行。',
     '看边界用例命中行是否含 qty=2。'],
    aux='flow', gloss=(), engine='SQLite 3.37',
    conclusion='>= 包含边界，> 不包含。',
    refs='lab/course_server.py _one(where)',
    cases='正常:>=2；边界:恰好2；错误:>2',
    evidence='回放读取步命中行')
add('fk', '订单指向不存在的商品会怎样？',
    ['插 sku=Z 的订单被外键拒绝。',
     '外键让数据库在写入时检查被引用行存在。',
     '看无效编号步的 FOREIGN KEY 报错与订单表0行。'],
    aux='flow', gloss=('外键',), engine='SQLite 3.37',
    conclusion='REFERENCES 在写入时拒绝悬空引用。',
    refs='lab/course_engines.py _fk；PRAGMA foreign_keys=ON',
    cases='正常:有效 sku；边界:无效 Z；错误:去掉 REFERENCES',
    evidence='回放"插入订单 sku=Z 被拒绝"步')
add('join', '孤儿订单为什么不在 JOIN 结果里？',
    ['3笔订单1笔孤儿，INNER JOIN 只出2行。',
     'INNER JOIN 只保留两边匹配的行；孤儿被滤是语义不是bug。',
     '对照种子表与查询结果行。'],
    aux='flow', gloss=(), engine='SQLite 3.37',
    conclusion='INNER JOIN 过滤无匹配行。',
    refs='lab/course_engines.py _join',
    cases='正常:匹配2行；边界:孤儿；错误:ON 错列',
    evidence='回放 JOIN 步结果行')
add('stock', '「库存为0」和「商品不存在」一样吗？',
    ['A=0 返回1行 qty=0；A 不在表返回0行。',
     '两种失败语义不同：售完 vs 不存在。',
     '切换三个用例看返回行数。'],
    aux='flow', gloss=(), engine='SQLite 3.37',
    conclusion='查到 qty=0 ≠ 查不到行。',
    refs='lab/course_server.py _one(stock)',
    cases='正常:恰好够；边界:0；错误:不存在',
    evidence='回放各用例读取步')
add('decrement', '为什么扣减要把条件放进 WHERE？',
    ['A=0 时条件扣减影响0行；无条件扣减会撞 CHECK。',
     '把够不够交给数据库同语句判断；rowcount 是唯一可信信号。',
     '看影响行数与是否报错。'],
    aux='flow', gloss=(), engine='SQLite 3.37',
    conclusion='条件 UPDATE + rowcount 检查防负库存。',
    refs='lab/course_server.py _one(decrement)；refs commerce-ledger service.place_order',
    cases='正常:够；边界:0；错误:无条件',
    evidence='回放执行写入步 rowcount')
add('commit', '提交之后别人才能看见？',
    ['事务内 A=3 暂时可见；COMMIT 后新连接读到3。',
     'COMMIT 让写入持久化并对其他连接可见。',
     '对比 pending 与 committed 快照及新连接复核。'],
    aux='flow', gloss=('事务',), engine='SQLite 3.37',
    conclusion='COMMIT 前仅本事务可见，COMMIT 后持久。',
    refs='lab/course_engines.py _txn(commit)',
    cases='正常:提交；边界:pending；错误:no-op 写入',
    evidence='回放 pending→committed 步')
add('rollback', '回滚后到底留下什么？',
    ['事务内 A=3 是临时；回滚后最终 A=2。',
     '回滚撤销事务内全部写入；最终状态以独立连接复核为准。',
     '同屏看"事务内A=3"与"回滚后A=2"对照卡。'],
    aux='flow', gloss=('事务',), engine='SQLite 3.37',
    conclusion='ROLLED_BACK 不留任何写入；判题用最终快照。',
    refs='lab/course_engines.py _txn(rollback)',
    cases='正常:回滚；边界:pending；错误:no-op',
    evidence='回放 rolled_back 步与对照卡')
add('retry', '重试为什么会重复扣库存？',
    ['无守卫时重试再扣1行；加同键守卫后重试0行。',
     '重试是找回上次结果不是再买一次；先查同键订单再碰库存。',
     '看尝试1/尝试2 影响行数对照。'],
    aux='lanes', gloss=('幂等',), engine='SQLite 3.37',
    conclusion='同键守卫使重试影响0行。',
    refs='lab/course_engines.py _retry',
    cases='正常:守卫；边界:重试；错误:无守卫',
    evidence='回放尝试2扣减步 rowcount')
add('choice', '提交还是回滚，谁说了算？',
    ['同一条写入，选 COMMIT 留3、选 ROLLBACK 留2。',
     '事务边界在使用者手里；边界选择即事务语义。',
     '切换选择重跑看最终快照。'],
    aux='flow', gloss=('事务',), engine='SQLite 3.37',
    conclusion='最终状态由 COMMIT/ROLLBACK 选择决定。',
    refs='lab/course_engines.py _txn via choice',
    cases='正常:commit；边界:rollback；错误:no-op',
    evidence='回放最终步')
add('order', '一笔订单失败时留下半笔吗？',
    ['库存不足时整单回滚，订单行也消失。',
     '下单是链：任一步失败整单回滚；HTTP 与业务结果两层。',
     '切场景看响应与最终表；回滚步订单0行。'],
    aux='flow', gloss=('事务', '幂等'), engine='SQLite 3.37',
    conclusion='整单原子；409 业务拒绝 vs 200 成功。',
    refs='lab/course_engines.py _order；示意 POST /orders 非真实接口',
    cases='正常:success；边界:insufficient；错误:missing/无条件',
    evidence='回放回滚步与 http/frontend 步')
add('wait', 'B 等 A 的时候算失败吗？',
    ['B 的 BEGIN 停在等待，A 提交后 B 继续成功。',
     '等待=排队不是失败；写锁串行化写入。',
     '看双泳道 B 的等待段与最终两行各扣1。'],
    aux='lanes', gloss=('锁',), engine='SQLite 3.37',
    conclusion='BEGIN IMMEDIATE 串行化；等待后继续。',
    refs='lab/course_engines.py _wait；sqlite.org/isolation.html',
    cases='正常:等待后成功；边界:持锁700ms；错误:no-op',
    evidence='回放 B 等待→获锁步')
add('busy', '锁忙和库存不足，恢复路径一样吗？',
    ['锁忙零写入可同键重试；库存不足整单回滚重试也无用。',
     '锁忙=事务没开始；库存不足=事务开始但条件不满足。',
     '看同处对照卡与重试结果。'],
    aux='lanes', gloss=('锁',), engine='SQLite 3.37',
    conclusion='DATABASE_BUSY 零写入可重试；INSUFFICIENT_STOCK 回滚。',
    refs='lab/course_engines.py _busy',
    cases='正常:重试成功；边界:超时；错误:no-op',
    evidence='回放锁忙失败步零写入')
add('exit', '进程退出和回滚是一回事吗？',
    ['提交前退出0行；提交后退出保留且同键找回。',
     '提交前退出=连接断开事务回滚；提交后=数据已在磁盘。',
     '先猜再查两用例实际表与找回标记。'],
    aux='lanes', gloss=('事务',), engine='SQLite 3.37',
    conclusion='退出时点决定留存；提交后同键回放。',
    refs='lab/course_exit_child.py；refs hotel audit_sqlite_crashes',
    cases='正常:提交后；边界:提交前；错误:插错表',
    evidence='回放退出后查实际表步')
add('pg-connect', '真 PostgreSQL 的空库和重复键？',
    ['独立 schema 建表插数；重复键报 23505；重连仍在。',
     '约束在服务端执行；schema 是隔离边界。',
     '看四层链路卡与 23505 报错、重连读取。'],
    aux='flow', gloss=('主键',), engine='PostgreSQL 18.6',
    conclusion='23505 unique_violation；重连持久。',
    refs='lab/course_pg.py _pg_ddl；PG docs unique constraints',
    cases='正常:建表；边界:重复键；错误:无主键',
    evidence='回放重复主键步 sqlstate')
add('pg-iso', 'A 提交了 B 就一定看见吗？',
    ['RC 提交后见新值；RR 首条取快照语句后不变；只 BEGIN 不固定快照。',
     '快照由事务内第一条获取快照的语句建立（PG RR）。',
     '看 before/after/begin_only/rollback 四读取与双泳道。'],
    aux='lanes', gloss=('快照', '事务'), engine='PostgreSQL 18.6',
    conclusion='RC 每语句新快照；RR 首条取快照语句固定；BEGIN 不固定。',
    refs='lab/course_pg.py _pg_iso；postgresql.org/docs/18/transaction-iso.html',
    cases='正常:rc/rr；边界:begin_only；错误:查 id=2 空结果',
    evidence='回放四读取步',
    unverified=())
add('pg-lock', '锁住一行会锁住整张表吗？',
    ['同行等待、异行并行、超时55P03零写入。',
     '行锁粒度=行；超时回滚无部分写入。',
     '切三场景看双泳道与最终表。'],
    aux='lanes', gloss=('锁',), engine='PostgreSQL 18.6',
    conclusion='行锁不锁表；55P03 lock_not_available 零写入。',
    refs='lab/course_pg.py _pg_lock；postgresql.org/docs/18/explicit-locking.html',
    cases='正常:same；边界:different；错误:timeout 无 WHERE',
    evidence='回放 B 等待/超时步')
add('pg-contrast', '等待、锁忙、死锁怎么区分？',
    ['同一张两行表：等待成功、超时55P03、交叉40P01。',
     '三者恢复路径不同；死锁=等待图成环。',
     '看双泳道三种命运与最终表。'],
    aux='lanes', gloss=('锁', '死锁'), engine='PostgreSQL 18.6',
    conclusion='等待≠失败；55P03 零写入；40P01 中止一笔。',
    refs='lab/course_pg.py _pg_contrast；PG explicit-locking',
    cases='正常:对照；边界:超时；错误:交叉',
    evidence='回放三组泳道步')
add('deadlock', '死锁时谁被牺牲？',
    ['交叉加锁成环，一笔40P01回滚另一笔提交；统一顺序全提交。',
     '数据库检测环并中止一笔；统一锁顺序消除环。',
     '看等待图与 cross/uniform 结果、最终表。'],
    aux='lanes', gloss=('死锁', '锁'), engine='PostgreSQL 18.6',
    conclusion='40P01 deadlock_detected；被中止者回滚。',
    refs='lab/course_pg.py _pg_deadlock；PG explicit-locking',
    cases='正常:uniform；边界:cross；错误:—',
    evidence='回放被中止步 sqlstate')
add('pg-plan', '加了索引查询结果会变吗？',
    ['建索引前后结果相同（行序不计）；计划树可能变。',
     '索引是访问路径不是语义；是否被优化器选中另说。',
     '对照独立基准行数与前后计划树估计/实际/缓冲区。'],
    aux='tree', gloss=('执行计划',), engine='PostgreSQL 18.6',
    conclusion='结果不变；计划可变；不强制走索引。',
    refs='lab/course_pg.py _pg_plan；PG EXPLAIN',
    cases='正常:high；边界:low/small；错误:absent 0行',
    evidence='回放前后计划步与基准行数')
for kind, cn in (('sales', '销售'), ('hotel', '酒店')):
    stages = [('schema', '建表'), ('single', '单次写入'), ('insufficient', '不足回滚'), ('retry', '同键重试'), ('race', '并发抢最后一件')] if kind == 'sales' else \
             [('schema', '建表'), ('single', '单晚'), ('twonights', '两晚同房'), ('conflict', '冲突整单回滚'), ('retry', '同键重试')]
    for stage, t in stages:
        add(f'pg-guide-{kind}-{stage}', f'{cn}引导：{t}为什么这样做？',
            [f'本关只补一处：{t}。', '最小业务规则决定表结构与事务边界。', '跑对应 stage 测试看通过/失败与最终表。'],
            aux='grid' if kind == 'hotel' else 'flow', gloss=('事务', '幂等'),
            engine='PostgreSQL 18.6',
            conclusion=f'{cn} {t} 的最小正确实现。',
            refs='lab/course_build.py GUIDE_STARTERS；lab/build_harness.py',
            cases='正常:参考；边界:stage 用例；错误:留空草稿',
            evidence='回放 harness 测试步')
add('pg-final-sales', '不给提示，你能写对销售服务吗？',
    ['独立补齐 schema+service；判题核对业务返回与新连接最终数据。',
     '契约=表结构+接口+事务+幂等；综合检验。',
     '跑基线测试+新连接复核；失败看具体断言。'],
    aux='flow', gloss=('事务', '幂等'), engine='PostgreSQL 18.6',
    conclusion='满足契约的最小可用销售服务。',
    refs='lab/course_build.py CONTRACT_SALES；build_harness',
    cases='正常:参考；边界:race/retry；错误:骨架',
    evidence='回放 harness 全测试步',
    unverified=('同时同键并发提交未在本课程验证',))
add('pg-final-hotel', '不给提示，你能写对酒店预订吗？',
    ['独立补齐两晚同房+整单撤回；新连接复核房晚。',
     '半开区间+交集+事务边界+唯一占用。',
     '跑基线测试+新连接复核。'],
    aux='grid', gloss=('事务',), engine='PostgreSQL 18.6',
    conclusion='满足契约的最小可用酒店预订。',
    refs='lab/course_build.py CONTRACT_HOTEL；build_harness',
    cases='正常:参考；边界:conflict/retry；错误:骨架',
    evidence='回放 harness 全测试步')
add('pg-build-sales', '开写销售：从空目录到可用服务',
    ['学生写 schema/service/tests；harness 基线+新连接复核。', '契约驱动；综合检验。', '看测试步与最终表。'],
    aux='flow', gloss=('事务', '幂等'), engine='PostgreSQL 18.6',
    conclusion='契约满足即通过。', refs='lab/course_build.py；build_harness',
    cases='正常:参考；边界:race；错误:骨架', evidence='回放 harness 步',
    unverified=('同时同键并发提交未在本课程验证',))
add('pg-build-hotel', '开写酒店：从空目录到可用服务',
    ['学生写两晚同房+整单撤回；新连接复核。', '契约驱动。', '看测试步与最终表。'],
    aux='grid', gloss=('事务',), engine='PostgreSQL 18.6',
    conclusion='契约满足即通过。', refs='lab/course_build.py；build_harness',
    cases='正常:参考；边界:conflict；错误:骨架', evidence='回放 harness 步')
add('cpp-page', '一页装不下时会发生什么？',
    ['空页插入成功；恰好装满 free=0；超容量整条拒绝；关后重读仍在。',
     '页=页头+记录槽+剩余空间；写前检查空间。',
     '看页格 free 变化与 reopen_read。'],
    aux='cells', gloss=(), engine='教学 C++（不等同 PostgreSQL）',
    conclusion='满页拒绝整条；关闭后磁盘重读。',
    refs='lab/cpp/teach.cpp cmd_page',
    cases='正常:empty；边界:exact；错误:overflow',
    evidence='回放 insert/reopen_read 步 free 与 records')
add('cpp-buffer', '被固定的页能被淘汰吗？',
    ['容量1命中/未命中/淘汰交替；固定页不可淘汰；脏页写回后读新值。',
     'pin 保护在用页；淘汰只选未固定；脏页先写回。',
     '看帧格 pin/dirty 与 writeback/reread。'],
    aux='cells', gloss=(), engine='教学 C++（不等同 PostgreSQL）',
    conclusion='pin 不可淘汰；脏页写回。',
    refs='lab/cpp/teach.cpp cmd_buffer',
    cases='正常:cap1；边界:pinned；错误:unpin',
    evidence='回放 evict_blocked/writeback 步')
add('cpp-btree', '叶满了再插会怎样？',
    ['点查走根到叶；叶满分裂提升；范围沿叶链；重复键拒绝。',
     'B+树数据在叶、叶间有链。',
     '看节点树分裂与 scan keys。'],
    aux='tree', gloss=(), engine='教学 C++（不等同 PostgreSQL）',
    conclusion='分裂+叶链扫描；重复键拒绝。',
    refs='lab/cpp/teach.cpp cmd_btree',
    cases='正常:split；边界:scan/missing；错误:dup',
    evidence='回放 split/scan 步 nodes')
add('cpp-exec', '每个算子交出哪些行？',
    ['Scan 全行→Filter 边界过滤→Project 选列。',
     '执行=算子流水线；索引减少 Scan 交出。',
     '看三算子 received/emitted 行。'],
    aux='flow', gloss=(), engine='教学 C++（不等同 PostgreSQL）',
    conclusion='算子输入输出可核对；>= 含边界。',
    refs='lab/cpp/teach.cpp cmd_exec',
    cases='正常:boundary；边界:empty；错误:阈值3',
    evidence='回放 Filter/Project 步 rows')
add('cpp-locks', '回滚会释放锁吗？',
    ['写写冲突等待→提交释放→获锁；回滚也释放；读读兼容。',
     '事务结束（提交或回滚）释放锁。',
     '看锁表 grant/wait/release 与 end 空表。'],
    aux='lanes', gloss=('锁', '事务'), engine='教学 C++（不等同 PostgreSQL）',
    conclusion='冲突串行；结束释放。',
    refs='lab/cpp/teach.cpp cmd_locks',
    cases='正常:conflict；边界:rollback/shared；错误:恒兼容草稿',
    evidence='回放 wait/release/end 步')
add('sales', '两个人同时买最后一件，为什么只卖出一件？',
    ['A/B 并发各买1：一笔条件 UPDATE 命中1行提交，另一笔命中0行整单回滚。',
     '条件扣减+事务原子+写锁串行化共同保证不超卖；库存最终0、订单1、流水1。',
     '切三案例看因果链：哪条 UPDATE 改了哪行、rowcount、提交/回滚归宿；同键重试0写入。'],
    aux='lanes', gloss=('事务', '幂等', '锁'), engine='SQLite 3.37（BEGIN IMMEDIATE）',
    conclusion='不超卖靠条件 UPDATE+rowcount+事务；同键同内容回放、同键不同内容冲突。',
    refs='lab/problems/sales.py place_order；refs commerce-ledger@0659bd5 app/service.py',
    cases='正常:race 一成功一不足；边界:同键重试；错误:同键不同内容/先查后写/漏幂等',
    evidence='回放各 UPDATE 步 rowcount 与 commit/rollback 步、最终 inventory/orders/movements',
    unverified=('同时同键并发提交未在本课程验证（顺序重试通过不代表并发同键已验证）',))
add('hotel', '两晚必须同一间房，第二晚失败时第一晚去哪了？',
    ['每晚可用房取交集得全程同房；无交集整单拒订；第二晚写失败回滚连第一晚占用一起撤销。',
     '半开区间离店日不占；事务边界覆盖整单；占用表唯一防双占。',
     '看房晚格交集与回滚前后占用；新连接复核最终房晚。'],
    aux='grid', gloss=('事务',), engine='SQLite 3.37（BEGIN IMMEDIATE）',
    conclusion='交集保证同房；整单回滚不留半笔；REJECTED 持久化。',
    refs='lab/problems/hotel.py reserve；refs hotel-reservation-lab@7151ff8 sqlite_store.reserve',
    cases='正常:两晚同房；边界:无交集拒订/第二晚失败；错误:逐晚挑房/拒订不落库',
    evidence='回放 booking_nights/active_room_nights 增删步与 rolled_back 步、新连接复核')


def content_for(lesson_id):
    return C.get(lesson_id)


def checklist_rows(lessons):
    rows = []
    for l in lessons:
        c = C.get(l['id'])
        if not c:
            continue
        ck = c['checklist']
        rows.append(dict(id=l['id'], title=l['title'], **ck))
    return rows
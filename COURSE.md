# 后端运行实验室 · 入门课程

打开 http://127.0.0.1:8022/course （Windows 可双击 start-course.cmd）。首页 #home 是 63 关知识地图。零基础必修 20 关（含两道 SQLite 综合），之后可学业务后端 PostgreSQL 20 关、云后端 18 关，或独立选修 C++ 内核 5 关。各路线独立计算进度，已知跳过的题可以配置后手动重试。独立项目入口为 http://127.0.0.1:8022/course/project；原实验室保留在 http://127.0.0.1:8021/。

从 00A 开始，先猜结果、再改一处、运行、看实际变化。通过后进入下一关；想跳过可以直接点课程目录。参考解不默认载入（预设下拉里手动载入）。

早期 19 关的迭代记录如下；当前路线顺序以首页为准：

- 第 0 章 · 空库到第一条记录：00A 打开空库（文件/连接/表）、00B 建第一张表（字段/类型/主键 + 重复主键用例）、00C 插一笔商品（空表→新增行）、00D 关掉再连接（两个连接 + 关后第三连接复核持久化）、读懂一张表、筛出有货商品、外键拦住无效编号、JOIN 商品与订单。
- 第 1 章 · 事务与订单：先查库存（恰好够/为 0/不存在三用例）、条件扣减（同三用例）、提交成功、写入后出错并回滚（同屏对照「事务内 A=3」与「回滚后 A=2」）、同键重试不重复扣减、自己选提交或回滚、完整下单（成功/库存不足/商品不存在可切换，链路与真实事件回放）。
- 第 2 章 · 四种容易混淆的情况：普通等待（双连接真实毫秒时间轴）、锁忙 vs 库存不足（同处对照 + 同键重试）、死锁（PostgreSQL；当前环境无 PG，标「示意 / 未实测」）、进程退出（提交前 vs 提交后，先预测再查实际表 + 原键找回）。
- 章末 · 综合应用：销售两笔订单抢一件、酒店连续两晚同一间（复用原实验运行器）。

每关只要求一个动作（补一个空 / 选一个选择 / 跑一次观察），题面一句话；原理、提示、完整日志、参考解都折叠或手动载入。涉及持久化、回滚、并发的判题都查实际数据库文件状态（runs/course/<run_id>/）。

前四关每个测试独立执行在内存 SQLite，仅允许题目对应的单条 SQL。时间轴显示真实查询结果或当前事务快照。「已提交」表示本次实验内的最终状态，不代表会存到下一次练习。最后两关继续使用原项目运行器和自己的临时数据库。浏览器里的练习进度是本机记录，不需要账号。当前版本不对外部署，没有 PostgreSQL 或公开视频能力。

## 画板版界面（2026-11-21 迭代）

页面是奶油白纸感大画板：左栏六关目录与进度；画板左题面与例子、右编辑器与「运行测试」；底部「测试结果／数据库回放」两个页签。首屏不铺 SQL 日志，提示与完整题面点开再看。

回放画板：命中的行亮蓝；写入标「待提交」；提交显示旧值 → 新值；回滚列出被收回的临时行并恢复旧状态。综合关把真实事件与快照按 seq 对齐，每步标明触发的请求／连接；酒店可按「正常拒订／写第一晚后回滚／提交后退出／退出后重试找回」等跳时点；外层 HTTP 状态与业务返回（BOOKED／REJECTED／ROLLED_BACK 等）用两枚标签分开显示。播放、暂停、单步、拖动、跳到提交／回滚都保留；「这一步的真实 SQL、影响行数、请求与响应」折叠在画板下方。未观测与教学示意事件带虚线标签。

QDuck 学习搭子：本地与会话中未找到原图，当前为原创小鸭 SVG 占位（气泡带「QDuck 占位 · 待换原图」标签），只在开场、打开提示、通过、回滚四个时机说一句话；开始改代码或运行时自动让位，不遮挡编辑器与结果，无循环动画。拿到原图后替换 course.html 里的 `#qduck` symbol 即可。

草稿与通关进度存 localStorage（键 backend-course-v2:practice）；地址栏 hash 对应关卡，可直接分享 #select / #hotel 等链接。小屏（≤820px）目录变横滑、画板单列，仍可运行、看表、切关。

## 三批次验收记录（2026-11-21）

无头验证 `scripts/verify_course.py`：22/22 用例符合预期（每关一正确一典型错误；死锁关验证「未实测」标注）。原实验室回归 `scripts/verify.py` 13/13。报告：runs/course-verify-report.json（含每关最终数据库文件复核）。

- 批次 1：学生补一个空（'table'/PRIMARY/1/'A'/REFERENCES/sku）。运行时真实打开隔离文件库：00B 二插同主键报 UNIQUE constraint failed；00D 两连接各读 A,1、关闭后第三连接复核仍在。最终数据见报告 final_db。
- 批次 2：学生补库存条件/加同键守卫/选 COMMIT 或 ROLLBACK。回滚关同屏给出事务内 A=3（临时）与回滚后 A=2（最终，独立连接复核）；重试关要求尝试 2 影响 0 行；下单关三场景响应与最终表一起判。
- 批次 3：等待关 B 的 BEGIN 真实晚于 A 的 COMMIT（毫秒时间轴）；锁忙关 B 零写入、释放后同键重试成功，与库存不足同处对照；退出关提交前 0 行 / 提交后 1 预订 1 房晚且原键找回（子进程 os._exit(73)）。
- 未实测：死锁关（127.0.0.1:5432 无监听、无 psycopg2/psycopg）——页面与报告均标「示意 / 未实测」，含等待图与预期 40P01、统一锁顺序对照；接上 PostgreSQL 后引擎自动改走实测分支。

## 第 0/1/2 批迭代（2026-11-21 第二次会话）

第 0 批（修路径）：`choice`/`order` 的 501 根因是 course_server 的引擎注册检查只认 ENGINES 字典、漏了 run() 特殊分发的两个引擎；新增 `course_engines.supports()` 统一认账。回归脚本 scripts/verify_course.py 改走页面同款 `POST /api/course/run`（不再直调内部函数），报告含 http_status。回放里的业务响应标为「业务响应（回放记录）」，与页面请求的真实 HTTP 状态分层显示（结果区 http-line）。

第 1 批（真实 PostgreSQL）：环境 = 本机 PostgreSQL 18.6 隔离集群 127.0.0.1:55433/course_lab（数据 tmp/pgcourse，本地不入库），psycopg 3.3.9。PostgreSQL 为可选依赖：按 README 安装步骤自备 binaries，连接走 COURSE_PG_DSN。连接走 COURSE_PG_DSN 环境配置；每次运行独立 run_* schema；无硬编码凭据、不删建固定表。五关：连接与建表、双连接隔离（rc/rr）、行锁（same/different/timeout，同步事件安排顺序）、死锁（真捕 40P01 + 统一顺序对照）、索引与计划（EXPLAIN ANALYZE BUFFERS JSON 计划树，只断言结果不变）。章末两个开写综合（销售/酒店）：学生写 schema.sql/service.py/tests_extra.py，子进程 harness 跑基线测试 + 新连接复核；旧填空综合关保留。

第 2 批（C++ 教学内核）：编译器 = pip ziglang 0.17.1（`python -m ziglang c++`）。教学引擎 lab/cpp/teach.cpp 五模块（page/buffer/btree/exec/locks），每步一行 JSON 事件；scripts/cpp_build.cmd 构建 bin/teach.exe。Web 只运行预编译二进制（固定 argv、20s 超时）或读取本地回放；学生锁管理器草稿用 scripts/cpp_run.cmd 本地命令行编译（-DUSE_STUDENT_LOCKS），产物 runs/cpp/<dir>/*.jsonl，页面「读取本地编译回放」按钮经 /api/course/cpp-replay 读取。明确标注教学引擎不等同于 PostgreSQL。参考与错误草稿实测对照：runs/cpp/locks-ref（有 wait）vs runs/cpp/locks-broken（恒兼容、无 wait）。

参考答案 Tab：默认折叠，三个按钮（给一点提示/解释上次失败/看完整解法）主动点击才请求 /api/course/assist。Agent 只收题干、学生代码、脱敏失败摘要；COURSE_AGENT_CMD 配置时走 Agent（20s 超时），否则/超时回退「内置参考解」并标明来源。完整解法带「已验证/未验证」标签与证据（HTTP 回归 run_id 或本地编译目录）；载入编辑器需学生确认；切换关卡不串答案（按 lesson_id 缓存）。

验收数字：scripts/verify_course.py 50/50（HTTP 200，每关一正确一典型错误；观察型关用 both_pass 标记）；原实验室 scripts/verify.py 13/13。浏览器实走：choice×2、order×3、pg-iso(rr)、死锁、计划树、开写销售、cpp-page(exact) 单步与关后重读、cpp-locks 本地回放、参考答案三按钮与回退（8023 临时服务挂慢 Agent 实测 builtin-fallback）。

复现命令：
- PG 集群（可选）：用自装 PostgreSQL 的 bin 目录执行 initdb.exe -D tmp\pgcourse -U course --auth-host=trust -E UTF8 --locale=C；pg_ctl.exe -D tmp\pgcourse -o "-p 55433 -h 127.0.0.1" -l tmp\pgcourse.log start；createdb.exe -h 127.0.0.1 -p 55433 -U course course_lab。或设 COURSE_PG_DSN 指向已有隔离实验库。
- C++：`scripts\cpp_build.cmd`；学生草稿 `scripts\cpp_run.cmd <草稿.cpp> runs\cpp\mine`。
- Agent 回退测试：`COURSE_AGENT_CMD="ping -n 30 127.0.0.1 >nul"` 启动临时服务后 POST /api/course/assist。

## 本次会话三批迭代（2026-11-22）

参考页 https://onefly.top/zero2Leetcode/05_interview/coding/huawei/ 已打开，借鉴其「知识地图 → 专题循序 → 优先级路线」编排；内容与视觉仍为本项目奶油白画板 + QDuck。

第 1 批（判题与回放）：pg-iso 空结果/错列返回结构化判错（区分「没查到」与「查错了」，失败回滚并关连接），并新增「BEGIN 不固定快照」对照观察（RR 快照由事务内第一条取快照语句建立）；pg-plan 用独立基准（题目要求的列与值）校验、行序不计、计划与缓冲区只作观察；无效 SQL 返回 sqlstate 结构化错误。请求（运行/参考答案/C++ 本地回放）绑定 lesson_id + 请求序号，过期回包丢弃；C++ 本地回放接入 timeline 可单步/重置；页面徽章标明 SQLite / 真实 PostgreSQL / 教学 C++ 与综合题实际请求路径 /api/run；综合题失败摘要传入参考答案页。

第 2 批（路线）：首页 #home 知识地图（六章 45 关、预计用时、进度、「从第一课开始 / 继续学习 / 推荐下一关」）；新增「等待 / 锁忙 / 死锁同例对照」关（同一张两行表三种命运）；销售引导 5 关（建表→单次下单→库存不足回滚→重复请求→两笔抢最后一件）与酒店引导 5 关（建表→单晚→两晚同房→第二晚冲突整单回滚→同键重试），每关只补一处；再各加一关无提示综合（pg-final-sales / pg-final-hotel，独立补齐 schema.sql + service.py，可写 tests_extra.py，判题核对业务返回与新连接最终数据）。harness 按 stage 只跑对应用例，retry 阶段自包含前置下单；酒店 booking_nights 增加 UNIQUE(room_id,night) 防同晚双占。

第 3 批（体验与验收）：结果区显示输入→预期→实际→失败原因；回放上一帧/下一帧/重置；参考答案「已验证」仅当报告里存在同答案+同场景的通过记录（附 run_id 证据），否则标未验证；Agent 未配置/超时回退内置参考解并标来源（回退不计作真实模型验证）。

验收：scripts/verify_course.py 66/66（HTTP 200；含五条原 501 路径、五条原 500 路径现结构化判错、0 行索引查询判错）；scripts/verify.py 13/13。浏览器实测：首页继续/推荐、销售引导→无提示综合（起始失败→参考答案确认载入→通过）、酒店引导→无提示综合闭环、pg-plan high 已验证 / low 未验证、慢答案返回前切关不串、C++ 导入回放单步与重置。环境：PostgreSQL 18.6 真跑、C++ ziglang 真编译、Agent 仅回退路径实测（真实模型未配置，标未实测）。

## 跳过语义（2026-11-22 补）

未配置 PostgreSQL / 未构建 C++ 时，相关关卡回执统一为「跳过：未配置 PostgreSQL／未构建 C++」，判题 passed=null，不计失败也不计通过；与「答错」（passed=false）严格分开。README 测试命令分「只跑入门（1,2,3）」与「跑全量（all）」。可用 COURSE_SKIP_PG=1 / COURSE_SKIP_CPP=1 模拟未配置，verify_course.py skip 验证跳过语义。

## 路线 C · 云后端实战（feat/cloud-backend-learning 分支，2026-11-22）

新增 18 关（c1,c2 / d1-d5 / l1-l5 / k1-k5 / f1），贯穿项目：下单/订房 → PostgreSQL 事务保存业务行+Outbox 待发送事件 → 独立 worker 进程消费 → 原始事件进本地数据湖（对象存储模拟）→ DuckDB 清洗/去重/按业务日期 Parquet → 看板查询。销售净营收（下单-退款，分/整数 cents）、酒店间夜（退房当天不计）两套口径分开；同一订单用 request_id + event_id 串联，双泳道显示实际步骤。

- 单机起点 2 关 + 启动向导（API/进程/端口/数据库各做什么）。
- 分布式 5 关：双进程、提交后丢回复同键重试、Outbox 补发、确认前退出重复事件唯一键幂等、下游不可用超时/有限重试/退避/失败记录。单机多进程不宣传为跨机器高可用；复制/分片/CAP 选修。
- 数据湖 5 关：原始 vs 业务库、坏记录列原因、Parquet 分区+改一条 DuckDB SQL、重复/迟到退款/跨午夜重跑对账、改口径从原始重建（来源/输入输出行数/坏/重复）。真实 S3/Iceberg 选修；对象存储权限与保留策略短说明见 l1 折叠区。
- K8s 5 关：k1 用本地 postgres 镜像真跑容器讲镜像/容器/端口；k2-k5 需 kind 集群，当前 Docker Hub 不可达无法拉 kindest/node → 给安装入口+可读示意，实测标「跳过」。有集群时自动走真实路径（删Pod看新UID、探针、rollout回滚）。
- 结课 f1：同一订单贯穿「超时→重试→消息重复→报表正确」。

实测：verify_course.py 7 = 6/6（d2/l4 错解结构化判错、d3/d4/k1 真跑、k3 跳过）；全量 72/72；原实验室 13/13。DuckDB 1.5.6 真跑 Parquet 分区与去重。跳过项：k2-k5（无 kind 集群）、真实 Agent（未配置）。


### 云后端练习提示

- l3 切换「全日期 / 单日筛选」后再运行。全日期应返回 12-07 和 12-08；单日只返回 12-08。画板的分区文件是目标日期对应的候选分区，不能用来推断引擎实际上扫了几个文件。
- 「提交历史」保存本浏览器的结果与输入；打开历史结果无需改写草稿。多文件综合题可逐文件对比，确认后恢复。网络或服务异常是「未完成」，缺依赖才是「跳过」。

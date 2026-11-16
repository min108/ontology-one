# R1-3 后端骨架与 R1-7a 权限键交接

基线：`1090384`，VERSION `1.0.0`。P-06 独立提交：`e2f522c`。本包未改 requirements.txt、两个受保护 Windows 工作流、既有测试断言或旧业务路由授权。

## 交付

- `backend/main.py` 只保留应用装配与生命周期。16 个原内联 API 路由及条件加载的 SPA 路由迁至 `backend/api/v1/core.py`；所有原顶层函数同名再导出。冻结 v1 完整方法/路径集合和函数 AST 摘要，比较仅去掉装饰器并还原相对导入层级。
- 原 `main.connect` 是既有调用者和测试的注入点；`api/v1/compat.py` 仅转发此工厂，保留旧调用语义。原 main 的准确 lint 规则集合只转移至旧 core 文件；新包无宽泛豁免。
- core 提供 44 个 C.5 错误码、统一错误模型、C.4 随机编号前缀、非敏感配置、结构化元数据日志格式器。v2 中间件错误与框架校验错误统一，保持 v1 原格式；请求编号由服务端生成。
- 能力注册期校验重复编号、输入输出严格模型、权限、前置检查、助手绑定、对外工具效果及限制；网关统一检查权限、确认级别、前置条件、输出模型、只读事务、大小/耗时与追加式元数据审计。
- 五个只读能力：`session.whoami`、`meta.kinds`、`org.tree`、`agent.readiness`、`semantic.search`。16 种类 ID 沿用既有 graph.KINDS 集合，中文名采用 C.1。就绪诊断明确只检查当前可知的会话及能力读取入口，`complete=false`，不冒充模型或运行环境验收。
- REST 入口：`POST /api/v2/capabilities/{id}:invoke`，正文为能力输入对象。空输入使用 `{}`；检索输入为 `{"q":"关键词"}`。所有 POST 仍需 CSRF；身份、租户、权限等额外字段均拒绝。五能力当前均需登录；业务调用还检查能力声明的权限键。
- `permissions_for` 覆盖五种角色、总部超管、总部 `group_steward`、既有实时跨机构授权以及服务端提供的场景作用域。`store.ROLES` 与 `policy.ROLES` 仍为旧三角色，新角色不能经账户管理创建，也不能访问 v1 业务接口。
- 生成器 `scripts/gen_api_types.py [--check]` 仅依赖现有 Python/FastAPI/Pydantic，输出 `frontend/src/api/generated/openapi.json`、`types.ts`，不需要 Node 或新依赖。只导出 v2。输入/输出 Union 来自启动期注册表，后续能力无需手改路由 Union；新增能力模块必须在路由构造前注册。现有 CI 的 unittest discovery 自动运行生成物逐字节一致性检查。

## 安全与自行裁决

1. 普通会话检索转发既有 `core.search`；无 `draft.read` 时转发同一 `core.entities` 并在 SQL 层限定 `status=published`，分页总数也只统计已发布项。真正的只读工作区不得直读原始机构表：该分支转发既有 federation 检索，沿用实时授权、共享可见性和授权复核；不完整结果当前返回运行环境不可用，避免隐瞒部分失败。
2. 只读 Principal 可调用 read/none/external_read 能力，拒绝写能力，输出权限不含写入与草稿读取键。POST 读取仍进入业务库只读事务；审计仅写控制库，记录能力编号、结果码、数量、耗时，不记录输入、检索条件或返回正文。
3. C.5 文案是示例，不当作已发生事实：默认文案移除虚构的人名、数量、已保存内容及已返回部分结果；权限不足不一律称审核权限。四类强制下一步均指向已存在的工作台 `/`，权限文案提示联系本机构管理员，避免把无管理权限者送至管理页。
4. 未登记 v2 路由仍返回 403；已登记通用入口的未知能力返回 404。v1 代理 catch-all 仍默认拒绝未知具体路径；策略覆盖测试对这两个既有代理模板单列，并检查所有具体业务路由。
5. 写能力的持久幂等基础设施尚未实施，注册器拒绝把写能力声称为 `idempotent=True`。当前只读幂等声明无业务写入效果；R4 引入持久幂等实现时扩展。
6. 能力 `timeout_s` 当前检查同步调用完成后的耗时，不声称能抢占中断同步业务函数；真实查询仍由已有数据库/联邦层限制保护。未来长任务须使用对应执行器的超时与取消机制。
7. 只有两项自动生成物精确加入 Prettier 忽略；生成器逐字节校验负责其格式，不豁免 TypeScript strict 编译或 ESLint，不扩大手写代码豁免。

## 后续衔接

- **R1-7（control 0003）**：本包给 Principal 增加默认空 `duties` 元组，并在 `_principal` / `_joined_principal` 解析可选 duties。当前 `_session_row` 投影仍不依赖不存在的列；新增列迁移合入后必须补 `u.duties` 投影与真实会话职责测试。R1-7 同时扩展机构树父子关系和停用状态。
- **R1-2**：迁移生命周期在本包之后合并，由 root 解决 main 装配冲突，保留 core/v2 注册及同名再导出。
- **场景/受限属性**：`PermissionScope` 仅供服务端已授权数据构造；未来场景服务必须从成员表与机构设置构造，不得从请求正文构造或直接信任成员角色。R1 无场景写入口。
- **R3 MCP**：当前五能力均未对外发布（`mcp=False`）；semantic.search 仅声明 builder/analyst 内部工具可用。外部应用身份接入及 MCP v2 发布留给 R3，不绕过现有 MCP 应用身份。
- **R5**：完整就绪诊断替换占位；应用助手仍须通过同一能力网关。

## 验证

- 本包新增 `tests/{api,core,security,capabilities}` 可收集测试 78 个，全部通过（含角色权限逐格 subTest、真实 SQLite 检索转发、只读可见性、CSRF、统一错误、注册约束、AST/路由集合、生成一致性）。
- 真实 MySQL/HTTP `scripts/check_security.py` 通过，新增 v2 跨机构检索、伪造机构请求头、伪造操作人/身份/权限三类攻击及能力审计元数据检查。
- 全仓 `ruff format --check`、`ruff check` 通过；16 个流水线脚本测试通过；生成器 `--check` 通过；生成类型 `tsc --strict --noEmit --skipLibCheck`、前端 ESLint、Prettier 检查通过。
- 首次完整后端运行因只授权 tmpfs 子目录导致沙箱挂载视图失真：17 个官方 SDK 测试报 HARNESS_UNAVAILABLE，39 个 Windows 原生项正常跳过。保持代码检查不变，改为授予整个 `/dev/shm` 写权限，同一执行环境创建独立 `/dev/shm/ontology-r1-3` 并验证 `_work_root()` 后重跑。
- 完整后端重跑：570 个测试，531 通过、39 个 Windows 原生项按原集合跳过，耗时 142.332 秒。相对 R1-1 起点新增 78 个；相对最初 489 基线新增 81 个（其中 R1-1 已有 3 个）。日志在本 worktree `work/package-tests.log`、`work/full-tmpfs.log`、`work/security.log`、`work/pipeline.log`；这些运行文件不入库。

## 集成复验修复：跨进程身份职责的不可变表示

root 的完整脚本复验发现 `check_agent_runtime.py` 中来源工具调用失败。新增 `duties` 是元组，但 `asdict → JSON → Principal(**payload)` 会把它恢复成列表。业务会话复核重建的是元组，两份相同身份在 `_SessionPolicy` 全对象比较处被拒绝；只读执行上下文的身份比较也受影响。

修复在冻结 `Principal.__post_init__` 统一把合法字符串列表/元组转换为元组，同时拒绝字符串、对象、非字符串元素等错误形状。没有更改会话、租户、权限、撤权或工具范围比较，没有缩小既有断言。全库复查的三个 JSON 恢复入口为 `agent_tool_worker.py`、`mcp_worker.py`、`jobs._invoke`，均通过此构造入口归一；旧任务没有 duties 字段时继续默认空元组。

8 个新增边界测试先失败后通过，覆盖空/非空职责往返、工具身份比较、真实身份变化仍拒绝、只读执行上下文、外部列表不可变、旧帧兼容及错误形状拒绝。49 项定向测试与全仓 ruff 检查通过。

首次补丁全量验证误与同包的真实脚本共用 tmpfs 根并行：完整后端的 3 个失败均为 teardown 观察到另一测试进程仍在使用的临时目录；真实脚本 18 项中 17 项通过，held MCP 超时测试在高负载并跑下出现 TimeoutError。原始日志保留，断言和时限未修改。后续须按 root 测试租约串行运行，并为每条命令分配独立真实 tmpfs 根。

最终按独占 SDK 测试租约串行复验通过：`check_agent_runtime.py` 18/18 全部通过（包括原 held MCP 超时/取消、双源、撤权三个失败点）；随后完整后端 578 项，539 通过、原 39 Windows 原生项跳过，62.772 秒。两条命令分别使用 `/dev/shm/ontology-r13-principal-runtime` 与 `/dev/shm/ontology-r13-principal-full`，均授权真实 `/dev/shm` 根而非仅子目录。日志分别为 `work/principal-runtime-serial.log`、`work/principal-full-serial.log`。本次额外新增 8 项后，本包总新增测试为 86 项。

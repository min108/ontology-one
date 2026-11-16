# R1 执行计划：地基与新骨架（1.0.0）

> **状态：已确认（负责人，2026-11-16）。** 执行期间由实施方在第 11 节续写进展。
> **分工**：开场规划与独立验收 — Claude；具体开发 — GPT-6 Astra；裁决 — 负责人。
> **基线**：`qa/publish-accepted-0.7.0` @ `1359118`（版本 0.7.0）。**目标**：`dev/r1-foundation`，版本 1.0.0。
> **依据**：《知元（Ontology One）蓝图性优化建设方案》（下称“蓝图”，原文 SHA-256 `1587272bc4273d3b9dfab1ce4e9696ae65bce61c0d12db0af84991ed85792075`）§14.1、§14.3。本计划只写“本轮怎么排、落到哪些文件、怎么验”，规范正文不重复，按章节号引用。
> **编制日期**：2026-11-16。文中 `文件:行号` 一律指基线提交 `1359118`；后端一次性格式化之后行号会变，届时按函数名定位。

---

## 0 摘要

1. **基线全绿，可以开工。** 后端 489 个可收集用例全部通过或按平台跳过，8 个回归脚本、16 个流水线脚本测试、前端构建全部通过（§1.2）。
2. **测试基数更正。** 蓝图写的“353 个后端测试函数”已过时。实测：`tests/` 下以 `test` 开头的函数 **490 个**，其中 1 个是嵌套辅助函数，`unittest` 实际收集 **489 个**；`tests/test_windows_*.py` 占 **111 个**。验收门 1、9 一律以此为准（§1.3）。
3. **最高风险的机械步骤已试跑通过。** “后端全量格式化一次”在草稿副本上执行：151 个文件改动 150 个，语法树等价，489 个用例结果逐条一致，8 个回归脚本通过（§1.5）。
4. **执行顺序**：R1-1（含格式化，串行）→ R1-2 ‖ R1-3 ‖ R1-4 ‖ R1-10（并行）→ R1-5 → R1-6 ‖ R1-7 ‖ R1-8（并行）→ R1-9 收口与收尾（§4）。
5. **开场发现 26 项**（§3），其中 8 项经负责人确认：P-02、P-03、P-08、P-09、P-11、P-12、P-13、P-20。最要紧的一条是 P-09：**打标签 `v1.0.0` 会自动发布一个公开的 Windows 版本并创建 `release/1.0.0` 分支**，因此标签只由负责人在独立验收通过后决定。
6. **交接只走 GitHub 仓库。** GPT-6 Astra 接触不到规划方的云端环境，它需要的一切（本计划、蓝图、基线清单与复现脚本）都要在仓库里；结果也经仓库与 GitHub Actions 交回（P-26）。**仓库是公开的**，负责人已同意蓝图公开入库（P-13）。
7. **两处基线里的隐患**，本轮顺带处理：版本号改成 1.0.0 会让 Windows 升级器拒绝工作（P-24）；加载过演示资料的机构导出的备份无法恢复（P-25）。

---

## 1 开场动作记录

### 1.1 已读材料

- 蓝图 0.2、0.3、§14.1、§14.2、§14.3（任务书）、§15.2、§15.3（D1、D2）。
- “本轮必读”：§3.1–§3.2、§3.4–§3.7（另读 §3.8–§3.10）、§5.1、§6.2（另读 §6.8、§6.10 开头）、§9.1–§9.2、§9.4–§9.5（另读 §9.3、§9.7）、§10.1、§10.3–§10.5（另读 §10.2、§10.9、§10.11）、§13.3–§13.4、§13.6、§13.8（另读 §13.1、§13.5）、附录 A、B（R1 行与 B.3）、C、F、H（全表）。
- 第一部分通读；第二部分读 §2.5。
- `AGENTS.md`、`STATUS.md`、上一轮报告：基线中不存在，由 R1-1 创建。
- 代码：按域勘察了安全底座、初始化与建表路径、路由与策略、前端外壳与本轮要重建的五个页面（清单见附录 B）。

### 1.2 基线实测（提交 `1359118`，Python 3.12.3，MySQL 8.4.13，Node 22.23.4）

| 检查 | 命令（在仓库根目录） | 结果 |
|---|---|---|
| 后端测试，默认 | `python -m unittest discover -s tests` | 489 个：430 通过，59 跳过 |
| 后端测试，开 MySQL 集成 | 加 `RUN_FEDERATION_MYSQL_TESTS=1 RUN_JOBS_MYSQL_TESTS=1` | 489 个：433 通过，56 跳过 |
| 后端测试，再开真实 Harness SDK | 再加 `RUN_HARNESS_SDK_TESTS=1` | 489 个：**450 通过，39 跳过**，约 55 秒 |
| 流水线脚本测试 | `python -m unittest discover -s .github/scripts -p 'test_*.py'` | 16 个全部通过 |
| 回归脚本（8 个） | `scripts/check_restore_v020.py`、`check_security.py`、`check_workflows.py`、`check_sources.py`、`check_mcp.py`、`check_skills.py`、`check_entity_storage.py`、`check_agent_runtime.py` | 全部退出码 0，合计约 4 分钟 |
| 前端 | `npm --prefix frontend ci && npm --prefix frontend run build` | 通过（`tsc -b && vite build`，1831 个模块） |
| 前端单元测试、端到端、代码检查 | — | 0.7.0 中不存在，本轮新建 |
| 模拟评测 | — | 0.7.0 没有 `evals/`；等价物是 `check_agent_runtime.py`（合成模型应答），已通过 |
| 真实模型脚本 | `check_harness_live.py`、`check_controlled_live.py` | **未执行**：属可选项，且本环境出网策略拒绝 `api.deepseek.com`（P-20） |

剩余的 39 个跳过项全部是只能在 Windows 原生环境运行的用例（`test_windows_sandbox_native` 21、`test_platform_io` 13、`test_skill_harness` 4、`test_windows_upgrade` 1），与 Linux 上的代码改动无关。按模块的明细见附录 A。

`check_skills.py` 需要先构建固定的技能测试镜像：`docker build -f ops/skill-runner.Dockerfile -t ontology-skill-test:0.5.0 .`。

### 1.3 测试函数清点口径

| 口径 | 数量 | 说明 |
|---|---|---|
| `tests/` 下名字以 `test` 开头的函数（语法树统计） | **490** | 负责人给出的数字，已复现 |
| 其中 `unittest` 可收集的用例 | **489** | 差的 1 个是 `tests/test_jobs_mysql.py:62` 的嵌套辅助函数 `async def test()`，不是用例 |
| 其中 `tests/test_windows_*.py` | **111** | Linux 上 89 通过、22 跳过 |
| `.github/scripts/test_*.py`（不在 `tests/` 内） | 16 | 单独统计 |

**本轮统一口径**：既有用例 = `tests/` 489 个 + `.github/scripts/` 16 个。验收门 9 的“新增后端测试不少于 80 个”，按“`tests/` 下可收集用例数减去 489”计算（改写的既有用例不算新增）。489 个用例的逐条结果清单已入库：`docs/blueprint/rounds/r1-baseline/baseline-tests.txt`（用例标识排序后的 SHA-256 为 `e8d97c89…d4a0fb4a`）。

### 1.4 环境实测与注意事项

以下是规划方云端环境的实测。实施方在自己的环境里开工前，必须用 `docs/blueprint/rounds/r1-baseline/run_baseline.sh` 重跑一遍基线，并把逐条结果与同目录的 `baseline-tests.txt` 比对；不一致先报告，再开工。

- **Docker**：29.9.2 已安装，但守护进程不会自动启动，需手动 `dockerd`。**MySQL**：用镜像 `mysql:8.4`（实测 8.4.13），与 `docker-compose.yml` 的启动参数一致。**Node** 22.23.4、**npm** 10.9.9。**Playwright** 与 Chromium 已预装，不要重装。
- **Python**：系统默认是 3.13，项目定版 3.12（`/usr/bin/python3.12`）。一律用 3.12 建虚拟环境。
- **一次完整回归约 6 分钟**（后端测试 1 分钟、回归脚本 4 分钟、前端构建 20 秒），4 核机器。
- **出网受限**：包管理源可用；`api.deepseek.com` 被拒绝（P-20）。容器内的进程不能直接使用宿主的代理，构建镜像时要另行传入代理与证书。
- **仓库默认分支 `main` 停在 0.1.0-demo（`3a51317`）**，不是基线。本次开场中，子代理的 worktree 默认从 `origin/main` 建出，读到了错误的代码（P-14）。
- 子代理规则（负责人要求）：不以最高推理档派发；并行不超过 5 个；各用独立 worktree。

### 1.5 一次性格式化试跑（R1-1 的可行性验证，未提交）

在基线的草稿副本上执行 `ruff format --line-length 120 backend scripts ops tests .github/scripts`（ruff 0.16.9）：

| 项 | 结果 |
|---|---|
| 文件 | 151 个 `.py`，改动 150 个（+39,348 / −18,489 行） |
| 语法树等价 | 150 个文件严格相等；1 个文件仅文档字符串的空白被规整；0 个文件有其他差异 |
| 后端测试（三个开关全开） | 489 个：450 通过、39 跳过，与基线逐条一致 |
| 8 个回归脚本、16 个流水线脚本测试 | 全部通过 |
| 压在一行的多条语句 | 格式化后为 0 |
| 仍超过 120 列的行 | 299 行（长字符串与 SQL，格式化器不拆），需在检查规则中豁免 |
| Windows 冒烟脚本对源码做的字节替换 | `tests/windows_portable_smoke.py` 用来注入故障的那一行（`ops/windows_initialize.py` 中的 `main(progress=ProgressWriter())`）格式化后字节不变，仍能命中 |

试跑用的是 ruff 默认风格（字符串统一为双引号）。结论：这一步可以安全地做，前提是**只运行 `ruff format`，不运行任何自动修复**（见 R1-1 易错点）。

---

## 2 本轮涉及的 0.7.0 功能（对照附录 H）

### 2.1 必须逐项验证的清单

| 编号 | 0.7.0 功能（附录 H） | 处置 | R1 的去向 | 工作包 | 验证方式 |
|---|---|---|---|---|---|
| H-01 | 服务端会话、CSRF、来源白名单、会话列表与撤销、登录限速、账户停用、角色变更即时生效、追加式审计 | 保留（全程） | 机制不动；重建的登录页与账号安全页继续使用 | R1-7 | `check_security.py` 全过；新端到端用例（会话撤销、限速提示）；审计触发器测试 |
| H-02 | 首次登录强制修改密码 | **取消**（H.2-1） | “建议修改初始密码”的提示点（§9.5） | R1-7 | 验收门 5 |
| H-03 | 51 家机构写死、ID 按序号生成 | 增强（H.2-3） | 可治理的机构目录，存量 ID 冻结（§9.1） | R1-7 | 验收门 6 |
| H-04 | 三种角色 | 增强 | 五种角色加治理职责；R1 只定义权限键，不开放新角色账户（§9.2） | R1-3、R1-7 | 验收门 7 |
| H-05 | 集团授权及其管理界面；总部超管切换工作区 | 保留 | “账号与权限 › 跨机构授权”；身份条中的机构切换 | R1-5、R1-7 | 新旧接口结果比对；端到端 |
| H-06 | 账号开通、编辑角色、重置密码、停用启用、管理原因 | 保留 | 管理 › 账号与权限 | R1-7 | 附录 B 的 ADM 清单逐条对照 |
| H-07 | 审计查询（“身份审计”页签） | 保留 | 管理 › 审计（基础版：筛选、分页、记录详情） | R1-7 | 附录 B 的 ADM-10；与 0.7.0 结果比对 |
| H-08 | 统一智能服务配置：每机构一份、密钥不回显不进备份、连接验证 | 增强（R1、R5） | 管理 › 模型与运行，R1 承接现有配置与验证 | R1-8 | 附录 B 的 SET-01；密钥不回显、不进备份的测试 |
| H-09 | 程序源码包导出、发布信息、健康检查 | 保留 | 管理 › 备份与升级、系统信息 | R1-8 | 附录 B 的 SET-06、SET-10 |
| H-10 | 机构备份导出与恢复（含恢复后的安全处理） | 保留（全程） | 行为不变，增加表分类清单（§3.6） | R1-2、R1-8 | `check_restore_v020.py`；分类清单测试；导出内容与 0.7.0 一致 |
| H-11 | 维护锁、迁移、Windows 便携包启停与升级、Compose 部署 | 保留（全程） | 统一到新的迁移入口 | R1-2 | 验收门 2；111 个 Windows 相关用例不被破坏 |
| H-12 | 安全、业务、数据源、MCP、技能、存储等验收脚本与压测 | 保留（全程） | 全部保持通过，随新接口扩充 | R1-9 | 8 个脚本重跑 |
| H-13 | 原文版本、精确锚点、历史引用持续有效 | 保留（全程） | 不触碰 | R1-2 | 迁移测试断言相关表逐行哈希不变 |
| H-14 | 尚未重建的 25 条旧路由对应的全部页面 | 保留至各自轮次 | 在新外壳中经新导航或原地址可达、可用 | R1-5 | 冒烟用例逐路由覆盖（验收门 3） |
| H-15 | 全局智能浮层、后台任务浮层 | R6 取代（H.2-6） | 作为 legacy 组件继续挂载 | R1-5 | 发起一次旧的智能辅助，浮层出现并可停止 |
| H-16 | 首页“总览驾驶舱”的口号横幅与装饰图 | 取消（H.2-4） | 工作台；承接的旧页面去掉口号横幅 | R1-5、R1-6 | 截图 |
| H-17 | 19 个平铺的侧栏入口；常驻的“集团成果”菜单 | 取消（H.2-5） | 六个工作区；“集团成果”仅有集团授权者可见 | R1-5 | 一级导航为 6 项；各角色导航快照 |
| H-18 | 旧演示面板（加载模拟资料、验证路线、样例下载、准备映射、归档） | R3、R9 取代 | 管理区内以 legacy 承接，功能不变 | R1-8 | 附录 B 的 SET-03 |
| H-19 | 集成应用（应用身份与令牌管理） | R9 重建 | 旧页面在“管理”下可达 | R1-8 | 冒烟；`check_mcp.py` |
| H-20 | 业务汇报导览（静态页） | R9 取代 | 从工作台的入口卡进入 | R1-6 | 冒烟 |
| H-21 | 平台级 MCP 服务：五个工具、应用身份 | 增强（R3 起） | 本轮不改；服务标识不随产品改名 | — | `check_mcp.py`；标识比对 |
| H-22 | 全局搜索 | 增强（R2） | 万能入口 v1 沿用 0.7.0 检索 | R1-3、R1-6 | 搜索结果与 0.7.0 一致 |
| H-23 | 我的工作台（任务清单） | 并入（R4） | 旧页面保留可达；新工作台读取同一数据 | R1-6 | 数字与旧页面一致 |

### 2.2 过渡期的导航归属（蓝图 A.1）

| 工作区 | R1 新建的页面 | R1 承接的旧页面（地址不变） |
|---|---|---|
| 工作台 `/` | 工作台 | 入口卡：业务汇报导览 `/briefing` |
| 建设 | — | 素材整理 `/materials`、数据台账导入 `/datasets`、数据源管理 `/data-sources`、经验萃取室 `/experience`、技能开发 `/skill-development`、专题工作室 `/projects`、我的工作台 `/work` |
| 本体 | — | 业务资产 `/assets`、权责规范 `/authority`、本体图谱 `/graph`、集团成果 `/group`（仅有集团授权者显示入口） |
| 应用 | — | 本体应用 `/applications` |
| 治理 | — | 审核维护 `/maintenance`、变更复核 `/impact` |
| 管理 | 机构目录 `/admin/orgs`、账号与权限 `/admin/users`、模型与运行 `/admin/model`、备份与升级 `/admin/backup`、系统信息 `/admin/system`、审计 `/admin/audit` | 集成应用 `/integration-applications`；旧演示面板嵌入 `/admin/demo` |
| 用户菜单 | 账号安全 `/account/security` | — |

详情类路由（`/materials/:id`、`/assets/:id`、`/projects/:id`、`/tasks/:id`、`/authority/:id`、`/datasets/:id`、`/data-sources/:sourceId`、`/skill-development/:id`、`/knowledge/collections/:id`）保持原地址。尚未建成的页面不出现入口（§14.1“入口出现的时机”）。

### 2.3 本轮重建并下线的旧页面

登录、外壳、机构总览、系统设置、账号安全、账号与权限（§14.2）。每个页面删除前按附录 A.2 的七条逐条确认，对照基准是本计划附录 B 的操作清单，结果写入报告第 8 节。

本轮新增的承接块（附录 A.4）：工作台数据取自原接口；全局智能浮层与后台任务浮层；集成应用与旧演示面板。

---

## 3 开场发现与裁决

共 26 项。两张表共用一套编号（按发现的先后），后文按编号引用。

### 3.1 负责人已确认的 8 项

负责人于 2026-11-16 确认：P-13 选“备选一：接受公开，直接入库”；其余 7 项均按“决定”执行。“备选”一行保留作记录。

**P-02　第 5 个打样能力用哪个编号**

- 事项：R1-3 要求用 5 个只读能力打样，第 5 个是“搜索转发”。但附录 D 和本轮登记项只列了 4 个只读能力，检索能力 `semantic.search` 排在 R2。
- 决定：就用 `semantic.search`。R1 只做“转发 0.7.0 的检索；没有草稿权限的人只得到已发布内容”，契约版本低于正式版，R2 换实现并扩展入参。附录 D 是能力编号的唯一定义处，编号发布后不得改名；另起临时编号会在 R2 留下两个同义的能力。
- 备选：不提前登记，搜索转发做成普通的 v2 接口。这样本轮只读能力只有 4 个，与任务书的“5 个”不符。

**P-03　过渡期的导航按谁的规则显示**

- 事项：§10.1 的按角色可见性是终态——审核官看不到“建设”，非管理员看不到“管理”。但在 R4 的审核中心建成之前，审核官要进技能开发、数据源管理、台账详情做审核；0.7.0 的“系统设置”也对所有角色可见（模型状态只读、演示样本下载）。
- 决定：过渡期旧页面入口沿用 0.7.0 的可见性，不收紧；§10.1 的规则只管新建页面。即：三种既有角色都看得到六个工作区；`/admin/orgs`、`/admin/audit` 仅总部超管；`/admin/users`、集成应用仅管理员；`/admin/model`、`/admin/system`、`/admin/demo` 对非管理员只读可见。“集团成果”入口按蓝图 A.1 收紧为仅有集团授权者显示，地址仍可直达。“0.7.0 功能不断档”属于底线，优先于体验层的终态规则。
- 备选：严格按 §10.1 执行。代价是审核官在 R4 之前只能靠直接输入地址进入这些审核页面。

**P-08　`backup.restore` 怎么登记**

- 事项：登记项要求本轮登记 `backup.restore`，但 0.7.0 没有在线恢复——恢复只能离线执行，且必须持有独占维护锁。
- 决定：登记该能力，复用现有的离线恢复逻辑，前置条件为“持有独占维护锁”。在线调用一律返回“前置条件不满足”并给出操作指引；命令行工具改为调用同一个函数。不新增在线恢复路径。
- 备选：本轮只登记 `backup.export`，`backup.restore` 留待以后，报告里注明缺少的原因。

**P-09　标签即发布**

- 事项：任务书要求轮末打标签 `v1.0.0`。现有的 Windows 流水线在 `v*` 标签上触发；原生验收通过后，它会**自动创建 `release/1.0.0` 分支并发布公开的 GitHub Release**（含 Windows 安装包）。它的升级基线目前写死为 0.6.0 的发行包。
- 决定：标签由负责人在独立验收通过后决定何时打，实施方和验收方都不打。打之前先让这条流水线以不发布的方式跑一次（它在拉取请求上也会触发），结果记入报告。按决策 D2，这次结果不作为 R1 的验收门。
- 备选：R1–R3 的轮次标记改用不以 `v` 开头的标签（不触发发布），只在做原生回归的 R4、R7、R10 打 `v` 标签。

**P-11　验收证据包怎么交付**

- 事项：证据包规定放在 `work/evidence/r1/`，而 `work/` 被 git 忽略，随分支带不走。
- 决定：证据包不入库，由 `ci.yml` 在 GitHub Actions 上生成并作为流水线构件上传，报告里写明运行编号、构件名与校验和；视觉基线按蓝图入库。GitHub Actions 是实施方与我都能看到的中立环境，我可以直接读运行结果与日志。验收仍以我独立重跑为准。
- 备选：证据包放进仓库的 `docs/` 下。截图会让仓库明显变大。

**P-12　分支怎么接力**

- 事项：蓝图要求从基线切出 `dev/r1-foundation`。本会话只被允许向 `claude/gpt6-astra-project-planning-uf1ggp` 推送。
- 决定：本计划提交在这条规划分支上（等于基线加文档）；GPT-6 Astra 从它的顶端切出 `dev/r1-foundation`；验收时规划分支快进到开发分支末端，再追加验收记录和下一轮计划。两条分支交替前进，与“从基线切出”实质等价。
- 备选：授权本会话直接创建并推送 `dev/r1-foundation`，计划与验收记录都放在开发分支上。

**P-13　蓝图原文怎么交给实施方（仓库是公开的）**

- 事项：实施方只能读仓库，R1-1 也要求把蓝图放到 `docs/blueprint/BLUEPRINT.md`。`min108/ontology-one` 是公开仓库，蓝图入库即对所有人可见。
- 决定：接受公开，直接入库。蓝图原文已随本计划提交到 `docs/blueprint/BLUEPRINT.md`，与页首校验和字节一致。
- 备选（未采用）：先改为私有再入库；或蓝图不入库、由负责人直接交给实施方。

**P-20　真实模型的验证由谁做**

- 事项：规划方的云端环境出网策略拒绝 `api.deepseek.com`。实施方在自己的环境里运行，能否访问取决于那边的配置。
- 决定：Harness 探针（R1-10）中需要真实模型的项，由实施方在它的环境里用 `DEEPSEEK_API_KEY` 环境变量执行，报告只写结论与用量，不写模型原文与密钥。我验收时需要独立复核的话，再申请放行该主机并把密钥存为环境密钥；否则这些项保留在“待负责人执行”清单里。密钥曾出现在对话中，建议在交给实施方之前更换一把新的。
- 备选：真实模型项全部列为“待负责人执行”，实施方与我都不做。

### 3.2 告知（18 项）

按所列做法执行，并记入报告的自行裁决清单。负责人有异议可随时调整。P-01 已由负责人指示。

| 编号 | 事项 | 做法 | 理由 |
|---|---|---|---|
| P-01 | 测试基数：蓝图 353，实测 489（490） | 以代码为准，口径见 §1.3 | 负责人已指示 |
| P-04 | 顶栏规格里有些入口本轮没有数据来源或后端 | 本轮不显示：通知、帮助、“＋ 新建”、助手停靠栏、查询范围切换器（身份条中固定显示“范围：本机构”）。`NotificationCenter`、`TaskTray` 两个组件只进画廊并配单测；顶栏的任务位置在 R5 之前由旧的后台任务浮层承接 | §14.1：尚未建成的不放入口；不出现点不动的入口 |
| P-05 | 能力契约测试要用 `permissions_for`，但 `permissions.py` 排在依赖 R1-5 的 R1-7 里 | 把 `permissions.py` 及其单测前置为 R1-7a，与 R1-3 同批完成 | 理顺依赖，不改变范围 |
| P-06 | “`main.py` 只做装配”与“旧模块不搬迁”并存；`main.py` 里的业务函数要到 R2、R4 才下沉到内核 | 把 `main.py` 内联的 17 条路由及其函数原样移到 `backend/api/v1/core.py`，单独提交、语法树等价；`main.py` 保留装配并转发旧名字 | 最小改动满足 §3.5 第 1 条 |
| P-07 | 升级前全量备份与失败处理，蓝图未细化到实现；而基线有三个相关事实：维护锁是进程级文件锁，进程一退出就释放；Windows 每次启动都会执行一遍迁移入口；0.7.0 靠服务启动时建控制库、登录时惰性建机构库 | ① 备份：机构库按 0.7.0 机构备份格式（含原件），控制库单独做逻辑转储，整包带清单与校验和，保留最近 3 份；新增整体恢复脚本。② **只在“确有待执行的迁移、且不是全新安装”时才备份**，空跑不备份。③ “失败后维护锁保持”用持久的升级状态标记实现，服务启动时读到“未完成”即拒绝对外服务。④ 服务启动只校验迁移版本，不再改结构；只有全新的空机构库允许自动开通。写入 ADR-0002 | §13.8、§3.8；更保守 |
| P-10 | 两个 Windows 冒烟脚本含强制改密断言（`tests/windows_portable_smoke.py:301`），只能在 Windows 原生流水线上验证 | R1-7 按新规则改写；验证依赖 P-09 的流水线预演，列入“待负责人执行” | 本环境无法原生运行 |
| P-14 | 默认分支 `main` 是 0.1.0-demo | 建 worktree 时显式指定起点为 `dev/r1-foundation` 的当前提交；子代理开工先核对 `VERSION` | 本次开场已踩到 |
| P-15 | 任务书说工作台的“最近访问”先取自原接口，但 0.7.0 没有这个接口（只有浏览器本地记录，且只写不读） | R1 的“继续上次的工作”用新外壳自己的本地记录；R4 改为服务端 `recent_visits` | 不为过渡期新建数据表 |
| P-16 | 旧“机构总览”有领域覆盖、近期成果、重点专题、工作动态四个列表块，新工作台规格里没有 | 工作台不复刻这四个列表；四个指标卡可点击进入对应页面；每个旧区块的去向写入报告的对照表 | §10.2 首屏只放必需信息；A.2 要求逐项有去向 |
| P-17 | 旧“系统设置”里有“查看平台发行包”的公网链接，与零公网依赖的检查冲突 | 保留该链接，登记为 `check_offline.py` 的白名单项（只是用户点击的超链接，平台不主动访问），在 `STATUS.md` 的已知限制里注明内网不可达 | 附录 H.2 之外的功能不得取消 |
| P-18 | 0.7.0 后端允许机构管理员查本机构审计，但界面只对总部超管开放 | 新页面与 `audit.query` 仅总部超管可用（§9.2）；原接口行为不动 | 与蓝图一致，不放宽 |
| P-19 | 旧代码中 `store.ROLES` 同时驱动会话载荷里的角色清单与账号管理页的下拉；它与 `policy.ROLES` 是两份独立常量 | 新角色只进权限键层，不加入这两份常量；创建与修改账号的接口在 R1 拒绝新角色 | 验收门 7；R2 才开放 |
| P-21 | “建议修改初始密码”的提示点需要一个判定信号，但 `must_change_password` 要恒为 0，附录 B 也没有登记新列 | 不新增未登记的列；由审计记录或用户偏好派生（实施方选定并登记） | 更保守 |
| P-22 | 旧演示面板在管理区的名称：“演示空间”是 R3 才有的新概念 | 入口暂名“演示资料”，R3 起由“演示空间”取代 | 避免名实不符 |
| P-23 | 现有字体文件只有 400、700 两个字重，令牌规定标题字重 600 | 由 R1-4 决定用 700 代替还是补充字重文件（离线清单同步），写入 ADR-0001 | — |
| P-24 | Windows 升级器把版本写死：`ops/windows_upgrade.py` 中 `TO_VERSION='0.7.0'`、`FROM_VERSIONS=('0.5.0','0.6.0')`，且要求当前版本等于 `TO_VERSION`。版本号一改成 1.0.0，升级器就拒绝工作，相关单测也会失败 | 改版本号的同一个提交里把 `TO_VERSION` 改为 `1.0.0`，`FROM_VERSIONS` 增加 `0.7.0` 并保留 `0.5.0`、`0.6.0`；同步启动器里的提示文字与相关测试，补上“0.7.0 → 1.0.0”的用例。每轮改版本号时都要同步这两个常量，写入 `AGENTS.md` | 不削减既有的升级来源；流水线现有的 0.6.0 基线预演仍然有效；正式的 0.7.0 → 当前版本原生演练按 D2 在 R4 做 |
| P-25 | 0.7.0 的一个既有缺陷（已在代码层确认，未做端到端复现）：备份导出的是机构库的**全部**表，而恢复只接受由建表语句派生的 34 张表，遇到名单外的表整包拒绝。`demo_installations` 是加载演示资料时才在机构库里建的表，不在名单内——所以**加载过演示资料的机构，导出的备份无法恢复**。本轮给每个机构库新增的 `schema_migrations` 会触发同样的问题 | 在 R1-2 的表分类清单里一并解决：`schema_migrations` 归为不导出，恢复时的“空工作区”判断容许它存在；`demo_installations` 归为导出并加入恢复白名单；补“加载过演示资料的机构可以备份并恢复”的测试 | 机构备份恢复是必须保护的资产（G1）；属于修复，不是新功能 |
| P-26 | 实施方 GPT-6 Astra 接触不到规划方的云端环境（容器、数据库、草稿目录、上传文件都只在这边，且会话结束即回收） | 双方只通过 GitHub 仓库交接：① 本计划、蓝图（P-13）、基线逐条结果与复现脚本（`docs/blueprint/rounds/r1-baseline/`）随成稿提交；② 实施方推送 `dev/r1-foundation`，进展写在本计划第 11 节，报告与交接材料按 §14.1 入库；③ `ci.yml` 跑在 GitHub Actions 上，作为双方共同认可的检查结果；④ 我验收时按分支名拉取，在自己的环境独立重跑。负责人需另行给实施方开通仓库写权限，并建议给 `qa/publish-accepted-0.7.0` 与 `v*` 标签加保护规则 | 计划里不得出现只在一方环境才有的路径或文件 |

---

## 4 执行顺序与并行安排

```
批次 0（串行，主干）  R1-1：分支、交接骨架、工具链、【后端格式化单独提交】、ci.yml 初版、版本号
批次 1（并行 ≤ 5）    R1-2 迁移框架 ‖ R1-3 后端骨架（含 R1-7a 权限键）‖ R1-4 设计体系 ‖ R1-10 Harness 探针
                      ‖ R1-9a：旧页面冒烟先在 0.7.0 界面上跑通，作为回归基准（用 R1-2 先交付的 2.f 夹具）
批次 2（串行）        R1-5 新外壳与信息架构
批次 3（并行 ≤ 3）    R1-6 工作台与万能入口 ‖ R1-7 身份与机构 ‖ R1-8 管理区
批次 4（串行）        R1-9 收口：端到端、视觉基线、检查脚本进 CI、离线构建检查、证据包；六项收尾
```

**规则**

1. 格式化提交必须在任何并行分支切出之前进入主干，否则所有分支都会冲突。
2. 每个并行工作包一个分支 `wp/r1-<编号>-<主题>`、一个独立 worktree，起点显式指定（P-14）。并行子代理不超过 5 个，不用最高推理档。
3. 合并前先变基到主干最新提交，在主干上跑通全量回归（§1.2 的全部项，加上已建成的新检查）再合并。提交说明以 `[R1-<编号>]` 开头。
4. 每个工作包先写契约与测试，再实现（§14.1）。涉及界面的工作包按 §13.5 的启发式清单与 §10.9 的文案规则自查，并出截图。
5. 交给子代理的材料：本计划中该工作包的小节、任务书对应行、相关规范章节与验收门、0.3 硬规则。不整份转交蓝图。

**容易冲突的文件与归属**

| 文件 | 会改它的工作包 | 约定 |
|---|---|---|
| `backend/main.py` | R1-2（启动校验）、R1-3（变薄） | R1-3 先合并，R1-2 变基 |
| `backend/security/store.py` | R1-2（基线迁移调用）、R1-7（目录、强制改密） | R1-2 只调用不修改；修改都留给 R1-7 |
| `backend/security/policy.py` | R1-3（v2 规则）、R1-7（删除改密拦截） | R1-3 只追加 |
| `frontend/package.json` 与锁文件 | R1-1、R1-4、R1-5、R1-9 | 依赖一次性在 R1-4 引入；其余工作包需要新增时回到主干串行处理 |
| `.github/workflows/ci.yml` | R1-1、R1-2、R1-4、R1-9 | 每次只追加一个阶段 |
| `docker-compose.yml`、`Dockerfile` | R1-1（版本）、R1-2（迁移目录、备份卷） | R1-2 负责 |
| `frontend/src/legacy/**` | R1-5（搬迁）之后的 R1-6、R1-7、R1-8 | 批次 3 只做最小改动，各改各的文件 |

**如果一次任务做不完**：在工作包边界停下并如实报告。建议的切分点是批次 2 结束（R1-1 至 R1-5、R1-10 为第一次，其余为第二次）。

---

## 5 工作包计划

文件路径是建议，实施时可以调整，调整处写入报告第 5 节。测试数量是下限。

**本轮不做**（§14.3）：不改任何业务流程与数据结构；不重建业务页面；不引入智能体新能力。

### R1-1 工程基线与交接机制【必达】

**细项**

- 1.a 建分支 `dev/r1-foundation`（P-12）。
- 1.b 交接骨架：`AGENTS.md`（附录 F.4 八节）、`docs/blueprint/BLUEPRINT.md`（规划方已入库，只需核对校验和，P-13）、`STATUS.md`（F.3）、ADR 模板、`ADR-0001` 技术选型定版、`ADR-0002` 绞杀者迁移与旧页面隔离。
- 1.c 工具链：`pyproject.toml`（ruff，行宽 120；新包用完整规则，旧模块按文件豁免存量告警）、`requirements-dev.txt`（ruff、pytest 等，精确版本）、前端 ESLint、Prettier（行宽 110）、Stylelint 配置（旧代码不格式化、不检查）。
- 1.d **后端一次性格式化**：单独一个提交，只运行 `ruff format`，范围 `backend/ scripts/ ops/ tests/ .github/scripts/`，提交里只有 `.py` 文件。前后各保存一份逐条测试结果。
- 1.e `ci.yml` 初版：代码检查 → 后端测试（MySQL 8.4 服务，三个开关全开）→ 8 个回归脚本 → 前端构建。在 `dev/**` 的推送与拉取请求上触发。后续工作包往里追加阶段。
- 1.f 版本号改为 1.0.0，单独一个提交，改后跑全量回归。涉及的位置见本节末尾“版本号相关位置”，其中 Windows 升级器的版本常量必须同步（P-24）。

**文件**：新建 `AGENTS.md`、`docs/blueprint/STATUS.md`、`docs/blueprint/adr/{ADR-0000-template.md,ADR-0001-*.md,ADR-0002-*.md}`、`pyproject.toml`、`requirements-dev.txt`、`frontend/{eslint.config.js,.prettierrc.json,.prettierignore,.stylelintrc.json}`、`.github/workflows/ci.yml`；修改 151 个 `.py`（仅格式）、`VERSION`、`frontend/package.json` 与 `package-lock.json`、`docker-compose.yml`、`ops/windows_upgrade.py`、`ops/windows_launcher.py`（提示文字）、`tests/test_windows_{upgrade,launcher}.py`、`.gitignore`。

**测试**：格式化不新增测试，判据是前后结果逐条一致。版本号提交补 2–3 个用例：0.7.0 作为升级来源被接受；`TO_VERSION` 与当前版本一致。

**完成判据**：格式化提交通过语法树等价核验（方法见 §7）；`ruff format --check` 与 `ruff check` 在 CI 通过；`windows-native.yml` 与 `publish-accepted-0.7.0.yml` 无任何改动；`ci.yml` 在分支上通过。

**易错点**

- 不要运行 `ruff check --fix`：自动修复会删除“未使用”的再导出，改变行为。存量告警用配置豁免。
- **本轮不要改 `requirements.txt`，连注释也不要动。** Windows 构建会校验它的 SHA-256 与 `ops/windows-wheels.lock.json` 里记录的一致，不一致即构建失败。开发依赖放 `requirements-dev.txt`。本轮没有新增运行期 Python 依赖的需要。
- Windows 构建还会校验 `frontend/package.json` 与 `package-lock.json`（两处）的版本等于 `VERSION`。
- 格式化之后，核对 Windows 冒烟脚本里对源码做字节替换的模式仍能命中（试跑结论见 §1.5；若改用别的格式化风格要重新核对）。
- 仓库目前没有任何 Linux 流水线，`.github/scripts/` 下的 16 个测试也不在任何流水线里运行；`ci.yml` 把它们一并带上。

### R1-2 迁移框架【必达】

**细项**

- 2.a `backend/infra/migrations.py`：发现、排序、校验和、执行、登记；控制库与机构库两套序列；各库的 `schema_migrations`；同库串行；可查询当前版本与待执行项。
- 2.b 基线迁移：`migrations/control/0001_baseline.py` 调用 `initialize_control`、`initialize_federation`、`ensure_queue`；`migrations/tenant/0001_baseline.py` 调用 `ensure_tenant_schema` 的既有逻辑，不另写一份（它的执行顺序是：13 张核心表与索引 → 载荷列转 JSON → 台账、出处、专项、数据源、技能五组建表 → 机构根节点 → 排序规则归一 → 技能类资产迁移），并把只在加载演示资料时才建的 `demo_installations` 一并建好，使各机构库结构一致。
- 2.c `scripts/migrate.py` 改为唯一入口：独占维护锁 → 判断是否需要备份（P-07）→ 升级前全量备份并校验 → 控制库迁移 → 逐机构迁移；失败即停并留下持久的升级状态；可重入。`ops/provision.py`（Compose 的 `migrate` 作业，另负责建运行账号）、`ops/windows_initialize.py`、`scripts/bootstrap_admin.py` 改为调用同一入口。
- 2.d 升级前全量备份与整体恢复（P-07）：`backend/infra/backup.py`、`scripts/restore_upgrade_backup.py`；环境变量 `ONTOLOGY_BACKUP_DIR`（§3.9），Compose 挂独立持久卷。
- 2.e 服务启动（API、智能体服务、worker）只校验迁移版本与升级状态，不再改结构；登录与切换工作区时的惰性建库改为调用 `provision_tenant()`（只对全新的空机构库执行全部机构库迁移），R1-7 新增机构时也调用它。
- 2.f **0.7.0 基线夹具** `tests/fixtures/v070/`：生成器（只调用 0.7.0 的代码与接口，在 `1359118` 的检出上运行）、控制库与三个机构库（总部、一个总部部门、一个子公司）的结构与数据快照、一份 0.7.0 导出的机构备份包、清单（来源提交、各表行数、逐表校验和）、装载器。覆盖任务书列出的各类数据，并包含：三种角色与总部超管的测试账户（口令公开写在夹具说明里，全部是合成数据）、带强制改密标记的账户、一个加载过演示资料的机构（P-25）、一个目录中存在但尚未开通业务库的机构。
- 2.g 备份表分类清单骨架（`backend/system.py`）：机构库的每张表逐一归类（现有 34 张加 `demo_installations`、`schema_migrations`）；导出从“库里有什么导什么”改为按清单执行，对 0.7.0 已有的表结果不变；恢复白名单与“空工作区”判断同步（P-25）。把导出逻辑拆成不依赖登录上下文的函数，供升级前备份复用。
- 2.h `scripts/checks/check_migrations.py`：业务模块中的建表语句只减不增（基线为 46 处）；迁移序号连续；每个迁移有测试。
- 2.i **打包与沙箱的四份允许清单都要加上 `migrations/`**：`scripts/build_windows_release.py`（现在只带 `backend`、`ops`、`scripts`、`tests`）、`scripts/package_release.py`（源码包，也是“导出程序源码包”接口的实现）、`Dockerfile` 的 COPY、`scripts/check_security.py` 的沙箱（只复制 `backend/` 与 `VERSION`，7 个回归脚本和压测都用它）。

**文件**：新建 `backend/infra/{__init__,migrations,backup}.py`、`migrations/control/0001_baseline.py`、`migrations/tenant/0001_baseline.py`、`scripts/restore_upgrade_backup.py`、`scripts/checks/check_migrations.py`、`scripts/drills/upgrade_from_v070.py`、`tests/fixtures/v070/**`、`tests/migrations/**`；修改 `scripts/{migrate,bootstrap_admin,restore_backup,build_windows_release,package_release,check_security}.py`、`ops/provision.py`、`ops/windows_{initialize,launcher}.py`、`backend/{main,agent_service,system,identity,db}.py`、`scripts/run_worker.py`、`Dockerfile`、`docker-compose.yml`、`.env.example`。

**测试（不少于 25 个）**

| 文件 | 断言 |
|---|---|
| `tests/migrations/test_framework.py` | 按序执行；已执行的迁移被改动时拒绝；序号缺口或重复报错；重复执行无变化；两个执行者并发时只有一个生效；中途失败后停在失败处 |
| `tests/migrations/test_baseline_v070.py` | 在夹具上迁移后：全部既有表的行数不变、逐行哈希不变；`entities` 仍为 12 列、`relations` 仍为 7 列；控制库与每个机构库都有 `schema_migrations`；再执行一次无变化 |
| `tests/migrations/test_fresh_install.py` | 空服务器上全新安装成功；全新安装与“夹具升级”两条路径得到的表结构一致 |
| `tests/migrations/test_pre_upgrade_backup.py` | 迁移前生成备份并校验；备份失败则一条迁移都不执行；只保留最近 3 份；从备份恢复后各表校验和与升级前一致 |
| `tests/migrations/test_failure_stop.py` | 人为让一个机构的迁移失败：立即停止、后续机构未被触碰、服务拒绝启动；恢复后可重新迁移成功 |
| `tests/migrations/test_new_tenant.py` | 新开通机构自动执行全部机构库迁移 |
| `tests/migrations/test_backup_classification.py` | 每张机构库表都有分类；对 0.7.0 已有的表，导出结果与 0.7.0 一致；`schema_migrations` 不进备份；恢复白名单覆盖全部导出表；夹具里的 0.7.0 备份包可以恢复；加载过演示资料的机构可以备份并恢复（P-25） |
| `tests/checks/test_check_migrations.py` | 检查脚本能发现：业务模块新增建表语句、序号缺口、缺测试的迁移 |
| 既有用例 | 按新入口调整后全部通过：`test_windows_initialize`（它断言初始化入口以无参方式调用开通函数）、`test_windows_launcher`（进度阶段、停滞与超时）、`test_windows_upgrade`、`check_restore_v020.py`、三个 MySQL 集成用例 |

“恢复后旧版本可正常启动”由演练脚本 `scripts/drills/upgrade_from_v070.py` 覆盖：装载夹具 → 迁移 → 注入失败 → 恢复 → 用 `1359118` 的代码启动并通过健康检查。演练记录进证据包。

**登记项**：`schema_migrations`。

**易错点**

- 0.7.0 的机构库是惰性开通的：控制库在每个 API 进程启动时建，机构库在登录或切换工作区时由 `ensure_tenant_schema` 建，并有进程内缓存 `_READY`。Compose 的开通作业会一次建好全部机构；宿主机开发模式和回归脚本的沙箱则完全靠惰性路径。基线要同时处理“已开通”与“尚未开通”的机构，沙箱要改成先走迁移入口。
- 基线里有三段就地数据迁移（载荷列转 JSON、排序规则归一、技能类资产迁移），属于“0.7.0 结构”的一部分，必须原样执行；已有测试钉住它们的幂等性。
- 控制库初始化每次启动都会按 `organizations.json` 回写机构名称并重算超管标记。这一段留给 R1-7 改，R1-2 不动。
- 维护锁是非阻塞、不可重入的进程级文件锁：服务进程全程持共享锁，迁移与恢复要独占锁。备份和迁移要在同一次独占持有内完成，不要嵌套获取。
- **Windows 每次启动都会执行迁移入口**，并且启动器盯着进度：阶段名在 `ops/windows_initialize.py` 与 `ops/windows_launcher.py` 两处各有一份、必须一致；进度不得回退，总数一旦确定不得变化；总时限 600 秒，停滞 180 秒即判失败。因此空跑不能做备份，真要备份时要持续上报进度；新增阶段名要两处同步并更新测试。
- 恢复工具按“列集合完全相等”校验每张表。本轮机构库只多 `schema_migrations` 一张表，没有问题；R2 起给机构库加表加列时，恢复流程要改成“按备份的版本校验 → 恢复 → 执行机构库迁移”。写入 `STATUS.md` 给下一轮。
- 两个打包脚本会排除路径中名为 `data`、`runtime`、`work`、`uploads` 的目录：新目录不要用这些名字（夹具目录也一样）。蓝图为 R5 规划的 `backend/agent/runtime/` 会被排除，写入 `STATUS.md` 提醒。
- `.dockerignore` 排除了 `*.zip`：夹具里的备份包如果用这个扩展名，进不了镜像。
- 不给 `entities`、`relations` 加列或改列（G2）。`workspace_meta` 里的 `schema_version='0.5.0'` 是历史遗留的固定值，没有读取方，不要拿它当迁移版本。

### R1-3 后端骨架【必达】（含 R1-7a 权限键）

**细项**

- 3.a `backend/core/`：`config.py`、`errors.py`（附录 C.5 首批错误码与统一错误模型）、`ids.py`（附录 C.4）、`logging.py`（结构化日志，不含正文与密钥）。
- 3.b `backend/api/v2/` 骨架：统一错误处理、`X-Request-Id`、列表响应模型。
- 3.c `main.py` 变薄（P-06）：单独提交，语法树等价，原有路由集合不变。
- 3.d 能力注册表（§5.1）：`backend/capabilities/registry.py`（装饰器与注册期校验）、`context.py`、`preconditions.py`；通用调用路由 `POST /api/v2/capabilities/{id}:invoke`；`audit="metadata"` 的能力调用后写 `sec_audit`。
- 3.e 五个只读能力：`session.whoami`；`meta.kinds`（本轮返回 0.7.0 现有的 16 个种类及其词表名称，种类注册表在 R2）；`org.tree`（先按现有目录返回，R1-7 加上上下级与停用状态）；`agent.readiness`（占位，只返回现在能得到的检查项，R5 完整）；`semantic.search`（P-02）。
- 3.f v2 路由按权限键登记到 `backend/security/policy.py`；策略覆盖测试。
- 3.g `scripts/gen_api_types.py`：只导出 `/api/v2` 的 OpenAPI，生成 `frontend/src/api/generated/`，CI 校验生成物与代码一致。
- 7.a `backend/security/permissions.py`：§9.2 的权限键与 `permissions_for(principal, scope=None)`；五种角色、`duties`、仅总部超管的键、依赖跨机构授权的键。

**文件**：新建 `backend/core/**`、`backend/api/{__init__.py,v1/**,v2/**}`、`backend/capabilities/**`、`backend/security/permissions.py`、`scripts/gen_api_types.py`、`tests/{core,api,capabilities,security}/**`；修改 `backend/main.py`、`backend/security/policy.py`。

**测试（不少于 45 个）**

| 文件 | 断言 |
|---|---|
| `tests/core/test_errors.py` | 错误码与 HTTP 状态同附录 C.5；响应体符合统一错误模型；未预期异常返回 `INTERNAL_ERROR` 且带请求编号；原接口的错误格式不变 |
| `tests/core/test_ids.py`、`test_logging.py` | 编号格式（含 `org-` 加 8 位小写十六进制）；日志字段齐全且不含密钥 |
| `tests/api/test_main_assembly.py` | `main.py` 中没有路由处理函数与 SQL；原有路由的（方法，路径）集合与基线清单完全一致 |
| `tests/api/test_v2_errors.py` | 401、403、404、409、422 的统一错误体；列表响应形状 |
| `tests/security/test_policy_coverage.py` | 应用的每条业务路由都有策略；未登记的 v2 路由被拒绝；`analyst`、`viewer` 访问全部原有业务接口被拒绝 |
| `tests/security/test_permissions.py` | §9.2 表逐格（五种角色 × 各权限键）；`group_steward` 只对总部账户生效；`federation.read` 取决于授权；未知角色得到空集 |
| `tests/capabilities/test_registry.py` | 编号重复、应用助手绑定写入类能力、写入类能力标记为对外工具、输入模型允许多余字段——都在注册时失败 |
| `tests/capabilities/test_contracts.py` | 遍历注册表自动生成：输入输出模式合法；每种角色的允许与拒绝同声明一致 |
| `tests/capabilities/test_invoke_route.py` | 五个能力可调用；未知编号 404；无权限 403 并带下一步；写请求要 CSRF；请求体不能冒充身份与机构 |
| `tests/capabilities/test_search_forward.py` | 结果与 0.7.0 检索一致；没有草稿权限的主体只得到已发布内容 |
| `tests/api/test_openapi_types.py` | 生成物与代码一致 |
| `scripts/check_security.py`（扩展） | 对 v2 接口补上既有的三类攻击用例：跨机构访问、伪造机构请求头、伪造操作人，全部被拒绝（§9.7“后台接口”） |

**登记项**：`meta.kinds`、`session.whoami`、`org.tree`、`agent.readiness`。

**易错点**：身份只来自会话，能力函数不读请求对象与环境变量；`policy.ROLES` 不扩（P-19）；`/docs` 与 `/openapi.json` 现有的访问限制保持。

### R1-4 「知元」设计体系与品牌配置【必达】

**细项**

- 4.a 词表与品牌配置：一份源文件加生成脚本，产出 `frontend/src/i18n/zh-CN/**`、`frontend/src/i18n/brand.ts`、`backend/core/terms.py`、`backend/core/brand.py`（附录 C.1–C.3、C.6、C.7）；CI 校验两端同步。
- 4.b 令牌与主题：`frontend/src/design/tokens.css`（§10.3 全表，`--zy-` 前缀）、`theme.ts`（antd 映射）、全局样式；字体沿用仓库内置文件（P-23）。
- 4.c 依赖一次性引入并锁定版本：antd、`@ant-design/x`、TanStack Query、Zustand、Vitest、Testing Library、Playwright、openapi-typescript、样式前缀插件及检查工具。逐个核对许可，写入 ADR-0001。
- 4.d 第一批 18 个平台组件（`frontend/src/ui/`）：`AppShell NavRail TopBar IdentityBar PageHeader StatusTag KindTag StateBlock DataTable FilterBar HelpTip ViewModeSwitch ConfirmCard Timeline Omnibox DropOverlay TaskTray NotificationCenter`。
- 4.e 组件画廊 `/dev/gallery`，只进开发构建。
- 4.f 品牌接入：登录页、顶栏、浏览器标题（“知元 · 集团本体平台”）、系统信息页；替换 0.7.0 界面上的旧名与副标语。
- 4.g `scripts/checks/check_tokens.py`、`check_terms.py`。

**文件**：新建 `frontend/src/{design,ui,i18n}/**`、`frontend/src/features/dev/gallery/**`、`frontend/vitest.config.ts`、`scripts/gen_terms.py`、`scripts/checks/{check_tokens,check_terms}.py`、`tests/checks/test_check_{tokens,terms}.py`；修改 `frontend/package.json` 与锁文件、`frontend/index.html`、含旧名的旧文件（`auth.tsx`、`App.tsx` 等）、`backend/main.py` 的应用标题。

**测试**：前端单元测试不少于 30 个（每个组件至少 1 个，关键行为另计：`StateBlock` 六种状态都带下一步；`StatusTag`、`KindTag` 的文案只来自词表；`DataTable` 的列设置、空状态与地址栏同步；`Omnibox` 的键盘操作；`NavRail` 按权限渲染）。后端不少于 10 个（两个检查脚本各自的“违规样例必须被拦截、干净样例必须通过”；词表生成的一致性）。

**完成判据**：`check_tokens.py` 对 `legacy/`、`design/` 以外的前端代码报告 0 项；`check_terms.py` 确认品牌配置之外没有写死的产品名称；生产构建中不含画廊。

**易错点**

- 技术标识不改名：MCP 服务标识与五个工具名、`ONTOLOGY_` 前缀、数据库名前缀与 Compose 项目名、会话 Cookie 名、浏览器本地存储的键前缀、Windows 可执行文件名与数据目录名。`check_terms.py` 需要一份带理由的技术标识白名单。
- 新代码一律 `strict`、不出现令牌之外的取值、文案只取词表。

### R1-5 新外壳与信息架构【必达】

**细项**

- 5.a 搬迁：用 `git mv` 把 0.7.0 的 82 个前端文件整体移入 `frontend/src/legacy/`；`tsconfig.legacy.json` 用宽松检查，不为旧代码补类型。
- 5.b 样式隔离：构建期给 `legacy/` 的全部选择器加 `.legacy-root` 前缀，`:root`、`html`、`body` 改写为该容器；旧页面只渲染在这个容器里。
- 5.c 路由表与六个工作区（§10.1、本计划 §2.2）；工作区根地址落到该角色的第一个可用页面。
- 5.d 旧地址重定向表（带查询参数与锚点）：`/security` → `/account/security`；`/administration` → `/admin/users`；`/settings` → `/admin/model`；`/settings#demo` → `/admin/demo`。其余旧地址本轮不变。
- 5.e 外壳：`AppShell`、`NavRail`、`TopBar`、`IdentityBar`、面包屑、用户菜单（视图模式、账号安全、退出）。
- 5.f 基础设施：`api/client.ts`、TanStack Query（缓存键含机构与范围）、Zustand 仓库（会话、范围、界面）、地址栏状态工具。
- 5.g 机构切换沿用 0.7.0 做法并补齐 §9.4 中本轮适用的部分：有未保存内容或进行中的上传时先确认；切换时清空全部缓存与机构相关状态；迟到的旧响应不覆盖新界面。
- 5.h 旧代码适配层：保留 `useAuth` 接口（19 个旧文件在用），数据改由新会话仓库提供；旧的请求封装原样保留；全局智能浮层与后台任务浮层继续挂载。
- 5.i 去掉承接的旧页面上的口号横幅。
- 5.j `scripts/checks/check_legacy_shrink.py` 的基线文件数。

**文件**：新建 `frontend/src/app/**`、`frontend/src/api/client.ts`、`frontend/src/state/**`、`frontend/tsconfig.legacy.json`、样式前缀的构建配置、`scripts/checks/check_legacy_shrink.py`；移动 `frontend/src/{*.tsx,*.ts,*.css,components/**,pages/**}` → `frontend/src/legacy/**`；修改 `frontend/tsconfig.json`（`strict`）、`frontend/vite.config.ts`、`frontend/index.html`。

**测试**：前端单元测试不少于 10 个（一级导航恰为 6 项；三种既有角色与两种新角色的导航快照；重定向表逐条；切换机构清空缓存；迟到响应被丢弃）。构建检查 1 个：产物中来自 `legacy/` 的每条选择器都带 `.legacy-root` 前缀，没有裸的 `:root`、`html`、`body`。

**完成判据**：批次 1 准备的旧页面冒烟在新外壳下全部通过；1366×768 与缩放 125% 下外壳无横向滚动。

**易错点**（来自对 0.7.0 外壳的逐项清点，完整清单见附录 B）

- 旧弹窗用 `createPortal` 挂到 `document.body`，在 `.legacy-root` 之外。必须改挂到带该类名的专用容器，否则弹窗样式全部丢失。
- 外壳给页面上所有 `/api/` 下载链接自动追加 `workspace_id`，下载的机构校验靠它。漏掉会失去保护。
- 旧页面监听一组 `ontology:*` 窗口事件（登录失效、机构切换后的清理、智能任务的开始与变化），新外壳必须继续派发。
- 请求代次、切换机构时中止在途请求、`409 workspace_changed` 整页重载、401 回登录页，这些行为都要保留。
- 旧的请求封装会把 11 类智能辅助请求自动改走后台任务，原样保留，不要“顺手整理”。
- `/assets/:id` 是分发路由（按种类转到人员、专题、任务、专栏、材料、台账等），保留。
- 新的全局样式不能影响 `.legacy-root` 内的旧页面；用旧页面的截图前后对比确认。

### R1-6 工作台与万能入口 v1【必达】

**细项**

- 6.a 工作台 `/`：问候与身份；万能入口；待我处理；进行中；继续上次的工作（P-15）；本机构概况（不超过 4 个指标卡，可点击进入明细）；“业务汇报导览”入口卡；机构尚无内容时给管理员一个指向“管理 › 演示资料”的下一步。数据取自原接口：`/overview`、`/work/catalog`、待审核与待审定的列表接口、`/harness/runs`、`/agent/runs`。
- 6.b 万能入口 v1：搜索（`semantic.search`）；页面与操作命令（以“/”开头列出）；Ctrl/⌘+K 打开；回车直达。
- 6.c 拖入文件：全屏遮罩，按文件类型跳到对应的旧上传页并带入文件（表格 → 数据台账导入；文档 → 素材整理；CSV 两者都可，让用户二选一）。旧上传页做接收预置文件的最小改动。

**文件**：新建 `frontend/src/features/home/**`、万能入口的数据钩子与命令清单；修改旧的两个上传页（最小改动）。

**测试**：前端单元测试不少于 8 个（各区块用原接口数据渲染；各角色的待办来源；指标卡不超过 4 个；搜索结果分组；命令列表；文件类型到去向的映射）。端到端 2 条（搜索后回车到达对象页；拖入文件后到达旧上传页且文件已带入）。

**易错点**

- 工作台不出现口号与装饰图；一屏只有一个主动作。
- 0.7.0 的 `/work/catalog` 返回的是本机构的全部任务，不按人过滤。“待我处理”要么按负责人过滤，要么如实写明范围，不能名为“我的”而数是全机构的。数字必须与点进去看到的列表一致。
- 文件带入只走内存，不写浏览器存储。

### R1-7 身份与机构【必达】

**细项**

- 7.b **取消强制改密**（§9.5 六条）
  - 后端：清理 20 处以该标记为条件的拒绝分支（14 个文件），只去掉这一个条件，其余校验原样保留。位置：`policy.authorize`；`store.assert_principal_current`、`assert_job_principal_current`、`authorized_control_connection`；`context.readonly_session_execution`；`security/applications.py` 的 `_principal`、`_delegate`、`_public`、`application_options`；`ai._principal`、`ai.require_admin`；`demo.py` 的权限检查；`mcp_tools._current`；`sources._role`；`integrations.invoke_as_session`；`skill_harness._principal`；`harness_runs.submit`；`agent_tasks.current`；`skills._role`；`agent_tools._current`。
  - 创建账号、重置密码、超管初始化不再置位。接口仍接受 `must_change_password` 入参（忽略），会话载荷保留该键且恒为 `false`——既有脚本与旧页面依赖这两点。
  - 控制库迁移把存量标记全部置 0，列保留。
  - 前端删除改密门；账号安全入口显示“建议修改初始密码”的提示点（P-21）。
  - 启动器、初始化脚本、README 中的相关文字同步更新。
- 7.c **机构目录治理**（§9.1）
  - 迁移：`sec_tenants` 新增 9 列并回填。
  - `_directory()` 只在目录为空时播种；删除 51 断言；启动时不再按文件回写名称。
  - `backend/security/directory.py`：新增（编号为 `org-` 加 8 位十六进制，开通业务库并执行全部机构库迁移，创建机构根节点要素）、改名、调整上级与排序（同步根节点标题并留版本快照，不改编号与库名）、停用与启用（停用后该机构账户不能登录、不进入集团范围，数据保留）；不提供删除。
  - 能力 `org.manage`（仅总部超管，L3，全部写审计）；页面 `/admin/orgs`（树形）。
  - 文案里的“51 个机构”改为实际数量（`scripts/migrate.py`、`ops/provision.py`、README 等）。
- 7.d 角色与职责：迁移新增 `sec_users.duties`；新角色不可创建（P-19）。
- 7.e 身份条接入真实数据：登录身份、建设机构（总部超管可切换）、查询范围（固定“本机构”）。
- 7.f 用户偏好：迁移新增 `sec_user_prefs`；v2 接口只能读写本人数据；视图模式保存在服务端。
- 7.g 重建页面：登录页、账号安全 `/account/security`、账号与权限 `/admin/users`（“机构账号”“跨机构授权”两个页签）、审计 `/admin/audit`。能力 `user.manage`、`grant.manage`、`audit.query`。原有的 `/api/admin/*` 接口保留，与新能力调用同一组服务函数。
- 7.h 按附录 H.2 改写的既有断言，逐条列入报告：

| 位置 | 原断言 | 对应 |
|---|---|---|
| `scripts/check_security.py:294-301` | 首次登录带强制标记，改密前访问业务接口得到 403 | H.2-1 |
| `tests/test_skills.py:97-104` | 带强制标记的主体调用技能操作得到 403 | H.2-1 |
| `tests/test_applications.py:48-53` | 委托账号带强制标记时应用凭据失效 | H.2-1 |
| `tests/windows_portable_smoke.py:301` | 超管首次登录的强制标记为真 | H.2-1 |
| `scripts/check_security.py:387-388`、`:397` | 目录恰为 51 家，各类别数量固定 | H.2-3 |

**文件**：新建 `migrations/control/000{2,3,4,5}_*.py`、`backend/security/directory.py`、`backend/capabilities/{org,user,grant,audit}/**`、`backend/api/v2/{orgs,users,grants,audit,prefs}.py`、`frontend/src/features/{auth,account,admin}/**`、`tests/identity/**`；修改上述 14 个后端文件、`backend/security/store.py`、`backend/identity.py`、`scripts/{migrate,bootstrap_admin,load_test,check_security}.py`、`ops/provision.py`、README、表中列出的测试；删除旧的登录门与改密门界面、`Security.tsx`、`Administration.tsx`（通过对照清单之后）。

**测试（后端不少于 45 个）**

| 主题 | 断言 |
|---|---|
| 取消强制改密 | 新建账户立即可调业务接口；重置密码后不被阻断；带旧标记的账户迁移后可用；20 处分支逐一验证“标记不再拦截，而只读、角色、身份类型等其余校验仍然拦截” |
| 保留的安全行为 | 主动改密；密码强度；登录限速；改密后撤销其他会话；会话列表与撤销；相关审计 |
| 机构目录 | 只在空目录时播种；改动目录文件不影响已有机构；迁移前后 `sec_tenants` 的编号与库名全等、行数不变；新增机构得到新编号、业务库已开通、根节点已建、可登录；改名同步根节点并留版本；停用后登录被拒且不进入集团范围；启用后恢复；非总部超管被拒；每个操作都有审计记录；总部不可停用 |
| 角色 | 两种新角色的账户不能经任何接口创建；会话载荷的角色清单不含它们 |
| 结果一致 | 同一份数据经原接口与新能力查询账号、授权、审计，结果一致 |
| 用户偏好 | 只能读写本人；键与取值校验；持久化 |

**登记项**：`sec_tenants` 新增列、`sec_users.duties`、`sec_user_prefs`、`org.manage`、`user.manage`、`grant.manage`、`audit.query`。

**易错点**

- 修改账号会撤销目标账号的全部会话（改自己也一样）；“总部管理员自动成为超管”“最后一个超管不能停用或降级”“开通账号自动写入默认授权”等隐含规则保持不变。
- 请求体校验失败时旧前端只显示通用文案，新页面要在前端预校验（用户名 3–80 位及字符集、原因不少于 2 字、密码规则）。
- 两个 Windows 冒烟脚本的改动无法在本地验证（P-10）。

### R1-8 管理区重建【必达】

**细项**

- 8.a `/admin/model` 模型与运行：承接统一智能服务的配置与验证（地址、模型、密钥不回显、清除密钥、验证连接；非管理员只读；跨机构运维时不能验证）。能力 `model.configure`。就绪诊断区用 `agent.readiness`。
- 8.b `/admin/backup` 备份与升级：导出工作区备份（`backup.export`）；恢复说明（`backup.restore`，P-08）；升级前备份清单与迁移版本（只读）；导出程序源码包（仅总部超管）。
- 8.c `/admin/system` 系统信息：“知元 1.0.0”、英文名小字、健康检查、迁移版本、运行方式说明。旧页面里写死为“已启用”的两行状态，改为真实检查或不显示。
- 8.d 承接：`/admin/demo` 用 `LegacyBlock` 嵌入旧演示面板，功能不变；集成应用的旧页面在“管理”下可达。
- 8.e 管理区次级导航与可见性（P-03）。

**文件**：新建 `frontend/src/features/admin/{model,backup,system,demo}/**`、`frontend/src/ui/LegacyBlock.*`、`backend/capabilities/{model,backup}/**`、`backend/api/v2/system.py`；删除 `Settings.tsx`、`HarnessConfiguration.tsx`（通过对照清单之后）。

**测试**：后端不少于 10 个（配置经新能力与原接口结果一致；密钥不回显、不进备份、不进日志；导出内容与原接口一致；`backup.restore` 在线调用被前置条件拒绝；非管理员被拒）。前端单元测试不少于 5 个。

**登记项**：`model.configure`、`backup.export`、`backup.restore`。

**易错点**：术语黑名单在“模型与运行”“集成应用”两处有豁免，其余管理页没有；公网链接见 P-17。

### R1-9 质量底座【必达】（贯穿全轮）

**细项**

- 9.a 端到端工程 `e2e/`：夹具为“装载 `tests/fixtures/v070` → 迁移 → 启动应用”。冒烟：三种角色登录、六个工作区导航、总部超管切换机构、每条旧路由可打开（有标题、无控制台错误、无失败请求）、重定向；三种角色逐一访问全部新路由，无权限时进入统一的无权限状态页（§9.7“直接访问链接”）。**旧路由的冒烟先在 0.7.0 界面上跑通**，作为 R1-5 的回归基准。
- 9.b 视觉基线入库：登录、工作台、外壳加一个旧页面、管理各页、账号安全；三种视口（1366×768、1920×1080、1366×768 缩放 125%）。关闭动画，固定字体与浏览器版本。
- 9.c 检查脚本：`check_policy_coverage.py`、`check_legacy_shrink.py`、`check_offline.py`，连同 `check_migrations.py`、`check_tokens.py`、`check_terms.py` 一起进 CI。每个脚本都配“违规样例必须被拦截”的自测。
- 9.d 离线构建检查骨架 `ops/offline/`：联网时生成依赖包；CI 在断网容器里完成后端安装、前端构建与启动测试。
- 9.e 证据包生成脚本 `scripts/build_evidence.py` → `work/evidence/r1/`（P-11）。
- 9.f `ci.yml` 补齐 §13.3 的全部阶段。本轮没有 `evals/`，“模拟评测”阶段运行 `check_agent_runtime.py`，不放空壳。

**文件**：新建 `e2e/**`、`scripts/checks/{check_policy_coverage,check_legacy_shrink,check_offline}.py`、`tests/checks/**`、`ops/offline/**`、`scripts/build_evidence.py`；修改 `.github/workflows/ci.yml`。

**测试**：端到端冒烟不少于 12 条；视觉基线不少于 8 个页面状态；检查脚本自测不少于 9 个。

### R1-10 Harness 技术探针【必达】（限时，不影响其他工作包）

**细项**

- §6.2 探针表 8 项，在锁定版本与候选新版本上逐项验证。脚本放 `scripts/probes/harness/`，每项给出可复现的命令与输出摘要。
- `ADR-0003`：集成方式、可用能力、版本结论、内网协议清单；每一项都有结论和采用的预定决策。
- 仓库已有“真实 SDK 加本地模型替身”的测试（`RUN_HARNESS_SDK_TESTS`），可以直接改造成探针，不依赖真实密钥：

| 探针项 | 可复用的基线资产 | 需要补的 |
|---|---|---|
| 单轮 ≥ 40 步与取消 | `tests/test_skill_harness.py` 的循环模式（断言恰好停在第 8 次模型请求）与取消后进程回收的用例；`tests/fixtures/agent_model_server.py` 按剧本驱动 | 步数与调用数上限写在 `backend/harness_plugin/*.mjs` 和 `backend/agent_tools.py` 里，探针用自己的补丁与插件副本放宽，不改生产文件；同时受 CPU 25 秒、墙钟 90 秒、Node 堆 256 MB 约束 |
| 并行工具调用 | 无：现有替身每条消息只发一个工具调用，补丁把并行上限定为 1 | 新写“一条消息多个工具调用”的替身剧本 |
| 文本增量输出 | `tests/test_harness_worker_modes.py` 的分块流式用例与 `public-progress` 插件 | 基本可直接复用 |
| 上下文压缩、失败重试、思考开关 | 补丁里这三项现在都是关闭的 | 在探针补丁里逐项打开；思考开关与协议兼容性需要真实模型才能定论 |
| 新版本的校验摘要 | 锁文件只记录了 Linux x64 一个平台；Windows 的摘要在 `ops/windows-runtime.lock.json`；代码里另有一份写死的版本与摘要 | 在隔离的虚拟环境里装候选版本，不动仓库的版本锁 |

- 需要真实模型的项，没有条件时列入“待负责人执行”，不得用替身结果代替。
- 不改生产代码。版本只在新版本通过两个平台的摘要校验与既有 Harness 测试时才建议更新（涉及锁文件、`requirements.txt`、Windows 轮子锁与代码里的摘要常量，本轮只出结论，不动手）。
- 到限时仍未有结论的项，按探针表“不可用时的预定决策”落地并写明。

**文件**：新建 `scripts/probes/harness/**`、`docs/blueprint/adr/ADR-0003-harness-probe.md`。

### 版本号相关位置（R1-1 的 1.f 要逐处核对）

| 位置 | 现状 | 本轮怎么办 |
|---|---|---|
| `VERSION` | `0.7.0`；`backend/version.py` 读取并校验必须是纯 `X.Y.Z` | 改为 `1.0.0` |
| `frontend/package.json`、`package-lock.json`（两处） | `0.7.0`；前端显示的版本来自它；Windows 构建校验它等于 `VERSION` | 同步 |
| `docker-compose.yml` | 默认镜像标签 `0.7.0` | 同步 |
| `ops/windows_upgrade.py` | `TO_VERSION='0.7.0'`、`FROM_VERSIONS=('0.5.0','0.6.0')`，要求当前版本等于 `TO_VERSION` | 按 P-24 修改 |
| `ops/windows_launcher.py` | 三处提示文字写着“0.5.0 或 0.6.0 到 0.7.0” | 同步文字 |
| `tests/test_windows_upgrade.py`、`tests/test_windows_launcher.py` | 用例依赖上面两个常量 | 同步并补 0.7.0 来源的用例 |
| `tests/windows_upgrade_smoke.py`、`windows-native.yml` | 升级基线写死为已发布的 0.6.0 包（含校验和与来源提交） | 本轮不改；写入 `STATUS.md`，R4 改为 0.7.0 包 |
| `.github/scripts/publish_accepted_07.py` 及其流水线 | 0.7.0 的一次性发布流程 | 不动 |
| `backend/db.py` 的 `schema_version='0.5.0'`、技能镜像标签 `0.5.0`、演示包键 `business-demo-0.7` | 不是平台版本 | 不动 |

---

## 6 本轮登记项落位

| 登记项 | 类别 | 工作包 | 落点 |
|---|---|---|---|
| `schema_migrations` | 数据表 | R1-2 | 迁移框架在控制库与每个机构库中建立 |
| `sec_tenants` 新增 `parent_id`、`short_name`、`org_code`、`sort_order`、`level`、`is_demo`、`mirror_of`、`disabled_at`、`disabled_reason` | 列 | R1-7 | `migrations/control/0002` |
| `sec_users.duties` | 列 | R1-7 | `migrations/control/0003` |
| `sec_user_prefs` | 数据表 | R1-7 | `migrations/control/0004` |
| （存量强制改密标记置 0） | 数据迁移 | R1-7 | `migrations/control/0005`；不是登记项，列出以免遗漏 |
| `meta.kinds`、`session.whoami`、`org.tree`、`agent.readiness` | 能力 | R1-3 | `backend/capabilities/`。`agent.readiness` 为占位（R5 完整） |
| `semantic.search`（提前，P-02） | 能力 | R1-3 | 同上；报告第 7 节注明“多出”及原因 |
| `org.manage`、`user.manage`、`grant.manage`、`audit.query` | 能力 | R1-7 | 同上。`user.manage` 本轮只含账号与角色（职责授予在 R8，交接清单在 R4）；`audit.query` 为基础版（R9 完善） |
| `model.configure`、`backup.export`、`backup.restore` | 能力 | R1-8 | 同上。`model.configure` 本轮承接现有的单一配置（多档位在 R5）；`backup.restore` 见 P-08 |
| 后台任务、机构设置项、走查用例与体验路线 | — | — | 本轮无 |

新增环境变量：`ONTOLOGY_BACKUP_DIR`（§3.9 已定义，R1-2 启用）。新增错误码：只用附录 C.5 已登记的。

---

## 7 验收门：实施方的证据与我的独立核验

**原则**：我按分支名拉取 `dev/r1-foundation` 的交付提交，在全新检出上、用自己起的数据库重跑，不以对方的日志和截图为准；GitHub Actions 的运行结果作为参考一并核对。任何一个验收门不通过，本轮不通过，不打标签。

| 门 | 内容（§14.3） | 实施方交付 | 我的独立核验 |
|---|---|---|---|
| 1 | 既有后端测试与安全回归通过；格式化前后一致 | 全量回归日志；改写断言清单（位置、对应的 H.2 条目） | 重跑全部。基线 489 个用例标识逐一比对：除清单列明的改写外，不得消失、不得由通过变为跳过；跳过集合仍是那 39 个 Windows 原生项。格式化提交：只含 `.py`，每个文件前后语法树等价（文档字符串按空白规整后比较）。8 个回归脚本与 16 个流水线脚本测试重跑 |
| 2 | 迁移无损、可重入；新装与升级都可启动；升级前备份可恢复；失败即停并可回到旧版本 | 迁移测试；演练记录 | 两份数据各跑一遍：仓库里的夹具，以及我在 `1359118` 上独立生成的一份。核对行数与逐行哈希、重复执行、全新安装、从备份恢复后的一致性；注入失败后恢复，并用 0.7.0 代码启动。另比对我生成的结构与夹具的结构，确认夹具确实出自 0.7.0 |
| 3 | 旧页面全部可达可用；重建页面通过 A.2 对照清单 | 冒烟报告；A.2 对照表 | 用我自己写的浏览器脚本，以三种角色逐路由打开；按附录 B 的操作清单逐条核对重建页面；取 0.7.0 的同页截图对照旧页面内容区 |
| 4 | 令牌外取值为 0；一级导航 6 项；无横向滚动；产品名称统一；技术标识不变 | 检查脚本输出；截图 | 重跑两个检查脚本，并注入违规样例确认脚本真的会报错；在 1366×768 与缩放 125% 下实测滚动宽度；核对登录页、顶栏、浏览器标题、系统信息页四处名称；逐项比对仓库名、包名、环境变量前缀、数据库名前缀、MCP 服务标识 |
| 5 | 三类账户都可直接使用；主动改密、限速、会话撤销正常 | 测试清单 | 实际操作新建账户、重置密码后的账户、带旧标记的账户；实测改密、限速与撤销 |
| 6 | 总部超管可新增、改名、停用、启用机构；新机构可登录使用；既有编号与归属不变 | 测试与截图 | 实际走一遍新增到登录的全过程；比对迁移前后 `sec_tenants` 的编号与库名；用非超管账号确认被拒 |
| 7 | 五种角色的权限键测试通过；账号管理不出现新角色；跨机构授权与审计结果与 0.7.0 一致 | 单测；比对记录 | 重跑；对照 §9.2 的表逐格抽查；从界面和接口两头尝试创建新角色账户；同一份数据在 0.7.0 与新版本下比对授权与审计结果 |
| 8 | `ADR-0003` 完成，探针表每项都有结论与预定决策 | 文档与探针脚本 | 逐项核对；不需要真实模型的项重跑；需要真实模型的项视 P-20 而定，否则保留在“待负责人执行” |
| 9 | 测试增量：后端 ≥ 80，前端单元 ≥ 40，端到端冒烟 ≥ 12，视觉基线 ≥ 8 个页面状态 | 统计表 | 按 §1.3 的口径自己清点 |
| 10 | 交接材料与证据包齐全 | §14.1 的六项收尾 | 逐项核对；抽查 `AGENTS.md` 的代码地图、`STATUS.md` 与代码是否一致；用 0.7.0 备份的升级演练重做一遍 |

**不属于某一个门、但每轮都查的底线**（0.3 的禁止事项）：`qa/publish-accepted-0.7.0` 仍指向 `1359118`；没有删除既有测试、没有缩小断言；原接口的路由策略逐条比对，没有放宽；`entities` 12 列与 `relations` 7 列不变；仓库里没有密钥（全库扫描）；没有运行时访问公网的依赖；两个既有流水线文件没有改动。

**验收结论**写在 `docs/blueprint/rounds/R1-ACCEPTANCE.md`：逐门给出通过、不通过或有条件通过，附命令与结果；不通过项列成清单交回，修复后复验。

---

## 8 裁剪阀、停工点与风险

**裁剪阀**（§14.3）：只有两项可以降为冲刺项——组件画廊的示例完整度、“备份与升级”页的界面打磨。其余都是必达。未完成的冲刺项写入 `STATUS.md`，自动成为 R2 的必达项。

**停工点**：只在工作包边界停。停下时本计划第 11 节要写清已完成的工作包、进行中的细项和下一步。不为赶进度降低验收门。

**主要风险**

| 风险 | 对策 |
|---|---|
| 旧页面搬进隔离容器后样式或行为回归 | 冒烟先在 0.7.0 界面上建立基准；弹窗容器、下载链接、窗口事件等逐项保留（R1-5 易错点）；旧页面截图前后对比 |
| 清理强制改密时误删其他安全校验 | 每处只去掉一个条件；20 处分支各有一条“其余校验仍然拦截”的测试；`check_security.py` 全过 |
| 基线迁移漏掉惰性建表或就地迁移 | 全新安装与夹具升级两条路径的结构比对；逐行哈希 |
| 格式化与并行分支冲突 | 格式化先行，单独提交 |
| 标签触发对外发布 | P-09：标签由负责人决定；先做不发布的预演 |
| Windows 包缺少新目录，或冒烟脚本断言过时 | R1-2 的 2.i 与 R1-7 的 7.h；流水线预演 |
| 视觉基线不稳定 | 关闭动画，固定字体、浏览器与视口；基线在与 CI 相同的环境生成 |
| 组件库大版本与对话组件不兼容 | R1-4 开头先验证，结论写入 ADR-0001；不兼容时按 §3.2 自行实现同名组件（R5 才用到） |

---

## 9 收尾清单（实施方，缺一视为未完成）

1. 十个验收门逐条自验，写入 `docs/blueprint/rounds/R1-REPORT.md`（附录 F.2 的十一节）。本轮没有走查用例。
2. 更新 `docs/blueprint/STATUS.md`：基线、迁移版本号、遗留、已知限制（含 P-17）、旧页面剩余清单与在用的承接块。给后续轮次的提醒至少包括：
   - R2：恢复工具按“列集合完全相等”校验，机构库加表加列前要先改恢复流程；`semantic.search` 已提前登记，R2 是换实现而不是新登记。
   - R4：Windows 流水线与升级冒烟的基线仍是 0.6.0 的发行包，要换成 0.7.0；“最近访问”改用服务端数据。
   - R5：两个打包脚本会排除名为 `runtime` 的目录，蓝图规划的 `backend/agent/runtime/` 需要先调整打包规则。
   - 每轮：改版本号时同步 Windows 升级器的两个版本常量（P-24）；`requirements.txt` 一动就要同步 Windows 轮子锁。
3. 更新 `AGENTS.md` 的代码地图与常用命令。
4. `ADR-0001`、`ADR-0002`、`ADR-0003`；自行裁决清单（含本计划 §3 中标“告知”的各项与执行中新出现的事项）。
5. 证据包 `work/evidence/r1/`（P-11）：关键页面截图（两种视图、三种视口）、测试报告、迁移与升级演练记录、可复现步骤。
6. 用一份 0.7.0 备份演练升级。
7. “待负责人执行”清单：Windows 原生流水线预演（P-09、P-10）、探针中需要真实模型的项（P-20）、打标签。
8. 交付方式：推送 `dev/r1-foundation`，告知提交号。**不打标签；除非负责人要求，不建拉取请求。**

---

## 10 给 GPT-6 Astra 的启动补充说明

负责人把附录 F.1 的启动提示发给实施方，“本轮补充说明”填下面这段（负责人实际转交的完整提示另行提供，以那份为准）。

```
1. 仓库 https://github.com/min108/ontology-one ，从分支 claude/gpt6-astra-project-planning-uf1ggp 上本计划所在的提交（提交号由负责人转交）切出 dev/r1-foundation。
   不向 qa/publish-accepted-0.7.0 推送，不打任何标签，不建拉取请求。默认分支 main 是 0.1.0-demo，不是基线：建 worktree 时显式指定起点，开工先核对 VERSION。
2. 先读 docs/blueprint/rounds/R1-PLAN.md。它是本轮执行计划，§3 的裁决已经负责人确认，按其执行。
   蓝图原文：docs/blueprint/BLUEPRINT.md，按 §14.1 用 grep 定位、按行读取，不通读。
3. 开工前在你自己的环境用 docs/blueprint/rounds/r1-baseline/run_baseline.sh 重跑基线，逐条结果与 baseline-tests.txt 比对。
   既有后端用例是 489 个（test 前缀函数 490 个，Windows 相关 111 个），不是 353。
4. 环境：Docker、MySQL 8.4、Node 22、Playwright、Python 3.12。模型密钥只从环境变量 DEEPSEEK_API_KEY 读取，不写入任何文件、提交、日志或报告。
5. 子代理：不用最高推理档；并行不超过 5 个；各用独立 worktree。
6. 后端格式化只运行 ruff format，单独提交，先于一切并行分支。
7. 进展随时写入 R1-PLAN.md 第 11 节并推送，便于中断后恢复与规划方查看。完成后推送分支并告知最后的提交号与 ci.yml 的运行编号。验收由独立会话进行。
```

## 11 进展记录（实施方在执行中更新）

| 日期 | 工作包 | 状态 | 提交 | 正在做的细项与下一步 |
|---|---|---|---|---|
| 2026-11-16 | 开场动作 | 完成 | 本计划所在提交 | 负责人已确认本计划；下一步：实施方切出 `dev/r1-foundation`，重跑基线，开始 R1-1 |
| 2026-11-16 | 实施方开工前复现 | 完成 | `6e5a02c`（代码未改） | 已从指定起点建立开发分支并核对版本、蓝图摘要与推送通道。首次后端结果为 433 通过、39 跳过、17 错误；全部错误在 Harness 的可写 tmpfs 前置检查。负责人已授权仅修复测试环境，完整基线通过后继续。已开放测试命令对 `/dev/shm` 的写访问，完整重跑通过：450 通过、39 跳过；16 个流水线测试、8 个回归脚本与前端构建全过。原清单漏收项亦通过，详见 `R1-PREFLIGHT.md`。下一步：R1-1 纯格式化、语法树等价与全量回归。 |

| 2026-11-16 | R1-1 | 进行中 | `353b9b2`（纯格式化） | 格式化 150/151 个 Python 文件，归一文档字符串后 AST 全等；489 个结果前后完全一致，8 个回归、16 个流水线测试、前端构建全过。正在加入工具链、交接骨架、ci.yml 与 1.0.0 升级契约；尚未切并行分支。 |

---

## 附录 A 基线清单

**复现**（脚本化版本见 `docs/blueprint/rounds/r1-baseline/run_baseline.sh`）

```bash
# 数据库（一次性测试实例）
docker run -d --name oo-mysql84 --network host -e MYSQL_ROOT_PASSWORD=<随机> -e MYSQL_ROOT_HOST=% -e TZ=Asia/Shanghai \
  mysql:8.4 --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci --max-connections=512
export MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_USER=root MYSQL_PASSWORD=<同上>
# 依赖
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
docker build -f ops/skill-runner.Dockerfile -t ontology-skill-test:0.5.0 .
# 后端全量（-v 输出逐条结果，用于比对）
RUN_HARNESS_SDK_TESTS=1 RUN_FEDERATION_MYSQL_TESTS=1 RUN_JOBS_MYSQL_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s .github/scripts -p 'test_*.py'
for s in restore_v020 security workflows sources mcp skills entity_storage agent_runtime; do .venv/bin/python scripts/check_$s.py; done
# 前端
npm --prefix frontend ci && npm --prefix frontend run build
```

用例标识清单的校验和：把 `-v` 输出中每条用例整理成“`模块.类.方法::方法名`”，排序后以换行连接，取 SHA-256。

**按模块的结果（三个开关全开，Linux）**

| 模块 | 用例 | 通过 | 跳过 | | 模块 | 用例 | 通过 | 跳过 |
|---|---|---|---|---|---|---|---|---|
| test_agent_admission | 2 | 2 | 0 | | test_mcp_tools | 18 | 18 | 0 |
| test_agent_content | 2 | 2 | 0 | | test_mcp_transport | 13 | 13 | 0 |
| test_agent_runtime | 3 | 3 | 0 | | test_platform_io | 20 | 7 | 13 |
| test_agent_sdk | 6 | 6 | 0 | | test_review_boundaries | 8 | 8 | 0 |
| test_agent_streaming | 18 | 18 | 0 | | test_skill_execution | 12 | 12 | 0 |
| test_agent_tool_executor | 11 | 11 | 0 | | test_skill_harness | 36 | 32 | 4 |
| test_ai_helper_authorization | 5 | 5 | 0 | | test_skill_migration | 7 | 7 | 0 |
| test_ai_runtime | 26 | 26 | 0 | | test_skill_package | 20 | 20 | 0 |
| test_applications | 10 | 10 | 0 | | test_skills | 21 | 21 | 0 |
| test_controlled_live_cli | 15 | 15 | 0 | | test_source_adapters | 30 | 30 | 0 |
| test_db_adapter_compat | 3 | 3 | 0 | | test_sources | 10 | 10 | 0 |
| test_db_pool | 2 | 2 | 0 | | test_storage | 1 | 1 | 0 |
| test_demo | 8 | 8 | 0 | | test_windows_async_process | 16 | 16 | 0 |
| test_demo_mysql | 1 | 1 | 0 | | test_windows_distribution | 4 | 4 | 0 |
| test_experience_contract | 4 | 4 | 0 | | test_windows_initialize | 3 | 3 | 0 |
| test_federation | 14 | 14 | 0 | | test_windows_launcher | 22 | 22 | 0 |
| test_federation_mysql | 1 | 1 | 0 | | test_windows_mysql_utf8 | 4 | 4 | 0 |
| test_harness_diagnostics | 6 | 6 | 0 | | test_windows_process | 23 | 23 | 0 |
| test_harness_legacy_runs | 6 | 6 | 0 | | test_windows_sandbox_native | 21 | 0 | 21 |
| test_harness_live_cli | 12 | 12 | 0 | | test_windows_skill_runner | 10 | 10 | 0 |
| test_harness_runs | 20 | 20 | 0 | | test_windows_upgrade | 8 | 7 | 1 |
| test_harness_worker_modes | 6 | 6 | 0 | | **合计** | **489** | **450** | **39** |
| test_jobs_mysql | 1 | 1 | 0 | | | | | |

**基线代码事实**（供各工作包核对）

- 后端 65 个 `.py`，约 1.4 MB；228 个路由装饰器（含内部智能体服务的 12 个）；`backend/main.py` 内联 17 条路由。
- 建表语句 46 处，分布在 12 个模块：控制库 10 张表；机构库 34 张表由 `ensure_tenant_schema` 建立，另有 `demo_installations` 在加载演示资料时才建；还有 1 处重复定义（`asset_import_batches`）。蓝图附录 B.1 把 `demo_installations` 列在控制库下，与代码不符，以代码为准。
- 备份恢复白名单 34 张表，由建表语句派生，按列集合完全相等校验。
- 现有流水线两条：Windows 原生（拉取请求、`release/*` 推送、`v*` 标签触发；标签上另有发布作业）与 0.7.0 的一次性发布。没有 Linux 流水线。
- 按位置插入：`entities` 12 处，`relations` 16 处（仅 `backend/`）。
- 前端 `src` 下 82 个文件，约 1.4 MB，29 个页面文件，30 条路由，29 个样式文件；`strict` 为 `false`；没有测试与代码检查配置。
- 强制改密标记共出现 62 处：后端 15 个文件、前端 2 个文件、脚本与测试 8 个文件。
- 机构目录文件：总部 1、派驻 1、总部部门 12、子公司 37，共 51。

---

## 附录 B 旧页面操作清单（附录 A.2 的对照基准）

来自对基线代码的逐文件清点。0.7.0 的接口都在 `/api/` 下（蓝图称 v1）。“去向”是本计划的安排；重建页面删除旧文件前，逐条确认并写入报告。

### B.1 外壳

| 编号 | 0.7.0 的功能 | 去向 |
|---|---|---|
| S-01 | 侧栏 19 个入口（非管理员 17 个）；唯一的可见性条件是“管理员才看到账号与权限、集成应用” | 六个工作区与次级导航（§2.2、P-03） |
| S-02 | 机构切换：只有总部超管有多个选项；切换后整页重载回首页，切换中有提示 | 身份条 |
| S-03 | 全局搜索弹窗：Ctrl/⌘+K 打开，Esc 关闭；结果跳到对象页；内含“跨机构成果检索”入口 | 万能入口 v1 |
| S-04 | 后台任务浮层：轮询两个运行接口；条目按类型跳回原业务页 | 旧组件继续挂载（R5 由任务托盘取代） |
| S-05 | 全局智能对话浮层：进度、结果、停止、返回业务页 | 旧组件继续挂载（R6 删除） |
| S-06 | 助手状态指示（已连接、待验证、未配置），点击进入设置 | 顶栏保留指示，点击进入 `/admin/model` |
| S-07 | 待办铃铛，进入“我的工作台” | 工作台“待我处理” |
| S-08 | 账号菜单：姓名、角色、机构、超管标识；账号安全；账号与权限；退出登录 | 用户菜单 |
| S-09 | 来路面包屑与“返回上一处”；对象页显示“所属工作与专栏” | 旧页面内保留；新外壳另有面包屑 |
| S-10 | 对象页的归属条：所属机构、可见范围的查看与保存 | 旧页面内保留 |
| S-11 | 无建设权限时的只读提示条 | 保留等价提示 |
| S-12 | 给页面上的 `/api/` 下载链接自动追加 `workspace_id` | 必须保留 |
| S-13 | 机构被切换（409）时整页重载；登录失效（401）回登录页；切换时中止在途请求并清理本地缓存 | 必须保留 |
| S-14 | `/assets/:id` 按对象种类分发到对应页面 | 保留 |
| S-15 | 404 页，带“返回机构总览” | 新外壳的统一状态页 |
| S-16 | 页面内锚点滚动（如 `/settings#demo`）与返回时恢复滚动位置 | 旧页面内保留；`#demo` 重定向到 `/admin/demo` |
| S-17 | 智能辅助请求自动转为后台任务（11 类） | 旧请求封装原样保留 |
| S-18 | 窄屏下的折叠导航；打印时隐藏导航 | 新外壳的响应式布局 |

### B.2 登录

| 编号 | 0.7.0 的功能 | 去向 |
|---|---|---|
| L-01 | 账号加密码登录；提交后清空密码；账号或密码错误、账号停用的提示；登录过于频繁的提示；服务不可达时的“重新连接” | 新登录页，行为不变 |
| L-02 | 登录后进入首页 | 进入工作台 |
| L-03 | 强制改密门（整站拦截） | 取消（H.2-1） |
| L-04 | 页面文案：旧产品名、标语、默认管理员账号提示、联系管理员提示、版本号 | 按品牌配置与词表重写 |

### B.3 机构总览（`/` → 工作台）

| 编号 | 0.7.0 的功能 | 去向 |
|---|---|---|
| O-01 | 四个统计数：资产总量、关联关系、共建单位、业务记录 | 本机构概况的指标卡，可点击进入对应页面 |
| O-02 | 业务领域覆盖、近期沉淀成果、重点工作专题、工作动态四个列表，各带跳转 | 不在工作台复刻；去向逐项写入报告（P-16） |
| O-03 | “打开业务汇报导览” | 工作台入口卡 |
| O-04 | 机构没有资产时，给管理员“加载模拟样例”的引导 | 工作台的下一步，指向“管理 › 演示资料” |
| O-05 | 三个快捷入口：整理材料、接入台账、查找分析方法 | 万能入口的页面命令 |
| O-06 | 口号横幅与装饰图 | 取消（H.2-4） |

### B.4 账号安全（`/security` → `/account/security`）

| 编号 | 0.7.0 的功能 | 去向 |
|---|---|---|
| SEC-01 | 当前账号：姓名、登录账号、当前机构、角色、当前权限 | 保留 |
| SEC-02 | 修改密码：12–128 位且含字母与数字、两次一致；成功后撤销其他设备的会话并提示 | 保留 |
| SEC-03 | 登录会话列表：设备信息、最近使用、登录时间、来源、到期时间、当前会话标记；刷新 | 保留 |
| SEC-04 | 撤销会话（带确认）；撤销当前会话等于退出 | 保留 |
| 新增 | “建议修改初始密码”的提示点 | §9.5 |

### B.5 账号与权限（`/administration` → `/admin/users`、`/admin/audit`、`/admin/orgs`）

| 编号 | 0.7.0 的功能 | 去向 |
|---|---|---|
| ADM-01 | 账号列表：姓名与账号、所属机构、角色、状态；机构管理员只看本机构，总部超管看全部 | `/admin/users` |
| ADM-02 | 开通账号：登录账号、姓名、归属机构、角色、初始密码；机构管理员只能开通本机构账号 | 保留；去掉“首次登录必须修改”的说明与强制标记 |
| ADM-03 | 编辑姓名与角色；不能改自己的角色；修改后目标账号的会话全部失效 | 保留 |
| ADM-04 | 重置密码（临时密码） | 保留；不再强制改密 |
| ADM-05 | 停用与启用；不能停用自己；最后一个超管受保护 | 保留 |
| ADM-06 | 管理原因：修改其他超管时必填，其余可选；写入审计 | 保留 |
| ADM-07 | 跨机构授权列表：账号、授权机构、访问范围、授权时间 | “跨机构授权”页签 |
| ADM-08 | 新增授权：三种范围（读取已发布成果、读取机构全部成果、进入机构维护工作区）及其适用限制；原因必填；同一账号同一机构再次授权即覆盖 | 保留 |
| ADM-09 | 撤销授权（带确认） | 保留 |
| ADM-10 | 身份审计：时间、操作人、操作、对象与机构、结果、详情与请求编号；每页 30 条；仅总部超管 | `/admin/audit`，另加筛选（P-18） |
| ADM-11 | 没有管理权限时的说明页 | 统一的无权限状态 |
| 新增 | 机构目录管理 | `/admin/orgs`（§9.1） |

### B.6 系统设置（`/settings` → `/admin/model`、`/admin/backup`、`/admin/system`、`/admin/demo`）

| 编号 | 0.7.0 的功能 | 去向 |
|---|---|---|
| SET-01 | 统一智能服务：状态；服务地址、模型名称、接口密钥（保存后不回显）；清除密钥；保存；验证连接。非管理员只读；跨机构运维时不能验证 | `/admin/model` |
| SET-02 | “AI 参与业务的方式”六张说明卡，各带跳转 | 不保留说明卡；跳转由导航承担（附录 A.3） |
| SET-03 | 演示资料面板：加载或恢复、归档、准备台账查询草稿、查看演示资产、查看模拟台账、验证路线、三个示例问题（可复制）、六个样本文件下载、资料清单。写操作限本机构管理员；下载与查看对所有角色开放 | `/admin/demo`，旧组件原样嵌入 |
| SET-04 | 集成应用入口（仅管理员） | 管理区导航 |
| SET-05 | “当前运行方式”说明 | `/admin/system` |
| SET-06 | 服务状态：平台服务（健康检查）、模型服务；另有两行写死为“已启用” | `/admin/system`；写死的两行改为真实检查或不显示 |
| SET-07 | 导出工作区备份（仅管理员；导出当前工作区机构） | `/admin/backup` |
| SET-08 | 恢复说明（恢复只能用命令行离线执行） | `/admin/backup`（P-08） |
| SET-09 | Windows 便携运行说明与“查看平台发行包”链接 | `/admin/backup`（P-17） |
| SET-10 | 导出程序源码包（仅总部超管） | `/admin/backup` |

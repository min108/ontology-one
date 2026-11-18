# 给实施模型的工作守则

## 1 项目与轮次

R4（1.3.0）语义内核补齐，分 A、B 两段交付与验收：范围、裁决 D1–D10 与 A 段动作清单见 `docs/blueprint/rounds/R4-PLAN.md`，任务书为蓝图 §14.4（只做 `SCOPE-TIERS.md` 的基础部分）。界面上位依据仍为 `docs/design/UI-BASELINE.md` 与 `docs/design/benchmarks/`；执行按 `docs/blueprint/EXECUTION-RULES.md` v3（质量第一；交付前独立对抗审查；动作清单逐条断言结果并用反例证明断言能失败；快速层每次推送都运行、不可跳过；真实模型经界面连续 3 次），经验见 `docs/blueprint/LESSONS.md`。门禁接入后汇报主线必须照常走通（R4-PLAN D2）。R4-A 首次独立验收不通过（`R4-ACCEPTANCE.md`）：内核合格，门禁接入主线与旧入口需修复；当前任务是 **R4-A.1 定向修复**，范围、裁决 D11–D16、动作清单 AB1–AB16 与验收门见 R4-PLAN 第 7 节，验收材料见 `docs/blueprint/rounds/r4a1-materials/`（不得改写）；守则新增 §2 第 20、21 条。A.1 复验通过后才启动 B 段。所有人默认简洁视图。环境模型仅在 `ONTOLOGY_MODEL_FROM_ENV=1` 时覆盖机构配置，只限演示或单机构部署；数字核验规则不放宽。

知元（英文名 Ontology One）是集团本体管理平台。R3（1.2.0）经 R3.1 修复后由 Claude 复验通过（`R3-ACCEPTANCE.md` 第 6 节）。R2（1.1.0）已验收，交接见 `docs/blueprint/rounds/R2-REPORT.md`；CI结论按报告列出的各实际受验提交分别核对；R3原交付两次完整层均失败，修复后按执行守则D1定向补验，不称原交付完整层全绿。R1（1.0.0）已通过独立验收；交付情况见 [STATUS](docs/blueprint/STATUS.md)、[R1 报告](docs/blueprint/rounds/R1-REPORT.md) 与 `docs/blueprint/rounds/R1-PLAN.md` 第 11 节；报告中的规范验证代码提交与最终文档交付提交分别记录。进入任何 worktree 先读 VERSION 并核对起点；默认 main 是旧 demo，不能作为开发基线。

## 2 硬规则摘要

完整定义见 `docs/blueprint/BLUEPRINT.md` 0.3；本轮已确认裁决见 R1-PLAN §3。

- G1：保留会话、CSRF、默认拒绝策略、机构隔离、审计触发器、原文锚点、技能隔离、外部源边界与 MCP。
- G2：保留数据、编号、历史和关联；entities 保持 12 列，relations 保持 7 列。
- G3：权限与只读边界在服务端强制，提示词不承担安全职责。
- G4：页面、助手与 MCP 共用同一业务能力实现。
- G5：模型输出需校验与采纳，发布需独立审核。
- G6：数字和结论可回溯；演示、模拟与真实执行明确区分。
- G7：新界面只用设计令牌，正文至少 14px，所有文字至少 12px。
- G8：名称、状态、动作来自词表；用平实业务语言。
- G9：运行期无公网 CDN、字体或遥测依赖；离线构建必须验证。
- G10：默认流程简单，新增必填项和跳转需说明理由。
- G11：每轮更新本文件、STATUS、计划、报告和 ADR。

## 3 代码地图

- R4-A：`kernel/kinds/metadata/elements/relations/cards/oxl/rules/gates` 统一种类、侧表、写入、派生关系、卡片、表达式与五项检查；新能力与 v1 共用内核。岗位/外部主体的检查适用性独立于业务资产分类。配方 A 在 `domains/build.py` 提出适用关系并从明确原文补条件，有外部编辑时必须显式版本确认；豁免只由独立审核人记录。`e2e/run_r4.py` 复用隔离生命周期，完整动作包含原 R3 用例；AA2 的 200 条中合成扩充与原始夹具必须区分。

- R2：`domains/build.py`复用原材料分段/提炼/采纳；`domains/reviews.py`保护新提案的独立审核；`domains/artifacts`从已完成会话生成私有版本化报告。`kernel/cards.py`生成确定性业务卡，`capabilities/elements.py`复用本地与联邦检索；`features/ontology`提供本体目录与统一要素页。`features/build`与`features/artifacts`为新主线页面，旧页继续保留。`DEEPSEEK_API_KEY`仅运行环境读取，设置与测试操作不得将环境密钥写入文件。

- R3 集成应用使用 `features/admin/integrations` 展示适配器提供共享骨架，仍复用旧业务体及原请求/授权/一次性令牌清理；旧路由和文件未退役。只在专业技术详情显示内部标识，普通视图按业务名称展示。

- `backend/main.py`：薄 API 装配；原内联路由已原样移到 api/v1/core.py。v2 入口在 api/v2，业务能力在 capabilities，配置/错误/编号/日志在 core。
- `backend/security/`：store、policy、middleware、context、applications、directory、preferences，承载身份、目录治理、会话、审计、应用授权和用户偏好。duties随会话读取并参与捕获身份/提交复核，仅存储不开放授予。
- R1-8 管理包：`features/admin` 与 `capabilities/model`、`capabilities/backup` 承接设置；`api/v2/system.py` 提供安全元数据和二进制绑定。浏览器15项后退役Settings/HarnessConfiguration及其专属CSS，DemoWorkspace继续保留。离线恢复仅可信offline绑定且持独占锁；模型新入口保持16KiB和严格拒重复JSON。主干完整回归与CI43832921234已通过，见R1-8-REPORT。
- R1-6 `features/home`：工作台使用真实同源统计、任务与近期运行；HomeIntake只在内存转交文件，RecentRecorder按身份/机构/scope记录有限本机元数据。`app/guardedNavigation.ts`沿用旧Authority未保存确认。O-01–06实际对照后退役Overview与41条专属CSS；旧素材/台账接收器保持人工确认上传；R2制度文件默认经零必填对话框进入配方A，文件仍只在内存转交，未保存确认先于上传。
- `backend/infra/`：版本化迁移、完整升级备份、可信恢复结构校验；`migrations/control`当前0001–0006，`migrations/tenant`当前0001–0004；恢复定义v1–v7按各库连续可信迁移历史精确选择，未知历史/部分结构拒绝。R4 只加侧表，旧四表行哈希保持不变。启动只校验、不自动升级。
- `backend/db.py`：MySQL 适配、连接池、机构初始化；`jobs.py`：任务队列；`maintenance.py`：维护锁。
- `backend/provenance.py`、`materials.py`：原文版本与出处；datasets、sources、skills、federation 等旧模块按所属轮次迁移。
- `backend/skill_harness*.py`、`backend/agent_*.py`、`backend/harness_plugin/`：受控 Harness 与现有应用执行。
- `frontend/src/`：design/ui/i18n 已包含令牌、18组件和生成词表；features/dev/gallery 仅开发构建开放。app 包已承载新六工作区外壳、路由、状态与请求层；原82文件整体移入legacy，旧App/main通过90项浏览器对照后退役，R1-8退役Settings、HarnessConfiguration与专属CSS，R1-6退役Overview，R1-7退役Security、Administration、auth.css后剩73；legacy/auth.tsx只留会话兼容桥。旧样式构建期加作用域，门户使用独立legacy-root。
- `frontend/src/features/{auth,account,admin,identity}`：新登录、账号安全、账号与授权、机构目录与审计；`state/preferences.ts`串行保存本人视图偏好，阻止迟到读取覆盖用户选择。12项真实操作对照后退役旧身份UI，原会话桥及共享CSS保留。
- `ops/`：Compose 开通、固定运行依赖、Windows 启停升级与技能运行器；`scripts/`：迁移、备份恢复、验收与打包。
- `e2e/run.py`：隔离装载真实0.7夹具、迁移、启停双服务及清理；check_results.py保留R1的210次历史汇总规格，R3各runner/results与最终CI逐项记录当前执行数和清理。R3由review_r3_baselines.py校验单汇总构件的63图及逐图审查，visual_baselines.py保留R1历史提取与共享校验；流程见e2e/CANONICAL-CAPTURE.md，像素差仅诊断。`ops/offline/`：准备依赖和可迁移镜像，在断网容器安装/构建/启动；`scripts/build_evidence.py`：显式收集并校验摘要，拒绝认证状态与符号链接。
- `tests/`：既有 unittest；`.github/scripts/`：独立的 16 个流水线脚本测试。
- `docs/blueprint/`：蓝图、进度、轮次记录与 ADR；`work/`：不入库的日志、临时数据与证据。
- e2e 已保留三角色 × 25 条旧路由基线；scripts/gen_api_types.py 从已登记能力生成 OpenAPI/TypeScript。terms/zh-CN.json 是品牌与文案维护源，scripts/gen_terms.py 同步前后端；R1 首页、身份和管理域已集成；最终浏览器、视觉及离线证据按报告所列的精确提交核对，历史包级通过不替代最终组合验收。

## 4 常用命令

使用 Python 3.12、Node 22、MySQL 8.4；前端单元测试环境的 Node 22 至少为 22.22.2，以满足已锁定的 jsdom/Vitest 要求（见 ADR-0001）。Windows 原生流程仍按受保护 workflow 的固定工具链独立验收，不据此修改其版本。先设置数据库环境变量，验收只连一次性测试实例，需有建库建用户及触发器权限。以下从仓库根目录执行，Python 命令显式使用已安装锁定依赖的虚拟环境，无须依赖当前 shell 是否激活。

安装依赖并准备开发实例：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
npm --prefix frontend ci
.venv/bin/python scripts/migrate.py --provision-all
.venv/bin/python scripts/bootstrap_admin.py --username group.admin
```

开发服务分别在两个终端启动；两条命令均持续运行，不要把它们串在后续检查脚本前。终端一：

```bash
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
```

终端二：

```bash
npm --prefix frontend run dev
```

验收检查在独立终端、一次性测试数据库中执行，环境前提见下方回归说明；构建后再检查产物的公网依赖：

```bash
RUN_HARNESS_SDK_TESTS=1 RUN_FEDERATION_MYSQL_TESTS=1 RUN_JOBS_MYSQL_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s .github/scripts -p 'test_*.py'
.venv/bin/ruff format --check backend scripts ops tests migrations .github/scripts e2e
.venv/bin/ruff check backend scripts ops tests migrations .github/scripts e2e
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend run lint:styles
npm --prefix frontend test -- --run
npm --prefix frontend run test:isolation
node --test e2e/*.test.mjs
.venv/bin/python scripts/checks/check_legacy_shrink.py
.venv/bin/python scripts/gen_terms.py --check
.venv/bin/python scripts/gen_api_types.py --check
.venv/bin/python scripts/checks/check_migrations.py
.venv/bin/python scripts/checks/check_policy_coverage.py
npm --prefix frontend run build
.venv/bin/python scripts/checks/check_offline.py --require-dist
```

8 个回归脚本及环境前提见 R1-PLAN §1.2 与 `r1-baseline/run_baseline.sh`；该历史脚本的退出码不聚合失败，必须逐项查 summary.tsv。真实 SDK 使用本地合成模型服务，需可写的 tmpfs；Linux 沙箱需明确允许写 `/dev/shm`，不得放宽代码检查或退回磁盘。完整结果要关联可能分行的用例名与状态，不能仅按单行正则计数。

## 5 实施约定

先写契约与测试，再实现。新增结构走迁移，接口同时登记默认拒绝策略，能力共用业务函数；新 INSERT 必须列出列名。新代码遵循类型、词表和令牌规范。旧 Python 告警按精确文件豁免，不运行 ruff check --fix；旧前端不重格式化。自 R2 起按 `EXECUTION-RULES.md`（R3 起为 v3）使用快速CI与最多2次完整CI，不重复本地全量；R3.1 仅一次完整层，失败按 D1 定向补验。任何代码推送均保留完整快速层，提交标记只能追加验收；新增结构/动作断言须用反例证明会失败。提交说明用对应轮次与包编号。并行最多 5 个子代理，不用最高推理档，各用显式起点的独立 worktree，格式化提交必须先合入。

## 6 易错点

- VERSION 是纯 X.Y.Z；同次提交同步 frontend package/lock、Compose 镜像默认值，以及 windows_upgrade 的 TO_VERSION/FROM_VERSIONS；保留已有升级来源。
- 本轮不改 requirements.txt（包括注释）；Windows 轮子锁验证其摘要。
- 不修改 windows-native.yml 和 publish-accepted-0.7.0.yml；不向 qa/publish-accepted-0.7.0 推送，不打任何标签，不建 PR。
- 主分支 main 不是基线。首次起点为规划提交 6e5a02c，代码基线为 1359118。
- 备份恢复有表白名单、原件绑定和可信结构版本；新增结构必须同步分类和对应恢复定义，保留旧版，不能放宽成列集合并集。服务启动不迁移；已有部署失败必须停止共享实例后离线完整恢复。
- 新机构开通失败为provision_failed，保留目录、审计和库；不自动DROP/启用/重试部分库。运行门例外只限新org-目录明确的provisioning/provision_failed状态，不能跳过active旧库。离线处置须先停止共享实例。
- 目录org_id可以为null（真实HQ旧数据），业务根按tenant id定位；版本快照两owner字段均为目标tenant id，不能沿用发起请求的工作区或混用业务org_id。目录/root/audit在同一跨库事务提交。
- H.2取消的是must_change_password的授权效力，兼容字段保留；会话/CSRF/只读/角色/机构/取消与实时duties检查不能随之删除。偏好不承载安全授权，审计不存偏好JSON。
- 两个打包脚本会排除名为 runtime/data/work/uploads 的目录；R5 的新 runtime 包需先调整规则。
- 产品中文显示名变更不影响仓库、包、Cookie、环境变量、库名前缀、Compose 项目名、MCP 或 Windows 文件等技术标识。
- 新Shell懒加载保留旧组件期间，所有异步URL写入必须校验固定路由所有权；NavigationProvider补via前校验真实浏览器完整URL。会话请求使用专用AbortController，旧401/409不能先触发全局副作用。
- Linux 的 39 个 Windows 原生跳过不能称为原生验收完成；真实模型密钥只从 DEEPSEEK_API_KEY 读取，不进入文件、日志、提交或截图。
- 视觉capture仅是生成，不能作为验收通过；先逐图打开审查，再将基线与compare模式同提交。真实125%使用官方完整Chromium的原生profile偏好，不能以headless-shell、pinch或模拟DPR替代。跨机离线包必须带images.tar，联网prepare使用--export-images。

## 7 禁止事项

见蓝图 0.3。不得通过删测试、缩小断言、放宽授权、跳过审核来过关。只有附录 H.2 明确取消行为的断言可按新规则改写，并逐条列入报告。不得删除尚未通过操作对照清单的旧页面。不得用模拟结果冒充真实模型或原生运行结果。

## 8 未规定事项

按蓝图硬规则、规范正文、任务书、已有受测行为的顺序裁决；仍无定义时采用权限更少、持久化更少、自动化更少且与现有命名一致的做法，并登记自行裁决。基线不绿、仓库不可达或无法推送时先报告，不绕过。负责人已经授权本次 tmpfs 环境修复；修复后完整基线已通过，详见 R1-PREFLIGHT。

# 给实施模型的工作守则

## 1 项目与轮次

知元（英文名 Ontology One）是集团本体管理平台。当前进行 R1（目标 1.0.0），实际进度见 `docs/blueprint/STATUS.md` 与 `docs/blueprint/rounds/R1-PLAN.md` 第 11 节。进入任何 worktree 先读 VERSION 并核对起点；默认 main 是旧 demo，不能作为开发基线。

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

- `backend/main.py`：薄 API 装配；原内联路由已原样移到 api/v1/core.py。v2 入口在 api/v2，业务能力在 capabilities，配置/错误/编号/日志在 core。
- `backend/security/`：store、policy、middleware、context、applications，身份、目录、会话、审计与应用授权。
- R1-8 管理包：`features/admin` 与 `capabilities/model`、`capabilities/backup` 承接设置；`api/v2/system.py` 提供安全元数据和二进制绑定。浏览器15项后退役Settings/HarnessConfiguration及其专属CSS，DemoWorkspace继续保留。离线恢复仅可信offline绑定且持独占锁；模型新入口保持16KiB和严格拒重复JSON。包内报告见R1-8-REPORT，待主干复验。
- `backend/infra/`：版本化迁移、完整升级备份、可信恢复结构校验；`migrations/control` 与 `migrations/tenant` 当前0001，恢复定义在 migrations/restore。
- `backend/db.py`：MySQL 适配、连接池、机构初始化；`jobs.py`：任务队列；`maintenance.py`：维护锁。
- `backend/provenance.py`、`materials.py`：原文版本与出处；datasets、sources、skills、federation 等旧模块按所属轮次迁移。
- `backend/skill_harness*.py`、`backend/agent_*.py`、`backend/harness_plugin/`：受控 Harness 与现有应用执行。
- `frontend/src/`：design/ui/i18n 已包含令牌、18组件和生成词表；features/dev/gallery 仅开发构建开放。app 包已承载新六工作区外壳、路由、状态与请求层；原82文件整体移入legacy，旧App/main通过90项浏览器对照后退役，剩80。旧样式构建期加作用域，门户使用独立legacy-root。
- `ops/`：Compose 开通、固定运行依赖、Windows 启停升级与技能运行器；`scripts/`：迁移、备份恢复、验收与打包。
- `e2e/run.py`：隔离装载真实0.7夹具、迁移、启停双服务及清理；`ops/offline/`：准备依赖并在断网容器安装/构建/启动；`scripts/build_evidence.py`：显式收集并校验摘要，拒绝认证状态与符号链接。
- `tests/`：既有 unittest；`.github/scripts/`：独立的 16 个流水线脚本测试。
- `docs/blueprint/`：蓝图、进度、轮次记录与 ADR；`work/`：不入库的日志、临时数据与证据。
- e2e 已保留三角色 × 25 条旧路由基线；scripts/gen_api_types.py 从已登记能力生成 OpenAPI/TypeScript。terms/zh-CN.json 是品牌与文案维护源，scripts/gen_terms.py 同步前后端；新业务前端域正在后续工作包集成，离线工具须在最终依赖上重新验证。

## 4 常用命令

使用 Python 3.12、Node 22、MySQL 8.4。先设置数据库环境变量，验收只连一次性测试实例，需有建库建用户及触发器权限。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
npm --prefix frontend ci
.venv/bin/python scripts/migrate.py --provision-all
.venv/bin/python scripts/bootstrap_admin.py --username group.admin
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
npm --prefix frontend run dev
RUN_HARNESS_SDK_TESTS=1 RUN_FEDERATION_MYSQL_TESTS=1 RUN_JOBS_MYSQL_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s .github/scripts -p 'test_*.py'
.venv/bin/ruff format --check backend scripts ops tests migrations .github/scripts e2e/run.py
.venv/bin/ruff check backend scripts ops tests migrations .github/scripts e2e/run.py
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend run lint:styles
npm --prefix frontend test -- --run
npm --prefix frontend run test:isolation
python scripts/checks/check_legacy_shrink.py
python scripts/gen_terms.py --check
python scripts/gen_api_types.py --check
python scripts/checks/check_migrations.py
python scripts/checks/check_policy_coverage.py
python scripts/checks/check_offline.py --require-dist
npm --prefix frontend run build
```

8 个回归脚本及环境前提见 R1-PLAN §1.2 与 `r1-baseline/run_baseline.sh`；该历史脚本的退出码不聚合失败，必须逐项查 summary.tsv。真实 SDK 使用本地合成模型服务，需可写的 tmpfs；Linux 沙箱需明确允许写 `/dev/shm`，不得放宽代码检查或退回磁盘。完整结果要关联可能分行的用例名与状态，不能仅按单行正则计数。

## 5 实施约定

先写契约与测试，再实现。新增结构走迁移，接口同时登记默认拒绝策略，能力共用业务函数；新 INSERT 必须列出列名。新代码遵循类型、词表和令牌规范。旧 Python 告警按精确文件豁免，不运行 ruff check --fix；旧前端不重格式化。每个包全量回归后提交，说明以 `[R1-编号]` 开头。并行最多 5 个子代理，不用最高推理档，各用显式起点的独立 worktree，格式化提交必须先合入。

## 6 易错点

- VERSION 是纯 X.Y.Z；同次提交同步 frontend package/lock、Compose 镜像默认值，以及 windows_upgrade 的 TO_VERSION/FROM_VERSIONS；保留已有升级来源。
- 本轮不改 requirements.txt（包括注释）；Windows 轮子锁验证其摘要。
- 不修改 windows-native.yml 和 publish-accepted-0.7.0.yml；不向 qa/publish-accepted-0.7.0 推送，不打任何标签，不建 PR。
- 主分支 main 不是基线。首次起点为规划提交 6e5a02c，代码基线为 1359118。
- 备份恢复有表白名单、原件绑定和可信结构版本；新增结构必须同步分类和对应恢复定义，保留旧版，不能放宽成列集合并集。服务启动不迁移；已有部署失败必须停止共享实例后离线完整恢复。
- 两个打包脚本会排除名为 runtime/data/work/uploads 的目录；R5 的新 runtime 包需先调整规则。
- 产品中文显示名变更不影响仓库、包、Cookie、环境变量、库名前缀、Compose 项目名、MCP 或 Windows 文件等技术标识。
- 新Shell懒加载保留旧组件期间，所有异步URL写入必须校验固定路由所有权；NavigationProvider补via前校验真实浏览器完整URL。会话请求使用专用AbortController，旧401/409不能先触发全局副作用。
- Linux 的 39 个 Windows 原生跳过不能称为原生验收完成；真实模型密钥只从 DEEPSEEK_API_KEY 读取，不进入文件、日志、提交或截图。

## 7 禁止事项

见蓝图 0.3。不得通过删测试、缩小断言、放宽授权、跳过审核来过关。只有附录 H.2 明确取消行为的断言可按新规则改写，并逐条列入报告。不得删除尚未通过操作对照清单的旧页面。不得用模拟结果冒充真实模型或原生运行结果。

## 8 未规定事项

按蓝图硬规则、规范正文、任务书、已有受测行为的顺序裁决；仍无定义时采用权限更少、持久化更少、自动化更少且与现有命名一致的做法，并登记自行裁决。基线不绿、仓库不可达或无法推送时先报告，不绕过。负责人已经授权本次 tmpfs 环境修复；修复后完整基线已通过，详见 R1-PREFLIGHT。

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

- `backend/main.py`：现有 API 装配与内联实体路由；R1-3 将内联部分原样移到 api/v1/core.py。
- `backend/security/`：store、policy、middleware、context、applications，身份、目录、会话、审计与应用授权。
- `backend/db.py`：MySQL 适配、连接池、机构初始化；`jobs.py`：任务队列；`maintenance.py`：维护锁。
- `backend/provenance.py`、`materials.py`：原文版本与出处；datasets、sources、skills、federation 等旧模块按所属轮次迁移。
- `backend/skill_harness*.py`、`backend/agent_*.py`、`backend/harness_plugin/`：受控 Harness 与现有应用执行。
- `frontend/src/`：0.7.0 外壳、页面与组件；R1-4/5 引入设计体系并把旧代码整体移入 legacy。
- `ops/`：Compose 开通、固定运行依赖、Windows 启停升级与技能运行器；`scripts/`：迁移、备份恢复、验收与打包。
- `tests/`：既有 unittest；`.github/scripts/`：独立的 16 个流水线脚本测试。
- `docs/blueprint/`：蓝图、进度、轮次记录与 ADR；`work/`：不入库的日志、临时数据与证据。
- 版本化 migrations、v2/capabilities、新前端域、e2e、离线工具链仍按 R1-PLAN 后续工作包建设；不存在的目录不能写成已完成。

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
.venv/bin/ruff format --check backend scripts ops tests .github/scripts
.venv/bin/ruff check backend scripts ops tests .github/scripts
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend run lint:styles
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
- 备份恢复有表白名单与列集合校验；机构库新增结构必须同步分类和恢复处理。
- 两个打包脚本会排除名为 runtime/data/work/uploads 的目录；R5 的新 runtime 包需先调整规则。
- 产品中文显示名变更不影响仓库、包、Cookie、环境变量、库名前缀、Compose 项目名、MCP 或 Windows 文件等技术标识。
- Linux 的 39 个 Windows 原生跳过不能称为原生验收完成；真实模型密钥只从 DEEPSEEK_API_KEY 读取，不进入文件、日志、提交或截图。

## 7 禁止事项

见蓝图 0.3。不得通过删测试、缩小断言、放宽授权、跳过审核来过关。只有附录 H.2 明确取消行为的断言可按新规则改写，并逐条列入报告。不得删除尚未通过操作对照清单的旧页面。不得用模拟结果冒充真实模型或原生运行结果。

## 8 未规定事项

按蓝图硬规则、规范正文、任务书、已有受测行为的顺序裁决；仍无定义时采用权限更少、持久化更少、自动化更少且与现有命名一致的做法，并登记自行裁决。基线不绿、仓库不可达或无法推送时先报告，不绕过。负责人已经授权本次 tmpfs 环境修复；修复后完整基线已通过，详见 R1-PREFLIGHT。

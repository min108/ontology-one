# R1 开工前复现记录

日期：2026-11-16。状态：环境调整后完整基线通过，允许按负责人指示继续 R1；本记录不代表 R1 工程完成。

## 起点与环境

- 开发分支：`dev/r1-foundation`，起点 `6e5a02ca1de4ed40d52575fea34a646c140ed1e6`。
- 代码基线：`1359118dd672431dcd0cde2eb81f2b0f994eef37`，`VERSION` 为 `0.7.0`；起点比代码基线仅多规划材料。
- 蓝图 SHA-256：`1587272bc4273d3b9dfab1ce4e9696ae65bce61c0d12db0af84991ed85792075`，与计划一致。
- Python 3.12.14、Node 22.23.4、MySQL 8.4.13、Docker 29.9.2。测试数据库为本轮新建的一次性实例。
- `requirements.txt` 原样安装，`pip check` 通过；技能镜像由仓库原有 Dockerfile 构建为 `ontology-skill-test:0.5.0`。
- 未调用真实模型。真实 SDK 测试使用本地合成模型服务。

## 首次差异与负责人指示

原样执行 `docs/blueprint/rounds/r1-baseline/run_baseline.sh`，三个测试开关均启用。后端原始结果：

```text
Ran 489 tests in 25.222s
FAILED (errors=17, skipped=39)
```

即 433 通过、17 错误、39 跳过。全部 17 个错误来自三个类的 `asyncSetUp`：`test_agent_sdk.AgentOfficialSDKTests`（6）、`test_harness_worker_modes.OfficialRuntimeModesTests`（4）、`test_skill_harness.OfficialSDKTests`（7）。共同链路为 `_work_root()` → `_tmpfs_root()` → `HARNESS_UNAVAILABLE`，测试主体尚未运行。

执行环境的默认文件系统权限使 `/proc/self/mountinfo` 中最长匹配的 `/dev/shm` 挂载显示为只读；既有安全检查因此拒绝运行。仅做写入试探不能代替挂载检查。

已按启动提示第四节第 2 条及第八节暂停开发并报告。负责人明确回复：**“仅修复测试环境，基线通过后继续（推荐）”**。随后仅为测试命令增加 `/dev/shm` 写权限，确认 `_work_root()` 成功，仍返回真正的 tmpfs。没有修改 `_tmpfs_root()`、改用磁盘临时目录、关闭 SDK 测试或放宽任何断言。

## 原逐条清单遗漏

`baseline-tests.txt` 实际包含 488 条：449 `ok`、39 `skipped`。首次本机日志可提取 489 条，原清单漏收：

```text
test_agent_sdk.AgentOfficialSDKTests.test_agent_only_exposes_fixed_selected_tool_and_calls_authenticated_bridge
```

本机测试集合包含原清单的全部 488 项，Windows 跳过集合一致。没有规划方原始日志，不能断定历史漏收原因。保留原清单，不把它改写为本机结果；后续同时核对 489 个实际用例、原清单全部项目及原始 unittest 汇总。

## 复现与判定方式

前提：Python 3.12、Node 22、MySQL 8.4 一次性测试实例、原技能测试镜像、具有写权限的 Linux tmpfs，以及计划要求的数据库连接环境变量。执行：

```bash
PY=.venv/bin/python OUT=work/evidence/r1/baseline \
  bash docs/blueprint/rounds/r1-baseline/run_baseline.sh
cat work/evidence/r1/baseline/summary.tsv
diff -u docs/blueprint/rounds/r1-baseline/baseline-tests.txt \
  work/evidence/r1/baseline/tests.txt
```

原脚本会在单项失败后继续，并不把失败聚合成最终非零退出码。因此必须检查 `summary.tsv` 全部 11 项退出码、489 个后端用例的原始结果、16 个流水线测试，以及逐条清单差异。已知清单漏项须独立说明；不得将其他差异归入该遗漏。

## 调整环境后的完整复现结果

代码仍为 0.7.0 基线，文档提交为 `649d9cc`。全部 11 个检查的退出码均为 0：

| 检查 | 结果 |
|---|---|
| 后端（3 个开关全部开启） | `Ran 489 tests in 94.854s`，`OK (skipped=39)`：450 通过、39 跳过 |
| 流水线脚本测试 | 16 通过 |
| restore_v020 / security / workflows / sources | 全部通过 |
| mcp / skills / entity_storage / agent_runtime | 全部通过 |
| 前端锁定依赖安装与构建 | 通过 |

原清单 488 项的状态全部一致，跳过集合完全一致。漏收的那个用例也通过；本次日志实际复现了漏收机制：asyncio 的 `Executing <Task ...> took ... seconds` 诊断插在用例名之后，`ok` 出现在下一行。将这两行按用例起止关联后，完整结果为 489 项（450 `ok`、39 `skipped`），与原始 unittest 总数一致。此说明不推定规划方原始日志内容。

本机原始日志在 `work/r1-preflight/baseline-fixed/`，另保存 `tests-complete.txt` 供格式化前后逐条比较；首次受环境阻碍的日志保留于 `work/r1-preflight/baseline/`。这些目录是本机过程证据，不能代替后续 GitHub Actions 证据构件或 Claude 的独立重跑。

到基线通过为止，未改变生产代码、测试、版本、运行依赖及两个既有 Windows 流水线。

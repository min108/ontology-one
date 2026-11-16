# ADR-0003：Harness 运行时边界与 R1 探针

- 日期：2026-11-16
- 状态：已决策；真实模型与 Windows 原生验证保留待执行
- 基线：`1090384`，VERSION `1.0.0`
- 范围：R1-10，仅探针、测试与文档，不改变生产实现和版本锁
- 可复现入口：`scripts/probes/harness/README.md`
- 机器可读结果：`scripts/probes/harness/evidence.json`

## 集成方式

继续采用官方 DeepSeek Harness，限定为一轮内的受控模型/工具循环。一轮一个受监管子进程，通过私有内存上下文和认证回环桥调用平台能力。会话记忆、上下文装配、计划、子任务、确认、持久化、恢复由平台负责；生产权限、只读、租户与审核边界继续由服务端执行。Harness 不获得终端、宿主文件、任意联网或外部 MCP 能力。探针的合成工具不实现业务保存，也不替代能力网关。

保留会话持久化关闭、环境清洗、进程回收与密钥不落盘。探针只放宽自己的 45 步/120 调用边界，生产 8 步/16 调用没有修改。CPU 25 秒、墙钟 90 秒、Node heap 256 MB 保持约束。本探针不能证明生产 900 秒长任务已经验收。

## 八项结论与预定决策

下表的“通过”均指 Linux 上真实官方 SDK 加本地合成模型服务；不是 DeepSeek 或其他真实模型的执行效果。锁定版为 `0.2.1rc2`，候选为执行时 PyPI 最新发布的预发布版 `0.2.2rc2`。每个单项命令在两种 Python 下执行，安装及命令见 README。

| 项 | 单项参数 | 锁定版 / 候选版实测 | 采用决策 | 不可用时的预定决策 |
|---|---|---|---|---|
| ≥40 步与取消 | `--case loop`、`--case cancel` | 两版均 40 次已完成工具调用、41 次模型请求；取消在模型请求到达后执行，运行时退出、PID 消失、线程回收 | 可用于后续运行时适配；继续使用进程级取消 | 降低单轮步数，编排器续跑；取消回收不可省略 |
| 并行工具 | `--case parallel`、`--case serial` | 两版 4 个只读工具实际最大重叠 4；4 个写入类合成工具最大重叠 1 | 只读工具显式 `isConcurrencySafe=true`，上限 4；写入串行 | 全部串行 |
| 上下文压缩 | `--case compaction` | 两版各 1 个 `compaction/summary`、1 个真实 surface replacement；同轮另 1 次缩减不足被拒绝，仍记录失败计数 | 具备压缩能力，后续可开启；必须保留结果句柄/摘要及容量控制 | 由平台仅回传结果句柄和摘要，不依赖压缩救活任意大上下文 |
| 失败重试 | `--case retry`、`--case auth` | 两版 503 → 成功共 2 请求；401 共 1 请求且不重试 | 只对模型瞬时错误启用有界重试；不重放写工具、不重试认证错误 | 编排器对整轮做一次重试，并保留业务幂等与确认边界 |
| 思考/推理开关 | `--case thinking`；`--live` | 替身收到 enabled；其余模式收到 disabled。真实 `DEEPSEEK_API_KEY` 缺失，两版真实执行均 pending | 当前保持关闭；待真实开关验证后按档位开启 | 关闭 |
| 文本增量 | `--case stream` | 两版观察到 2 个真实 SDK 文本增量；锁定版既有 public-progress 隐私过滤与“结束前到达”回归通过 | 沿用 public-progress；候选自己的 seam 通过不替代生产插件回归 | 不可降级；无法通过则不采用该版本 |
| 内网接口协议 | `--case protocol`；`--live --base-url … --model …` | 两版本地 `/v1/messages` + Messages SSE 可用；真实 HTTPS/内网端点没有执行 | 协议清单限下节；每个实际部署端点须单独验收 | 内网模型必须提供兼容当前 Messages 协议的端点 |
| 双平台摘要/升级 | `versions.py --version …` | 两版 Linux x64/Windows x64 wheel SHA256 与 PyPI 匹配，exe 摘要已计算；锁定版与仓库锁匹配。Windows 未原生运行。候选既有生产 SDK 用例被固定版本 guard 拒绝 | 维持 `0.2.1rc2`，不改任何锁 | 维持现有锁定版本 |

压缩探针先用短合成输入观测到 start/end 但没有 summary，证明“插件加载”或“开始/结束事件”不能判成功；最终测试要求 summary 的 shadowedSeqs 和独立 replace 事件。大合成历史允许成功替换，随后过小的可选片段仍会失败。该结果只证明机制可用，不证明真实摘要准确度或任意长上下文可压缩。

## 内网协议清单

| 协议/能力 | 已有证据 | 允许据此认定的范围 |
|---|---|---|
| Anthropic 风格 Messages，`POST /v1/messages`，SSE `message_start/content_block_*/message_delta/message_stop` | 两版真实 SDK、本地合成端点通过 | 工具调用、工具结果、文本增量的协议机制 |
| DeepSeek 官方 HTTPS Messages，默认 base `https://api.deepseek.com/anthropic` | 本轮无 DEEPSEEK_API_KEY | 待负责人执行；不宣称本轮真实可用 |
| 具体内网 HTTPS Messages 兼容端点 | 本轮未提供端点/密钥 | 每个端点按 README 命令复验，含思考开关 |
| OpenAI Chat Completions、Responses、其他模型私有协议 | 未测试 | 不列入兼容清单，不据此更换官方 Harness |

## 摘要与版本结论

| 版本 | SDK wheel SHA256 | Linux runtime wheel SHA256 | Windows runtime wheel SHA256 |
|---|---|---|---|
| 0.2.1rc2 | `30fc72c67f20ff4b46c6952087ea596fc70c2692001aa372185a24f9866bdc46` | `0a0a4eb85a22cd9339972e6a5ced77a27ec791353a5b220e3e10b8cdbbdeca39` | `d38c18f5117859feedaaa791a0eea37b8644bc187118e7e6f3afa231a5381cb7` |
| 0.2.2rc2 | `ad14f1b1fccb29f7d97e5a7e0ee9dbb96d8af11292308bba66bf19a623d31899` | `3c8f15a7a92b12857032995ec5dbcc105f192c36218e9b6bb2c5b54b4ee7db17` | `a791af95a97be205a73354c6819e95b5fe2ff40a57ef8a78d53c188ee33e9aea` |

| 版本 | Linux x64 executable SHA256 | Windows x64 executable SHA256 |
|---|---|---|
| 0.2.1rc2 | `b6f70327db1392b4ff879b8be26c87349b14933c1b4f7a90b391446bb005aae8` | `f9ed01f241df37c50c702d81b6c226d576210b8f3947699735ca90285ea13365` |
| 0.2.2rc2 | `1cc41d5a8745a5b9d47c8604e44f23eb8af2d8ca02b2afa0be101d5f71be6ea4` | `a3a56fad32059c85e520d892821e50ce5fe4ebcc88eac2c48839f4207efc8e39` |

这些是从下载文件实际计算并与发布元数据比较的摘要，不是仅转抄发布页。候选只安装在 `work/r1-10/0.2.2rc2/venv`。候选 SDK 要求 `pydantic>=2.12,<3`，私有环境实际安装 Pydantic 2.14.3 及对应依赖，已在探针安装器固定完整五项依赖清单以便复跑；生产 Pydantic 2.13.4 不变。候选结果属于该完整私有组合，不能外推成换入生产依赖后的验证。即使机制探针通过，也未满足候选完整生产契约/评测与 Windows 原生回归门，不能建议升级。生产 guard 的拒绝是预期锁定行为，不能解释为已证明候选 SDK 功能不兼容。

## 外部待执行与后续边界

负责人将 `DEEPSEEK_API_KEY` 注入安全进程环境后，运行 README 的两版 `--live` 精确命令；缺失变量退出码 2。实际内网端点的 URL/模型名必须由负责人替换，不能拿本地替身或其他名称的秘密替代。Windows 原生需在独立检出、锁定运行时及已安装项目依赖的 Python 上运行 `RUN_HARNESS_SDK_TESTS=1` 的既有 Harness 测试；候选升级另开审批范围并完整验证，R1-10 不改生产 guard 来跳过门禁。

## 自行裁决

1. 候选使用当日 PyPI 最新预发布 `0.2.2rc2`，明确标候选而非稳定升级建议。
2. 仅申请精准 `/dev/shm` 写权限恢复既有测试环境，不放宽生产 tmpfs 检查。
3. 单项输出使用布尔值/计数；真实正文、推理、提示词与异常原文均不进入证据。只读/写入并发的合成工具没有真实业务副作用。
4. 不为探针创建接口、迁移、持久化会话或新业务操作。后续集成要继续走共享能力函数、默认拒绝与独立审核。

# R1-10 Harness 技术探针报告

日期：2026-11-16。起点 `1090384`，分支 `wp/r1-10-probes`，VERSION `1.0.0`。范围为专属简报的 Harness 八项探针，没有混合检索、部署或业务功能扩展。R1-1 CI `43802818754` 成功后才开始编辑。

## 交付与结论

- `scripts/probes/harness/run.py`：真实官方 SDK、合成模型服务、独立受限插件、Linux 资源限制、取消回收与安全汇总；10 个可单独重跑案例覆盖运行时项目与反例。
- `scripts/probes/harness/versions.py`：两版 Linux/Windows wheel 下载、实际 SHA256 复核、exe 摘要、仓库锁复核、候选私有虚拟环境安装。
- `scripts/probes/harness/README.md`：两版单项、全套、真实模型、内网端点、原生 Windows 与既有回归命令。
- `scripts/probes/harness/evidence.json`：20 条官方 SDK + 合成模型最终通过结果、4 条真实模型 pending、两版三个 wheel/两个 exe 的摘要及候选依赖组合。
- `tests/probes/test_harness_probe.py`：13 条 discover 可收集的新契约测试；没有改写/删除既有断言。
- `ADR-0003-harness-probe.md`：集成边界、全部八项结论与预定决策、内网协议清单和版本结论。

两版都完成 40 次工具调用、41 次模型请求；取消在请求到达后发生，真实 SDK runtime PID 消失；只读最大并发 4、写入类合成工具最大并发 1；503 一次重试，401 不重试；两段文本增量可见。压缩各有 1 次 summary 和 1 次真实 surface replacement，同时另 1 次缩减不足失败被保留为失败计数。最终结果不隐去失败事件。

替身只验证 SDK 的思考配置传输和 Messages SSE 机制。`DEEPSEEK_API_KEY` 缺失，真实 DeepSeek 与真实内网模型均未调用；没有尝试其他环境变量里的密钥。两版的 `--live` 都明确返回 pending，当前脚本退出码为 2。候选原生产 SDK 用例被固定版本 guard 拒绝；没有修改 guard，也没有声称候选完整既有套件已经通过。候选环境用 Pydantic 2.14.3，生产仍为 2.13.4，完整五项候选依赖版本已固定在探针安装器。Windows wheel 校验不是 Windows 原生运行。

**版本裁决：维持 0.2.1rc2。** 后续可据探针采纳 40 步能力、受控并行、有限重试和压缩；思考开关等待真实验证；文本增量不得降级；压缩失败仍走平台结果句柄/摘要。生产配置本轮没有改变。

## 验证与证据

最初先添加 10 个契约测试，缺少实现模块时如期报 ImportError；实现后通过。随后增加摘要失败反例、实际 surface replacement 判据与 wheel 篡改检测，总数 13。没有将 start/end 当成压缩成功，也没有将“到时杀掉启动中 worker”当成取消成功。

| 检查 | 命令/条件 | 最终结果 |
|---|---|---|
| 全量后端 | 三个 opt-in 旗标均为 1，`python -m unittest discover -s tests -v` | 505 tests，OK，39 个既有 Windows 原生跳过；238.640 秒 |
| 新增契约 | `python -m unittest tests.probes.test_harness_probe` | 13 tests，OK |
| 既有流水线脚本 | `python -m unittest discover -s .github/scripts -p 'test_*.py'` | 16 tests，OK |
| 格式与 lint | `ruff format --check backend scripts ops tests .github/scripts`；`ruff check` 同目录 | 全部通过；没有使用 check --fix |
| 两版机制探针 | 两版 Python 分别 `scripts/probes/harness/run.py --case all` | 每版 10 案例通过，总 20，退出码 0 |
| 双平台摘要 | 两版 `versions.py --version …`，候选另加 `--install` | 六个 wheel 实际摘要与 PyPI 一致；四个 exe 摘要已计算；锁定版仓库摘要复核通过 |
| 真实模型 | 两版 `run.py --live` | 四项 pending；无密钥、无真实调用 |
| 候选生产门禁样本 | 既有 `OfficialSDKTests.test_real_sdk_multi_step_authoring_only_exposes_two_tools_and_disables_upload` | 1 test 被固定版本 guard 拒绝；保留生产锁、不称升级通过 |

本地未提交的完整日志在当前 worktree `work/r1-10/`：`full-regression-final.log`、`pinned-regression.log`、`candidate-production-gate.log`；原始模型内容没有写入这些探针日志。候选拒绝日志仅保留既有测试异常与代码位置。机器可读安全证据已入库，不依赖这些本地日志才能复跑。

完整后端测试使用独立库前缀 `ontology_r1_10_final`、MySQL `127.0.0.1:3306`，密码由授权预检文件只读入内存并作为子进程环境传入，不显示、不落新文件。启用 `RUN_HARNESS_SDK_TESTS=1 RUN_FEDERATION_MYSQL_TESTS=1 RUN_JOBS_MYSQL_TESTS=1`。tmpfs 使用 `/dev/shm/ontology-r1-10`；首次未授予该路径写权限时既有检查拒绝运行，取得精准写权限后重跑，没有软链、磁盘替代或更改检查。

## 外部待执行

负责人在安全环境注入 `DEEPSEEK_API_KEY` 后，于仓库根目录运行：

```bash
.venv/bin/python scripts/probes/harness/run.py --live
work/r1-10/0.2.2rc2/venv/bin/python scripts/probes/harness/run.py --live
.venv/bin/python scripts/probes/harness/run.py --live \
  --base-url https://model.intranet.example/anthropic --model deployment-model
```

最后一条必须替换为实际获准端点与模型名；当前缺少这些条件，不能提供虚假的成功命令输出。不得将密钥放进命令参数、日志或文件。Windows 原生完整命令见 README 的 PowerShell 代码块。候选正式升级必须另行通过生产契约/评测和两平台原生门禁；本轮只出结论。

## 自行裁决与交接

1. 无真实密钥时返回 pending，优先保留关闭思考的预定决策，不以合成效果代替。
2. 候选选 PyPI 当日最新预发布版并明确版本属性，隔离安装，固定实测依赖组合；不改生产依赖。
3. 压缩既验证成功也保留失败计数；机制可用仍不保证真实摘要质量或全部输入都能压缩。
4. 不新增业务页面、必填项、跳转、接口、迁移和权限；不持久化模型正文、推理或提示词。
5. 不修改 `requirements.txt`、两条保护 workflow、运行时锁、生产代码或既有测试；不 push、PR、tag。AGENTS/STATUS/R1-PLAN 由 root 统一整合，本包提供新增探针路径、测试数量、待执行项目与 ADR。

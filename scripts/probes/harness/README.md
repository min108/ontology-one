# R1 Harness 探针

从仓库根目录运行。生产文件、版本锁和 `requirements.txt` 均不改。探针使用官方 SDK、自己的受限插件和本地合成模型服务，不是实际模型效果评测。

```bash
PY=/workspace/ontology-one/.venv/bin/python
$PY -m unittest tests.probes.test_harness_probe
$PY scripts/probes/harness/run.py --case all
$PY scripts/probes/harness/versions.py --version 0.2.1rc2
$PY scripts/probes/harness/versions.py --version 0.2.2rc2 --install
work/r1-10/0.2.2rc2/venv/bin/python scripts/probes/harness/run.py --case all
```

其他检出使用其已安装锁定依赖的 Python 设置 `PY`。候选安装只发生在本检出的 `work/r1-10/0.2.2rc2/venv`，wheel 缓存同目录。联网下载时同时验证 PyPI SHA256；缓存文件也每次复核。锁定版额外比对仓库 Linux wheel、SDK wheel、Linux/Windows executable 摘要。Windows wheel 下载和解包校验不等于 Windows 原生执行。

候选私有环境依赖固定为本次实际安装的 Pydantic 2.14.3、pydantic_core 2.50.4、annotated-types 0.8.0、typing_extensions 4.16.0、typing-inspection 0.4.5，具体清单在 `versions.py`；它们不改变生产 Pydantic 2.13.4。因此候选证据属于这一完整私有组合，不能直接推断换入生产依赖后的结果。

单项命令：`$PY scripts/probes/harness/run.py --case NAME`。NAME 为 `loop`（40 工具调用、41 模型请求）、`cancel`（请求到达后关闭并回收 runtime）、`parallel`（4 只读工具）、`serial`（4 写入类合成工具）、`compaction`（summary 与真实 surface replacement）、`retry`（503 后一次成功）、`auth`（401 不重试）、`thinking`（仅检查替身收到的开关）、`stream`（文本增量）、`protocol`（Messages 路径与 SSE）。候选用其独立 Python 重跑每个命令。

`run.py` 的退出码：0 为本次所有项通过；1 为不可用/超时/运行失败；2 为真实模型条件未满足。输出是每行一个 JSON，只含类型、计数、布尔值与版本；不输出模型回答、请求、推理正文、异常原文或密钥。各项失败需按 ADR-0003 的预定决策处理，不得删掉失败行。

真实模型只从当前进程的 `DEEPSEEK_API_KEY` 读取。未设置时准确输出 pending，且不启动 worker、不发网络请求。不要把密钥写进命令参数、命令历史、文件或日志；由负责人已有的安全环境注入后运行：

```bash
$PY scripts/probes/harness/run.py --live
work/r1-10/0.2.2rc2/venv/bin/python scripts/probes/harness/run.py --live
# 对已获准的具体内网端点，以实际 HTTPS 地址替换示例：
$PY scripts/probes/harness/run.py --live --base-url https://model.intranet.example/anthropic --model deployment-model
```

真实运行包含 thinking 开启与关闭（protocol 案例）。开启项同时要求观察到 reasoning/thinking 内容块；只返回正文不能证明推理开关生效。即使通过，也仅说明该端点/模型当次兼容，不能外推到其他内网模型。密钥通过父子进程 stdin 内存通道传递，SDK 仅通过清洗后的子进程环境获取，不进入补丁或持久化会话。进程失败只返回异常类名。

资源约束为每项墙钟 90 秒、每个 worker/其继承子进程 CPU 25 秒、Node heap 256 MB、最多 45 步和 120 工具调用。临时目录在本检出 `work/r1-10`，正常结束清理；强制超时可能留下不含密钥/会话的补丁目录，允许人工清理。会话持久化、会话外发、内置终端/文件工具、任意网络工具、外部 MCP 与插件清单关闭。唯一自写网络回调是固定回环计数端点，并用一次性随机令牌认证。探针不连接业务库。

Linux 既有 SDK 回归需要真正可写的 tmpfs，保留原有检查：

```bash
mkdir -p /dev/shm/ontology-r1-10
ONTOLOGY_HARNESS_TMP_ROOT=/dev/shm/ontology-r1-10 RUN_HARNESS_SDK_TESTS=1 \
  $PY -m unittest tests.test_skill_harness tests.test_harness_worker_modes tests.probes.test_harness_probe
```

沙箱必须有 `/dev/shm` 写权限。不得以普通磁盘、软链或修改检查替代。候选生产回归仍被既有版本/摘要锁拒绝；本轮不更改这些锁来制造通过。升级须另行完成完整运行时契约与模拟评测，以及 Windows 原生回归。

Windows 原生待负责人在安装好锁定依赖的独立检出运行（PowerShell）：

```powershell
$env:RUN_HARNESS_SDK_TESTS = '1'
.venv\Scripts\python.exe -m unittest tests.test_skill_harness tests.test_harness_worker_modes
```

`run.py` 本身是 Linux 资源/进程探针；下载 Windows wheel 的摘要校验可在 Linux 重跑，不能替代上述原生执行。

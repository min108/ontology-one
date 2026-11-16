# R1-5 浏览器验收协议

当前状态：已执行多轮本地 foundation 验收并如实保留失败证据，见下表；整轮通过只以对应 runner/result 为准。每轮使用明确集成 SHA 与单个构建/浏览器执行窗口。旧 UI 75 条的 canonical CI 基准不重复运行。

## 基准与证据入口

已完成的 canonical 基准由 CI [43811372862](https://github.com/min108/ontology-one/actions/runs/43811372862) 提供：Playwright 1.65.1、Chromium 159.0.8154.6（v1255），旧界面 75 条及真实升级恢复演练通过。
原[总证据产物](https://github.com/min108/ontology-one/actions/runs/43811372862/artifacts/14201046591) 132740554 bytes，SHA-256 `a4ec24fd13bedd3340fd322a0ec712dbe141dce45735defadb36e07d2dc81117`。这是既有 CI 记录，不是本分支重新执行的结果。

以后 CI 保留总产物，另外上传：

- `r1-browser-summary-<run>`：runner/environment/results JSON 和明确列出的服务、构建、Playwright 日志。
- `r1-legacy-screenshots-modeler-<run>`、`reviewer`、`admin`：各 25 张旧路由截图，无 report/results 的重复附件。
- 每包 `index.json` 记录源相对路径、字节数、SHA-256 和总产物链接。分包源文件合计须不超过 30 MiB，预留 ZIP 与索引开销以满足 32 MiB 下载限制；超限必须再次拆包，不能截断证据。

认证态仍只放 runner 私有 `work`，退出清理。拆包仅接受固定清单，不收任意 JSON、不收 trace 或 HTML；发现 storage-state 内容、认证头或符号链接则失败。失败运行的摘要如有缺失文件，索引只列实际存在的文件，不能据此声称验收通过。

本地用历史 75 条结果验证了拆包，未重跑浏览器：summary 240344 bytes，modeler 14381708、reviewer 14824325、admin 14638624 bytes。该本地历史浏览器为 Chromium 156.0.8078.79，与 canonical CI 分开记录。

## 定位与测试矩阵

公共定位协议已与 Shell 负责人确认：

| 对象 | 定位 |
| --- | --- |
| 一级导航 | `navigation`，精确名称 `工作区导航`；子链接按 href |
| 二级导航 | `navigation`，精确名称 `工作区内导航`；子链接按 href |
| 机构 | `combobox`，精确名称 `机构`；实现 id `active-workspace` |
| 机构确认 | `dialog` 名称 `切换机构`；按钮 `确认切换` / `取消` |
| 旧规范标题 | `getByLabel('规范名称', {exact:true})` |
| 后台任务 | `details.harness-activity > summary[aria-label^="后台任务"]` |
| 智能处理对话 | `region` 名称 `智能助手处理对话`（旧源码是带名称的 section） |
| 收起处理对话 | button 名称 `收起智能处理对话` |
| 停止处理 | 对话内 button 名称 `停止本轮处理` |

`foundation-shell.spec.mjs` 共 14 条：

1. modeler/reviewer/admin 各验证六个一级 href；点击后精确比较二级链接、活动项和默认落点。集团入口以实际 `session.whoami` 的 `federation.read` 判定；用户/集成管理只对 admin 出现；未完成的机构/审计页面不提前曝光。
2. 三角色各测 1366×768 和 1093×614，后者是 125% **等效 CSS 视口**，不声称操作过浏览器原生缩放。检查页面横向溢出、导航与机构选择可见、真实链接可点击；截图写 `shell-screenshots/<role>`。最终视觉产物须按页面/视口分组继续执行 30 MiB 预算。
3. admin 验证 `/security`、`/administration`、`/settings`、`/settings#demo` 四条入口，同时保留 query/hash。
4. superadmin 从总部经真实 UI 建立独立草稿，修改规范名称，尝试切到办公厅。取消后字段仍可编辑且内容、地址、服务端机构不变，workspace POST 为零；再确认后只有一次 POST，整页回 `/`，服务端机构为 dept-01，旧编辑器消失。原夹具 authority 是 published，不能冒用为可编辑样本；新草稿随本 runner 精确库清理。

原 `legacy.spec.mjs` 不删减，foundation 执行同一 75 条并检查 `data-legacy-page`，总共 89 条。legacy 目标只选择原文件，不使用 skip 跳过新文件。`--list` 只核对发现数量，不等同于运行通过。

```bash
# 先 rebase 到负责人明确提供的集成提交；提交测试后才能由 runner 导出正确 HEAD。
python e2e/run.py --target foundation --api-port 8785 --agent-port 8786
# 只诊断 Shell 时可附 -- foundation-shell.spec.mjs；该结果不能替代完整 89 条。
```

CI 不设置 `E2E_CHROMIUM`，使用固定 Playwright 浏览器。本地 CDN 403 时只用已授权的 `E2E_CHROMIUM=/usr/bin/chromium`，记录实际版本，不绕过网络限制。CI 已添加完整 foundation --harness 运行与 summary/三角色/superadmin/SDK 小包。页面89条先执行，依赖的SDK项目单worker随后执行，避免并行截图拖过真实业务模型deadline。

## 真实 SDK 与全局浮层

此项使用独立的重型执行窗口，已在本地实际通过（完整轮次仍有导航失败，见执行记录），不以假请求、预置 running/cancelled 行或页面注入事件代替。实现沿用 `tests/test_skill_harness.py::OfficialSDKTests` 和 `tests/test_agent_sdk.py::AgentOfficialSDKTests` 的真实固定 SDK、本地 Messages SSE 服务与显式 hold/release 同步方式。

1. 用同一隔离 runner 装载数据、统一迁移、启动真实 API 和 agent。新增本地模型替身绑定 127.0.0.1 随机端口；仅替换模型供应方网络端，不 mock 业务 API、SDK、进程执行器或任务仓库。先确认 Harness tmpfs 工作目录可用。所有配置仅在本次 namespace/storage；合成密钥在内存生成，不输出、不截图。
2. 通过真实 `/api/skills/harness/config` 和 `/test` 配置并验证本地 Messages 服务；验证阶段返回合法 SSE，然后切到 hold 模式。以 admin 真实会话向 `/api/harness/tasks` 提交 `operation: 'ai_ask'`、`entity_id: ''`、`body: {question: '合同金额大于100000元时，应核实什么审批依据？', mode: 'online'}`。先以同一问题调用真实 retrieval 并断言 citations 非空（无命中时业务会直接返回检索结果，不调用模型）。必须观察本地模型收到真实 SDK 请求，才进入 UI 断言；仅见任务 accepted 不算 SDK 执行证据。
3. 页面打开后台任务 summary，按该真实 run id 对应链接进入。验证 `region` 对话、公开进度和停止按钮可见；检查其最近 `.legacy-root` 隔离宿主存在且行高/排版不被新设计上下文污染，1366 与等效 125% 各留单独截图。只记录合成任务文本，不记录请求头、原始 prompt 或模型密钥。
4. 点击“收起智能处理对话”，断言 region 消失且后端 run 仍 running；通过另一一级导航离开，再从顶部后台任务打开同一 run，断言重挂载与 SSE 公开进度正常。这区分“关闭查看”与“停止执行”。
5. 点击真实“停止本轮处理”，观察对应 `/api/harness/runs/<id>/cancel` POST 成功，后端最终为 cancelled，UI 显示已停止且停止按钮消失。同步记录本 runner agent 派生的 SDK 进程精确 PID，确认回收；释放本地服务 hold，finally 关闭模型服务、两个服务进程并按 runner 现有精确名单清库。不能仅凭 UI 显示停止就声称进程已清理。
6. 证据写明 `model_provider: local_synthetic_messages`、固定 SDK/浏览器版本、run id、状态序列、是否收到真实模型请求、取消 HTTP 状态、进程回收结果及截图。称为“真实 SDK + 本地合成模型”，不称真实公网模型。失败或尚未执行保持明确状态。

`python e2e/run.py --target foundation --harness` 显式增加第 90 条真实 SDK 浮层用例。不带此参数仍为89条；CI使用--harness完整90条且按89→1调度。`harness_fixture.py` 管理 loopback provider 与原配置的恢复，记录 agent 后代 PID/starttime，拒绝替换已有模型密钥；finally 在双服务结束后恢复原文件或删除本次新增配置。独立凭据仅在内存/子进程环境中传递，浮层用例关闭 trace。仅连接验证通过、筛选未执行浮层测试或缺进程回收证据都不能得到 --harness 成功。轻量 HTTP/SSE/配置恢复/进程身份单测不属于实际 SDK 或浏览器验收证据。


## 本地诊断执行记录（不是 canonical 最终验收）

以下均由隔离 runner 实际执行，浏览器是本机 Chromium 156.0.8078.79，Node 22.23.4。每次结果目录保留 runner/environment/results 与截图；不能把不同提交的通过数合并为某个提交的整轮通过数。

| 源码提交 | 实际范围与结果 | 证据目录（本 worktree 的 work/evidence/r1 下） |
| --- | --- | --- |
| e89e08ea5bf2d386eff6e28037e0aceb2f1570f1 | 86/89；旧集成页两角色被新路由拦截，机构虚拟列表无法直接定位 | foundation-shell-first |
| 42e14727a1445a6b0537aa20f9788476f0ac3de1 | 定向 7/10；SDK问题无资料命中、空态新建按钮定位二义性、Graph离场返航 | foundation-focused-harness |
| b3fe18c64c81afd0896039ab56d3ebb02af055cc | 87/90；旧75全过、真实SDK与机构切换过；两例Graph返航、一例二级导航读取早于渲染 | foundation-r1-5-full |
| e85ebd549fbf5396265b209e3bb710dfed281bb3 | 89/90；仅admin等效125的Graph返航失败 | foundation-r1-5-verified |

上述失败轮均无重试/跳过，4/4本次创建数据库精确清理、双服务停止、认证态移除。`foundation-r1-5-verified` 是预先选定的目录名，runner 状态仍为 failed，不能按目录名认定通过。

b3/e85 的真实 SDK 用例均单独通过：真实固定 SDK 0.2.1rc2，本地合成 Messages；先检索到3条实际夹具出处，连接验证与业务任务共2次模型请求；任务依次 running → 收起查看仍running → UI重新打开 → cancel HTTP200 → cancelled。e85观察到agent后代PID 167544/167572，并验证已回收。浮层1366/等效125与停止后截图均保留。此结果不等于真实公网模型验收。

### via 返航的因果记录

对 e85 的 admin 等效125失败，只提取了trace中的时间和路径，没有复制认证trace本体或请求头：

| 相对trace时钟(ms) | 实际事件 |
| --- | --- |
| 35233 | 点击一级导航 `/build` |
| 35427 | 主页面快照URL已为 `/build` |
| 35444 | 首次返航 `/graph?via=<visit>`，尚无vx/vy/vz |
| 36072 | 随后才写入vx/vy/vz |
| 36551 | 再次更新vx/vy/vz |

因此，不能仅以最终URL含viewport参数就认定Graph动画是首因。初次返航仅补via，导航提供器在新浏览器URL与旧render Location并存时的规范化副作用也必须覆盖；Graph固定挂载归属与导航提供器真实全URL检查需联合验证。测试保留快速离场，没有添加等待动画结束的延时来规避竞争。

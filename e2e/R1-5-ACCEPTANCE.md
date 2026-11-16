# R1-5 浏览器验收协议

当前状态：测试与证据拆包工具已准备。foundation Shell 尚待集成提交，本文不是浏览器通过记录。执行前登记集成 SHA，并获得单个构建/浏览器执行窗口。旧 75 条已在 CI 通过，不因本次编写协议而重复运行。

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

CI 不设置 `E2E_CHROMIUM`，使用固定 Playwright 浏览器。本地 CDN 403 时只用已授权的 `E2E_CHROMIUM=/usr/bin/chromium`，记录实际版本，不绕过网络限制。此处尚未把 foundation 执行加入 CI：等待 Shell 集成后由负责人安排。

## 真实 SDK 与全局浮层：待实现/待执行流程

此项需要独立的重型执行窗口，当前没有通过结果，也不以假请求、预置 running/cancelled 行或页面注入事件代替。实现沿用 `tests/test_skill_harness.py::OfficialSDKTests` 和 `tests/test_agent_sdk.py::AgentOfficialSDKTests` 的真实固定 SDK、本地 Messages SSE 服务与显式 hold/release 同步方式。

1. 用同一隔离 runner 装载数据、统一迁移、启动真实 API 和 agent。新增本地模型替身绑定 127.0.0.1 随机端口；仅替换模型供应方网络端，不 mock 业务 API、SDK、进程执行器或任务仓库。先确认 Harness tmpfs 工作目录可用。所有配置仅在本次 namespace/storage；合成密钥在内存生成，不输出、不截图。
2. 通过真实 `/api/skills/harness/config` 和 `/test` 配置并验证本地 Messages 服务；验证阶段返回合法 SSE，然后切到 hold 模式。以 admin 真实会话向 `/api/harness/tasks` 提交 `operation: 'ai_ask'`、`entity_id: ''`、`body: {question: '合成验收问题', mode: 'online'}`。必须观察本地模型收到真实 SDK 请求，才进入 UI 断言；仅见任务 accepted 不算 SDK 执行证据。
3. 页面打开后台任务 summary，按该真实 run id 对应链接进入。验证 `region` 对话、公开进度和停止按钮可见；检查其最近 `.legacy-root` 隔离宿主存在且行高/排版不被新设计上下文污染，1366 与等效 125% 各留单独截图。只记录合成任务文本，不记录请求头、原始 prompt 或模型密钥。
4. 点击“收起智能处理对话”，断言 region 消失且后端 run 仍 running；通过另一一级导航离开，再从顶部后台任务打开同一 run，断言重挂载与 SSE 公开进度正常。这区分“关闭查看”与“停止执行”。
5. 点击真实“停止本轮处理”，观察对应 `/api/harness/runs/<id>/cancel` POST 成功，后端最终为 cancelled，UI 显示已停止且停止按钮消失。同步记录本 runner agent 派生的 SDK 进程精确 PID，确认回收；释放本地服务 hold，finally 关闭模型服务、两个服务进程并按 runner 现有精确名单清库。不能仅凭 UI 显示停止就声称进程已清理。
6. 证据写明 `model_provider: local_synthetic_messages`、固定 SDK/浏览器版本、run id、状态序列、是否收到真实模型请求、取消 HTTP 状态、进程回收结果及截图。称为“真实 SDK + 本地合成模型”，不称真实公网模型。失败或尚未执行保持明确状态。

这条流程尚未实现 runner 的模型替身生命周期与进程观测，不能算在 89 条的成功数内。完成实现与实际执行后另补专门结果，不把设计文本当证据。

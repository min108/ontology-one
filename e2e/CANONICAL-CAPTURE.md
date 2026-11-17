# R3 规范视觉基线：一次重拍、拼图审阅、结构验收

依据 `R3-PLAN` D8/D9 与 `EXECUTION-RULES` v3。R1 的 33 个小包、逐张打开和零像素差门
已被本轮裁决替代；历史提取工具保留供历史证据核对。本轮只下载一个 CI 汇总构件，
视觉差异用于诊断，结构、功能、字体、溢出、无障碍和证据完整性仍是硬门。

## 1. 稳定界面后生成一次规范矩阵

先完成相关动作定向验证与审查修复。在干净集成提交里设置
`e2e/visual-policy.json` 为 `{"mode":"capture","reason":"R3 D8 canonical baseline refresh; not final acceptance"}`，
提交说明含 `R3 U9 [R3-FULL]`，推送 `dev/r3-ui-foundation`。记录实际 40 位 `CAPTURE_SHA`
及 `CAPTURE_RUN`；两次完整层预算的第一次用于此步。capture 不是最终验收。

本次第一次完整层 `44013937536` 在旧运行记录权限回归中提前失败，尚未执行视觉矩阵。
修复保留原回归断言；使用单独的 `[R3-VISUAL]` 提交只执行尚未运行的规范矩阵，
完整层预算仍为两次。该定向任务只生成基线，不代替最终完整验收，也不重复已运行的全量测试。

CI 的 `Reviewed 63-state canonical visual matrix` 使用固定 Playwright Chromium **159.0.8154.6**，
不设 `E2E_CHROMIUM`。矩阵仍为 3 个布局（1366×768、1920×1080、1366×768 原生 125%）
×（登录 + 简洁/专业各 10 页）= **63 图**。实际 SDK、全部旧路由及管理操作在独立夹具中运行，
不能用截图代替动作结果。每项保留精确源码提交与清理结果。

原生缩放仍要求 DPR 1.25、inner 约 1093×614、visualViewport.scale=1，真实 profile zoom preference；
PNG 是完整物理视口 1366×768。通过 CDP `Page.captureScreenshot` 取完整视口，不传 clip/scale，
不得用 CSS 缩放、裁图或本地 Chromium 156 冒充规范截图。完整右/下边界必须可见。
审计图固定原夹具 id=11，筛选并打开详情；仅专业视图展开技术详情，不遮审计内容。

## 2. 下载一个汇总构件并核验

用 GitHub 工具读取该 run 的元数据，核对 `head_sha == CAPTURE_SHA`，再读取 artifact 列表，
下载唯一 `r3-ci-summary-CAPTURE_RUN`。将 GitHub 返回的 artifact id、下载 URL 与 `digest`
写入 `work/canonical/CAPTURE_RUN/download.json`，不要把本地计算值充当预期摘要。

下载 ZIP 放在同目录，核对 SHA-256 与 GitHub digest 相等。安全解包至新目录：拒绝绝对路径、
`..`、反斜杠、重复成员和符号链接；只提取 `r3/visual/visual/` 下的 `actual.png`、`metrics.json`、
`axe.json`，以及必要的结果摘要。不得执行压缩包内容。源目录记为 `VISUAL_SOURCE`。

```sh
python e2e/review_r3_baselines.py review \
  --source "$VISUAL_SOURCE" --commit "$CAPTURE_SHA" \
  --review "work/canonical/$CAPTURE_RUN/review.json"
```

该命令校验精确 63 状态、源码 SHA、规范浏览器、原生缩放/PNG 尺寸、截图方法及 axe 严重问题，
并生成全部 `reviewed=false` 的清单。任何缺失、错误或失败不得先标成通过。

## 3. 拼图审阅并逐图记录

按布局/页面把截图做成每张不超过 **12 格** 的审阅拼图，保留页面名称和视图标签，实际查看每格；
发现细节疑问时打开原图。检查字体、标题/操作/业务行完整、首屏负担、旧页保留行为、
1366/1920/原生 125% 布局与完整边界。另将本轮改动页的 1366 图与六张设计基准并排，
差异与原因写入 R3 报告附表。拼图只留在 `work/`。

在实际查看后逐项填写 `notes` 和 `reviewed=true`，填写实际审阅者 `reviewer`。
不得脚本直接把未查看的 63 项全设为通过，不得修改每图摘要来掩盖不一致。

## 4. 入库与 compare 同一提交

```sh
python e2e/review_r3_baselines.py stage \
  --source "$VISUAL_SOURCE" --commit "$CAPTURE_SHA" \
  --review "work/canonical/$CAPTURE_RUN/review.json" \
  --output "work/canonical/$CAPTURE_RUN/staged"
```

stage 只接受新目录，产生 63 PNG、63 原始 metadata 与 review.json。用该目录替换
`e2e/visual-baselines/`，保留本轮下载元数据以关联 GitHub 来源；移除不再对应本轮的 R1 下载清单。
**同一提交**把 policy 恢复 `{"mode":"compare"}`，报告与 STATUS 引用实际生成 SHA/run。
推送第二次 `R3 U9 [R3-FULL]` 提交，作为交付完整层。禁止第三次完整层或零像素差循环。

compare 缺图、无效 PNG、axe 严重问题、缩放或结构违规仍失败；像素变化只记录诊断。
最终以交付提交 CI 中真实执行的功能、迁移恢复、浏览器、结构和断网安装结果为准。
真实 DeepSeek 三次连续界面演练单独记录渠道、模型、环境、提交与时间，不用模拟 CI 替代。

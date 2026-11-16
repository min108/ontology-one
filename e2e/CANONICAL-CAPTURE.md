# R1 规范视觉基线：生成、下载、逐张审查、严格比较

这是 root 执行的收口步骤，当前不表示验收已通过。不得把 capture 成功算作最终通过。
不使用 workflow dispatch、隐藏 API 或改默认分支；通过已经授权的 dev 分支正常提交触发 CI。

## 先核对集成契约

最终 `ci.yml` 的浏览器范围如下，全部使用 Playwright 1.65.1 / Chromium 159.0.8154.6。
每个 scope 都是独立随机空库；core 内部按依赖顺序执行。
原生缩放视觉启动固定官方完整 Chromium `channel: "chromium"`，不使用 headless shell；
本地 `E2E_CHROMIUM` 可覆盖可执行文件作诊断，但 capture 明确拒绝该覆盖。

| scope / 证据目录（在 `work/evidence/r1/` 下） | 精确数量 | 项目 |
|---|---:|---|
| `e2e-legacy-ci` | 75 | 原角色 × 25 条旧路由，原源码 `1359118` |
| `e2e-legacy-replacements-ci` | 5 | 旧 Overview / Login / Security / Administration / Settings |
| `e2e-foundation-ci` | 106 | pages 89 → home 4 → identity 12 → SDK 1 |
| `e2e-admin-ci` | 15 | 独立配置 / 备份 / 系统 / 演示操作及 provider 生命周期 |
| `e2e-visual-ci` | 9 个测试、63 张图 | 登录 3 张 + 10 页 × 3 视口 × 2 视图 |
| 合计 | 210 个测试 | 全部 0 unexpected / 0 flaky / 0 skipped / 0 retry |

`e2e/check_results.py` 逐一检查项目数、每个测试唯一编号及单次 passed 结果，核对两份结果摘要，
新程序必须是当前 HEAD、旧程序必须是固定 `1359118`。还检查规范浏览器版本、完整 63 图矩阵、
实际 SDK 结果与子进程回收、全部精确新建库已清理、两服务已停和认证私有目录已删。
汇总产物是 `r1-acceptance-summary-RUN` 中的 `browser-acceptance.json`。
R1-5 的历史 90 项证据保持原样，不替代这里的 210 项最终范围。

审计截图采用真实夹具 id=11、action=`demo_loaded`、resource=`business-demo-0.7`、tenant=`dept-01`、
outcome=`success` 的唯一历史记录。测试通过真实 UI 精确筛选，断言 HTTP 响应、对象和编号，展开详情；
这 6 张审计图不遮时间、请求编号或正文。最新记录、分页及新增修改的实时审计由 identity 12 项验证。
其它页面仅精确遮瞬时时间 / 请求编号，不遮业务行。随机库前缀不显示于这些页面；模型 SDK 版本由锁定依赖确定。

## 1. 专门生成提交

前提：Home 4、identity 12、admin 15 的实际 A.2 已通过，已处理确认的动态布局问题，集成工作树干净。
不要修改依赖、业务断言、截图阈值或单测试 deadline 来获取图片。

```sh
git status --short
python - <<'PY'
import json
from pathlib import Path
Path('e2e/visual-policy.json').write_text(json.dumps({
    'mode': 'capture',
    'reason': 'R1 canonical visual baseline bootstrap; not final acceptance'
}, indent=2) + '\n')
PY
git add e2e/visual-policy.json
git commit -m '[R1-9] Capture canonical visual baseline, not final acceptance'
git rev-parse HEAD
```

root 按既有授权推送这个提交，记下完整 `CAPTURE_SHA` 和实际 `CAPTURE_RUN`。等待同一 SHA 的
foundation 与 offline 两个 job 成功；核对 `browser-acceptance.json` 的上述精确数量、source SHA、
cleanup，以及 63 张图的 native zoom / axe 元数据。capture 下 `foundation_gates_passed=false`，
每张图与 visual runner 均标明 `baseline-capture-not-acceptance`。这不是功能或无障碍豁免。
任何测试、axe、字体、溢出、原生缩放失败都要保留真实失败并修复后重新生成。

## 2. 用现有 GitHub 工具下载并核对 ZIP 摘要

下载这些 **33 个**小 artifact，每个名字为 `r1-visual-LAYOUT-PAGE-RUN`：

- LAYOUT：`1366`、`1920`、`1366-native125`。
- PAGE：`login`、`home`、`shell-legacy`、`security`、`users`、`orgs`、`audit`、`model`、`backup`、`system`、`demo`。

不要下载超过 32 MiB 的完整证据来替代小包。另取 `r1-acceptance-summary-RUN`、`r1-visual-summary-RUN`、
`r1-evidence-manifest-RUN` 和 `r1-offline-evidence-RUN`；后者证明同 SHA 实际断网安装、构建、迁移及启动。
跨机断网重现另取完整 `r1-offline-dependencies-RUN`（不受小包下载规格约束）：CI 已用
`prepare.py --export-images` 导出 `images.tar`，同时包含工具与 MySQL 镜像。完整解包后，
在同架构 Docker 主机检出记录的 SHA，按 REPRODUCE 中 verify 命令验证；工具先核 manifest
再 docker load 镜像，无需联网拉取临时 runner 的镜像 ID。不要把仅有证据的小包当作依赖包。
旧 5 图可取该 run 的 `r1-replacement-PAGE-RUN` 供 A.2 对照，放在独立 `old/` 目录，不混进以下 33 包。

每个 ZIP 保存到 `work/canonical/RUN/downloads/`。从 GitHub artifact 元数据复制其摘要，
不要把本地 ZIP 自算摘要填写为“预期摘要”。生成待填目录：

```sh
export CAPTURE_SHA='填入40位实际生成提交'
export CAPTURE_RUN='填入实际run编号'
python - <<'PY'
import json, os
from pathlib import Path
from e2e.visual_baselines import LAYOUTS, PAGES
run = os.environ['CAPTURE_RUN']
folder = Path('work/canonical') / run / 'downloads'
folder.mkdir(parents=True, exist_ok=True)
rows = []
for layout in LAYOUTS:
    for page in ('login', *PAGES):
        name = f'r1-visual-{layout}-{page}-{run}'
        rows.append({'name': name, 'file': name + '.zip', 'sha256': ''})
(folder / 'artifacts.json').write_text(json.dumps({
    'capture_commit': os.environ['CAPTURE_SHA'], 'run_id': run, 'artifacts': rows
}, indent=2) + '\n')
PY
```

将每个工具下载文件复制成对应 `file` 名，将其 GitHub 摘要填入 `sha256`（允许 `sha256:` 前缀）。
然后执行本地校验与安全解包；空摘要、错 SHA、错 run、少包、超限或危险路径都会失败：

```sh
python e2e/visual_baselines.py extract \
  --catalog "work/canonical/$CAPTURE_RUN/downloads/artifacts.json" \
  --output "work/canonical/$CAPTURE_RUN/extracted" --capture-commit "$CAPTURE_SHA"
python e2e/visual_baselines.py verify \
  --source "work/canonical/$CAPTURE_RUN/extracted" --capture-commit "$CAPTURE_SHA"
```

第二层校验逐个包的 `index.json`，验证所有 PNG/metrics/axe 的字节数和 SHA-256、完整链接、
33 包 / 63 状态、规范浏览器与源码、DPR/visualViewport/PNG 尺寸以及无严重 axe 违规。
不得将本地 Chromium 156 或旧 CSS 等效缩放图片改名导入。
PNG 必须是完整物理视口：1366×768（包括真实125%）或1920×1080，绝非CSS inner尺寸。
125%仍须真实DPR1.25、inner约1093×614、visualViewport.scale=1。标准CDP整视口
Page.captureScreenshot不传clip/scale、不用Emulation；metadata方法为cdp-physical-viewport，
screenshotScale为physical。普通Playwright device截图虽1093×614却裁掉右/底边，css又缩成
874×491，均拒绝入库；逐图必须检查完整右/下边界。仅白名单动态标记临时白遮罩，audit为空；
caret/动画临时处理及CDP连接在成功和失败后均恢复/释放，不对PNG裁切或缩放。

## 3. 逐张查看并记录实施方逐图审查

```sh
python e2e/visual_baselines.py review \
  --source "work/canonical/$CAPTURE_RUN/extracted" --capture-commit "$CAPTURE_SHA" \
  --review "work/canonical/$CAPTURE_RUN/review.json"
```

生成的 63 行全部 `reviewed=false`。逐张用图片查看工具打开：
`extracted/r1-visual-LAYOUT-PAGE-RUN/visual/LAYOUT/MODE--PAGE/actual.png`；登录状态名是
`login--unauthenticated`。填明 `reviewer`（例如 Codex/root 或实际实施方审查者），每张实际查看后再将该行设为 true 并写具体 `notes`。
核对字体、完整标题/操作/业务行、原页面保留行为、1366/1920/真实125%布局、无横向溢出，
以及背景任务/旧页面视觉隔离。问题图片不可先接受后忘记修。

不要脚本批量把 63 行标记为已审查。脚本只验证记录完整性，不能替代实际查看。
每行保留 PNG 摘要、包 index 摘要、完整证据链接；不得手改这些值绕过不一致。

## 4. 入库并同提交恢复 compare

```sh
python e2e/visual_baselines.py stage \
  --source "work/canonical/$CAPTURE_RUN/extracted" --capture-commit "$CAPTURE_SHA" \
  --review "work/canonical/$CAPTURE_RUN/review.json" --output e2e/visual-baselines
python - <<'PY'
import json
from pathlib import Path
assert len(list(Path('e2e/visual-baselines').glob('*.png'))) == 63
Path('e2e/visual-policy.json').write_text(json.dumps({'mode': 'compare'}, indent=2) + '\n')
PY
git add e2e/visual-baselines e2e/visual-policy.json
git diff --cached --stat
git commit -m '[R1-9] Accept reviewed canonical baselines and require exact comparison'
git rev-parse HEAD
```

目标目录必须新建或为空，避免悄悄覆盖旧审查结果。stage 输出 63 PNG、逐图原始 metadata、
review.json 与下载摘要目录（如使用上面的 extract）。root 正常推送这个 compare 提交，
最终以 **该精确集成 SHA** 的 foundation+offline 两 job 全绿为准；经负责人批准，截图比较 `threshold=0.05`、
`maxDiffPixels=0`，缺图不自动生成。最终报告列出 run、SHA、两个 job、各 scope 数量、33 小包链接、
manifest/归档摘要、真实离线和迁移恢复证据。未运行、失败或未审查项目一律不得写成通过。

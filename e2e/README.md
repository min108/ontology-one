# 浏览器回归

使用 `tests/fixtures/v070` 的真实 0.7.0 合成夹具和真实后端，不拦截业务请求。
`legacy.spec.mjs` 覆盖三种既有角色 × 25 条保留路由，含全部详情地址。
先在 `1359118` 的界面上运行，随后在新外壳上运行同一组测试。

```bash
E2E_TARGET=legacy E2E_BASE_URL=http://127.0.0.1:8765 \
  frontend/node_modules/.bin/playwright test --config e2e/playwright.config.mjs
E2E_TARGET=foundation E2E_BASE_URL=http://127.0.0.1:8765 \
  frontend/node_modules/.bin/playwright test --config e2e/playwright.config.mjs
```

夹具装载、迁移和服务启动由演练脚本完成；测试本身不创建机构或放宽角色权限。
可用 `E2E_FIXTURE_MANIFEST` 指定同结构清单、`E2E_EVIDENCE_DIR` 指定证据位置。
`E2E_CHROMIUM` 只用于诊断已有浏览器；正式视觉基线和 CI 使用 Playwright 锁定的 Chromium。
依赖从前端的单一锁文件加载；独立诊断可用 `E2E_DEPENDENCY_ROOT` 指定相同版本的安装目录。

`work/e2e-auth/` 内是临时合成账户会话，只用于运行测试，不进入证据包或 Git。
证据构件只收测试结果、浏览器版本记录和截图。

## 一条命令装载、启动和回归

先安装 Python 运行依赖、前端锁定依赖及 Playwright 对应 Chromium，并让 Node 22 位于 PATH。CI 检出使用 `fetch-depth: 0`，确保历史提交 `1359118dd672431dcd0cde2eb81f2b0f994eef37` 可供 `git archive` 导出。

```bash
python e2e/run.py --target legacy
python e2e/run.py --target foundation
```

`MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD` 只提供连接凭据。runner 自行生成随机 `e2e_r1_*` 命名空间，忽略外部 `MYSQL_DATABASE` 与 storage 选择；拒绝 `ONTOLOGY_MYSQL_URL`，也没有指定库名的命令参数。建立任何库之前确认所有目标库不存在。退出时先终止本次 API/agent 进程组，再按实际创建的精确库名清理；不会按前缀执行 DROP，也不会清理预先存在的库。

每次在 `work/e2e-runs/<随机名>/` 导出源码：legacy 固定使用 0.7.0 原始提交，foundation 使用当前 HEAD（须先提交要验证的应用变更）。两个目标都对各自源码执行 `npm ci` 与完整前端构建，使用原结构 loader 载入夹具；foundation 随后执行统一 `scripts/migrate.py`。API 与 agent 均使用真实服务、独立 storage，worker 关闭，健康检查通过后才执行浏览器测试。

端口已被使用时可换一组；集成前可显式指向另一个 worktree 的原始夹具：

```bash
python e2e/run.py --target legacy --api-port 8785 --agent-port 8786 \
  --fixture /path/to/tests/fixtures/v070
```

`E2E_TARGET` 和 `E2E_DEPENDENCY_ROOT` 仍有效；浏览器依赖默认来自当前检出的 `frontend`，不在历史源码中增加测试依赖。`E2E_CHROMIUM` 指向本机浏览器时，报告记录实际浏览器版本；CI 不设置该变量，以使用锁定的 Playwright Chromium。

`--evidence` 或 `E2E_EVIDENCE_DIR` 可指定本检出 `work/` 内的新空目录。默认报告位置为 `work/evidence/r1/e2e-<目标>-<随机名>/`，包含源码提交、实际测试数、服务日志与精确数据库清理记录。认证状态在该次私有 work 目录中生成，并于退出时移除；不会放入证据目录。失败、零测试、跳过和重试均不能得到成功结果。`-- --grep ...` 可用于诊断筛选，报告记录筛选参数及实际测试数，完整验收运行不加筛选。

## Shell 与可读取证据

R1-5 的定位协议、14 条新增 smoke、等效缩放说明及真实 SDK 浮层待执行流程见 [R1-5-ACCEPTANCE.md](R1-5-ACCEPTANCE.md)。`legacy` 只发现原 75 条；`foundation` 发现 75 + 14 条，实际通过须看 runner 的执行报告。

CI 保留总证据，并增加摘要及三角色独立截图产物，每份源内容限 30 MiB。可对既有结果拆包而不重跑浏览器：

```bash
node --test e2e/*.test.mjs
E2E_COMPLETE_ARTIFACT_URL=https://github.com/OWNER/REPO/actions/runs/RUN/artifacts/ID \
  node e2e/evidence.mjs work/evidence/r1/e2e-legacy-ci work/e2e-ci-packages
```

目的目录必须不存在；每包索引列出实际包含文件、SHA-256 与总产物链接。只收明确列出的 JSON/日志和角色 PNG，不收临时认证态、trace、HTML 或重复附件。


完整 foundation + SDK 验收使用 `python e2e/run.py --target foundation --harness`。
Playwright 先运行 `foundation-pages`（89条），成功后单独以1个worker运行依赖项目 `harness-sdk`（1条）。
CI采用同一命令与调度。页面项目失败导致SDK依赖不执行时，整体仍失败；不放宽模型业务deadline，也不以重试/跳过获得成功。
指定 `--grep` 或 `--project` 时Playwright仍可能运行完整依赖项目；诊断若显式使用 `--no-deps`，只代表该次筛选结果，不能作为全套90条验收。

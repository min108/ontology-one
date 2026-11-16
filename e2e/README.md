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

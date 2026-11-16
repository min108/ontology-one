# R1 离线构建与启动检查

联网准备、断网验证是两个独立阶段。依赖、工具镜像和源码均有摘要；验证不调用在线安装源，也不禁用应用 lifespan 或数据库初始化。

```bash
python ops/offline/prepare.py --output work/offline/r1
python ops/offline/verify.py --bundle work/offline/r1 --evidence work/evidence/r1/offline
```

`prepare.py` 构建含 Python 3.12、Node 22 与 Noto CJK 字体的工具镜像，下载 requirements.txt 全部轮子（含 Harness SDK/运行时），以前端锁文件生成 npm 缓存，并打包运行源码/本地静态资源。镜像的实际内容 ID 记入 `images.json` 和 `manifest.json`。需要把包转交另一台完全离线的 Linux Docker 主机时，加 `--export-images` 将镜像也写进包；验证会从该归档加载镜像。镜像和轮子按准备主机架构生成，跨架构须分别准备。

企业代理使用 `--ca-bundle /path/to/trusted-ca.pem`（默认读取 `SSL_CERT_FILE`）。证书只挂载到联网下载容器，不会禁用 TLS 校验，也不包含在依赖包中。

`verify.py` 先校验每个包文件及本地依赖锁，再启动 `--network none` 的一次性 MySQL 8.4 容器。应用工具容器共享该容器的网络命名空间：两者只有回环接口，可以真实访问 MySQL，不能访问宿主或公网。容器中再次断言没有默认路由、公网 TCP 连接失败，然后实际执行：

1. `pip install --no-index --find-links=/bundle/wheels -r requirements.txt`；
2. `npm ci --offline` 和前端完整类型检查/构建；
3. 构建产物公网地址扫描、有效 API 路由策略检查；
4. 数据库迁移、完整 uvicorn 应用 lifespan 启动；
5. HTTP 健康检查及由后端提供的前端 HTML 检查。

输出 `offline.log`、`server.log`、`isolation.json`、`offline-result.json`。只有全部通过才有成功结果文件；容器始终清理。MySQL 为独立临时实例，测试口令只经进程环境传递。

修改任何依赖后，用新的输出目录重新准备；不得手工更新 manifest 来使旧包通过。仅源码变化时可加 `--reuse-dependencies <旧包目录>`，工具先验证旧包全部摘要，并要求 requirements、前端 package/lock、VERSION 和工具 Dockerfile 完全相同，才复用轮子与 npm 缓存；仍会重新打包当前源码。前端迁移或运行源码变化后也应重新准备，包内源码快照和摘要是实际受测版本。R1 的范围是构建安装与启动骨架，不声称已完成 R10 全产品离线功能验收。

## 检查脚本与证据包

```bash
python -m unittest discover -s tests/checks -v
python scripts/checks/check_policy_coverage.py
python scripts/checks/check_offline.py --require-dist
python scripts/build_evidence.py \
  --input work/evidence/r1/ci \
  --input work/evidence/r1/offline \
  --steps work/evidence/r1/reproduce.txt
```

证据脚本默认生成 `work/evidence/r1/package/`、`package.tar.gz` 及归档摘要；每个文件在 manifest 中记录大小和 SHA-256。输入必须显式指定，缺失、空证据集、重复目标、符号链接与浏览器认证状态均失败；已有非空输出拒绝覆盖。脚本仅收集实际结果，不自动判定验收通过。CI 应上传整个 `work/evidence/r1/`，在报告登记 run ID、artifact 名和归档 SHA-256（P-11）。

策略检查使用 FastAPI 的 `iter_route_contexts` 展开惰性嵌套路由，包括不出现在 OpenAPI 中的路由。普通业务路径必须逐方法出现在 `READ_PATHS` / `WRITE_RULES`；参数名可不同，静态路径不可借参数模板获准。既有控制接口和受控代理 catch-all 在 `policy_control_routes.json` 精确登记，新增接口不能借控制前缀过关。运行时默认拒绝探针覆盖全部现有角色与六种方法。

公网地址扫描覆盖 backend、前端源码、public、入口 HTML、dist（包含 JS/CSS/SVG/JSON）。文档注释、XML/SVG 命名空间不代表访问；私网/回环和已登记的内网示例允许。模型默认地址仅允许既有配置位置与打包产物；P-17 发行包地址仅允许既有设置页及其打包产物的 href，不允许用于 fetch/src。新增例外须有具体用途和违规自测，不能整目录豁免。

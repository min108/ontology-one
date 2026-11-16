# ADR-0001 技术选型定版

状态：已采纳（工程工具链与 R1-4 设计体系依赖已定版）　日期：2026-11-16　相关轮次：R1

## 背景

遵循蓝图 §3.2。0.7.0 的运行依赖和 Windows 轮子锁保持不变；本轮不改 requirements.txt。

## 备选方案与取舍

保留 Python 3.12、FastAPI/Pydantic v2、MySQL 8.4、React 19、TypeScript、Vite 和 React Router 7。未引入 ORM、消息中间件或独立搜索服务。开发工具单独锁定，避免改变 Windows 分发校验。

## 决定

| 项 | 固定版本 / 规则 | 许可 |
|---|---|---|
| Python / Node | 3.12 / 22；CI 使用这两条版本线 | Python-2.0 / MIT |
| Ruff | 0.17.5；行宽 120；仅 format 机械改写旧代码 | MIT |
| pytest | 9.2.0；既有 unittest 保持原命令 | MIT |
| ESLint / @eslint/js | 10.15.0 / 10.0.1 | MIT |
| typescript-eslint | 8.72.2；新代码禁止无说明的 any | MIT |
| Prettier | 3.10.1；行宽 110 | MIT |
| Stylelint / standard config | 18.0.0 / 41.0.0 | MIT |

按精确文件记录旧 Python 告警豁免；新路径执行完整选定规则（E、F、I、UP、B、SIM、C4、RUF、ANN；包括类型标注）。中文标点列入明确的 allowed-confusables。没有运行 ruff check --fix。旧前端文件按清单豁免，在 R1-5 搬入 legacy 后继续隔离。

设计体系采用下面的精确版本。React 与既有依赖保留 R1-1 锁文件实际版本；新增依赖按 2026-11-16 npm latest 稳定标签核验后固定，不使用范围版本。

## 影响

开发环境须先装 requirements.txt，再装 requirements-dev.txt。CI 的真实 SDK 检查采用本地合成模型服务，要求可写的真实 Linux tmpfs；已验证的 Harness runtime 文件须可执行。两者是不同路径的要求，不要求为会话 tmpfs 去除 noexec；不能把磁盘目录冒充内存目录。依赖缓存属于后续离线依赖包的输入，R1-9 完成离线构建验证。

## R1-4 前端依赖、兼容性与许可

全部直接依赖记录于 `frontend/dependency-licenses.json`，精确解析与完整性摘要以 `frontend/package-lock.json` 为准。Node 22.23.4 满足新 Vitest（22.12+）与 jsdom（22.22.2+）的要求；CI 的 Node 22 必须达到 22.22.2。

| 包 | 固定版本 | 许可 | 范围 |
|---|---|---|---|
| `@ant-design/x` | 2.10.0 | MIT | 运行 |
| `@antv/g6` | 5.1.1 | MIT | 运行 |
| `@tanstack/react-query` | 5.107.1 | MIT | 运行 |
| `@xyflow/react` | 12.12.0 | MIT | 运行 |
| `antd` | 6.7.0 | MIT | 运行 |
| `diff` | 9.0.0 | BSD-3-Clause | 运行 |
| `echarts` | 6.1.0 | Apache-2.0 | 运行 |
| `lucide-react` | 0.468.0 | ISC | 运行 |
| `react` | 19.3.0 | MIT | 运行 |
| `react-dom` | 19.3.0 | MIT | 运行 |
| `react-markdown` | 10.1.0 | MIT | 运行 |
| `react-router-dom` | 7.18.4 | MIT | 运行 |
| `recharts` | 2.15.4 | MIT | 运行 |
| `rehype-sanitize` | 6.0.0 | MIT | 运行 |
| `remark-gfm` | 4.0.1 | MIT | 运行 |
| `zustand` | 5.1.0 | MIT | 运行 |
| `@eslint/js` | 10.0.1 | MIT | 开发 |
| `@playwright/test` | 1.65.1 | Apache-2.0 | 开发 |
| `@testing-library/dom` | 10.4.2 | MIT | 开发 |
| `@testing-library/jest-dom` | 7.0.1 | MIT | 开发 |
| `@testing-library/react` | 16.3.3 | MIT | 开发 |
| `@testing-library/user-event` | 14.6.12 | MIT | 开发 |
| `@types/node` | 22.19.15 | MIT | 开发 |
| `@types/react` | 19.3.0 | MIT | 开发 |
| `@types/react-dom` | 19.3.0 | MIT | 开发 |
| `@vitejs/plugin-react` | 4.7.0 | MIT | 开发 |
| `eslint` | 10.15.0 | MIT | 开发 |
| `jsdom` | 31.0.1 | MIT | 开发 |
| `openapi-typescript` | 7.13.0 | MIT | 开发 |
| `postcss-prefix-selector` | 2.2.1 | MIT | 开发 |
| `prettier` | 3.10.1 | MIT | 开发 |
| `stylelint` | 18.0.0 | MIT | 开发 |
| `stylelint-config-standard` | 41.0.0 | MIT | 开发 |
| `typescript` | 5.9.3 | Apache-2.0 | 开发 |
| `typescript-eslint` | 8.72.2 | MIT | 开发 |
| `vite` | 6.4.5 | MIT | 开发 |
| `vitest` | 5.1.0 | MIT | 开发 |

Ant Design 6.7.0 的 React peer 为 >=18，@ant-design/x 2.10.0 的 antd peer 为 ^6.1.1、React peer 为 >=18，故本包在 React 19.3.0 下直接采用两者，无需 React 19 兼容补丁。TanStack Query、Zustand 与 Testing Library 的 peer 同样覆盖 React 19。安装未用 `--legacy-peer-deps` 或 `--force`。

rehype-sanitize 是 Markdown 净化的补充依赖（MIT）；jsdom、Testing Library DOM/user-event/jest-dom 为真实 DOM 行为测试提供支持；@types/node 属开发类型。未添加无业务需求的 Radix、表单库或替代组件底座。保留 recharts 2.15.4，避免改变仍承接的旧页面；新图表按蓝图使用 ECharts。

传递依赖许可来自 npm 锁文件，并补核缺元数据的 `format@0.2.2`（package.json licenses 字段，MIT）和 `khroma@2.1.0`（实际 license 文件，MIT）。未发现 GPL/AGPL；`(MPL-2.0 OR Apache-2.0)` 选择 Apache-2.0 路径。开发依赖的 CC-BY-4.0 数据许可保留原作者许可文本，不移除第三方声明。完整许可分类计数见清单。

## R1-9 无障碍测试依赖补充

质量阶段固定新增开发依赖 `@axe-core/playwright@4.14.0`，其传递依赖 `axe-core@4.14.0` 同样锁定，二者许可均为 MPL-2.0。采用范围仅为浏览器无障碍验证，不由应用运行代码导入，不作为应用运行时资源提供。对应测试工具说明及许可文本要求保留在 `frontend/dependency-licenses.json`；它们不是上文 `(MPL-2.0 OR Apache-2.0)` 的双许可包，不能套用选择 Apache-2.0 的结论。版本和完整性摘要仍以最终 `frontend/package-lock.json` 为准，离线包按该锁重新准备和验证。

## R1-4 字体与视觉裁决

复用 `frontend/public/fonts/` 的 Noto Sans SC / Noto Serif SC 400、700 四个本地文件，许可证为 SIL Open Font License 1.1。没有从公网下载字体，没有伪造 600 字重：正文使用 400，标题使用已有的 700（P-23）。`font-synthesis: none` 避免浏览器合成粗体。衬线仅供导览/报告调用，平台标题明确使用无衬线。

`tokens.css` 包含颜色、种类色、图表色板、字号/行高、间距、圆角、阴影、布局、密度、动效的完整表，并补充边框、焦点环与布局零值。antd 主题仅在 design 边界使用数值映射。状态标签采用深色文字和语义色边框/背景，避免黄色或绿色的小字降低对比度；状态同时有图标与词表文字。

R1-4 尚未执行 R1-5 的旧代码迁移，因此检查脚本复用 R1-1 ESLint 中的精确历史文件清单，只临时豁免旧文件的令牌/术语存量；品牌字面量仍检查全部旧前端。新路径不豁免。新增 design/ui/i18n/dev 由 `tsconfig.design.json` 严格检查；R1-5 完成隔离后主配置切换 strict。此裁决不允许扩大旧文件列表。

## R1-4 离线边界

包内没有运行时 CDN、在线字体或遥测配置。生成器只读仓库中的单一词表源，不联网。npm 缓存由联网安装准备，交给 R1-9 的离线依赖包；`npm ci --offline` 的验证不替代 R1-9 断网容器中的后端安装、前端构建及启动验收。组件画廊通过 `import.meta.env.DEV` 动态导入，生产构建剔除画廊路由和组件。

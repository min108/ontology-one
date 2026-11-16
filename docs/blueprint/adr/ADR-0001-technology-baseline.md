# ADR-0001 技术选型定版

状态：已采纳（工程工具链；设计体系依赖由 R1-4 补齐）　日期：2026-11-16　相关轮次：R1

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

设计体系按蓝图选用 Ant Design、@ant-design/x、TanStack Query、Zustand、Vitest/Testing Library、Playwright 与 openapi-typescript；具体兼容版本、许可及字体字重决定在 R1-4 的同一记录中补齐，尚不能宣称设计体系已经完成。

## 影响

开发环境须先装 requirements.txt，再装 requirements-dev.txt。CI 的真实 SDK 检查采用本地合成模型服务，要求可写且可执行的 Linux tmpfs；不能把磁盘目录冒充内存目录。依赖缓存属于后续离线依赖包的输入，R1-9 完成离线构建验证。

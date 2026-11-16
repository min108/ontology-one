# R1-4 设计体系与前端依赖交接

日期：2026-11-16。版本：1.0.0。分支：`wp/r1-4-design`；代码起点：`10903846e6c91b81f51f25d4e221fe1de580d354`。R1-1 CI 43802818754 成功后按负责人授权实施。本文件为工作包交接；轮次 STATUS、R1-PLAN、AGENTS 的总进度由 root 合并时统一更新。

## 已完成

- `terms/zh-CN.json` 是词表与品牌的唯一维护源；`scripts/gen_terms.py` 生成前端词表/brand 与后端 core/terms.py、brand.py。包含 C.1 种类、C.2 状态、C.3 动作、专业词解释、品牌、术语黑名单和带理由的技术标识白名单。源文件中文按钮超长例外逐项登记理由。
- `design/tokens.css` 落实全套颜色、种类色、图表色板、字体与行高、间距、圆角、阴影、布局、密度、动效；`theme.ts` 映射 antd。复用本地 Noto 字体 400/700，标题明确使用 700，关闭合成字重。说明见 ADR-0001。
- 18 个平台组件全部实现，画廊 `/dev/gallery` 只在开发模式动态导入。一级导航示例准确为“工作台、建设、本体、应用、治理、管理”；词表键为 home/build/ontology/use/govern/admin。
- 品牌最小接入原登录、侧栏、浏览器标题、Settings 的产品信息，以及图谱导出与导览中原品牌字符串。技术协议标识、Cookie、数据库、环境变量、MCP 工具名、Windows 文件不改。没有重新格式化旧页面，没有迁移 legacy，没有重建业务页面。
- 全部直接依赖精确锁定；新增 Ant Design 6.7.0、@ant-design/x 2.10.0、TanStack Query、Zustand、G6 v5、ECharts、react-markdown/remark-gfm/rehype-sanitize、diff，以及全部 R1 测试、OpenAPI、样式前缀依赖。兼容 React 19.3.0；许可、Node 条件和元数据缺项的许可证核查见 ADR-0001 与 `frontend/dependency-licenses.json`。

## 契约与测试

先写测试并确认缺实现/违规样例失败，再实现。最终前端单元 49 项（18 组件全部覆盖）；Python 检查/生成/许可契约 40 项，可由 `unittest discover -s tests` 收集。

关键行为覆盖：StateBlock 六种状态均有下一步；加载 300ms 前不闪烁、3s 后显示阶段文字；请求编号折叠；状态/种类只读词表；导航默认拒绝；表格默认最多 6 列、列设置/密度/分页与 URL 同步、浏览器导航恢复、专业切简洁后禁止紧凑、列契约改变后恢复有效列；Omnibox 上下键/Enter/Escape 与中文输入法组合事件保护；拖放只接 Files、嵌套计数避免闪烁；任务与通知使用受控数据。

独立复审指出并已补测试修复：rem 字体简写、令牌混合字面量、camelCase 背景/圆角、逻辑圆角、纯英文 JSX 表达式/模板属性的扫描漏检；表格模式切换残留 compact；输入法确认候选时误选结果。没有删除、放松既有断言。

检查脚本仍是静态模式检查，不能替代业务文案人工走查。R1-4 临时沿用 R1-1 ESLint 的精确历史文件清单作为旧代码词表/令牌豁免；新路径严格检查，旧文件品牌字面量仍扫描。R1-5 移动后历史路径自然不再命中。不得扩大该清单来掩盖新增代码问题。

## 可复用组件 API

全部平台组件和类型由 `frontend/src/ui/index.ts` 导出。`Action = {label, onClick, disabled?}`；文案必须来自 `terms`，服务端动态业务内容可直接传值。

| 组件 | 接线参数 |
|---|---|
| AppShell | `{navigation, topBar, identity?, children}`；内部已提供 DesignProvider/antd ConfigProvider/App |
| NavRail | `{items: {key,label,href,icon?,permission?}[], permissions: Record<string,boolean>, currentKey, onNavigate?, collapsed?, onCollapsedChange?}`；有 permission 而未明确 true 时隐藏 |
| TopBar | `{workspaceName,onSearch?,userMenu?}`；真实顶栏不挂未完成的托盘/通知入口 |
| IdentityBar | `{organization,role,scope}` |
| PageHeader | `{title,breadcrumbs?:{title,href?}[],status?,primaryAction?:Action}` |
| StatusTag | `{domain?:StatusDomain,status:string}`，默认 element；未知状态显示词表回退文字 |
| KindTag | `{kind:Kind|string}`；未知种类显示词表回退文字 |
| StateBlock | `{state:'loading'|'empty'|'forbidden'|'unconfigured'|'error'|'partial',action:Action,description?,requestId?,phase?}` |
| DataTable<T> | `{columns:TableColumnsType<T>,dataSource:T[],rowKey,viewMode?:'simple'|'professional',urlKey?,emptyAction:Action}`；列需稳定 key，默认 URL 命名空间 table |
| FilterBar | `{value,onChange,children?,onReset?}`；业务筛选条件与 URL 绑定由调用页面提供 |
| HelpTip | `{term:HelpTerm}` |
| ViewModeSwitch | `{value:'simple'|'professional',onChange}`；用户偏好持久化由 R1-5 接入服务端 |
| ConfirmCard | `{level:'L1'|'L2',summary,impact,gates?:string[],onConfirm,onCancel,confirmLabel?,busy?}` |
| Timeline | `{items:{id,title,time,description?,onCompare?}[]}` |
| Omnibox | `{open,onOpenChange,value,onSearch,items:{id,label,description?,group?}[],onSelect,loading?}`；Ctrl/⌘K 由 R1-6 入口注册；组件负责弹窗内键盘行为 |
| DropOverlay | `{enabled?,onFiles:(File[])=>void}`；文件分流和上传授权由 R1-6/服务端负责 |
| TaskTray | `{items:{id,title,status,progress?,onOpen?}[]}`；本轮仅画廊与单测 |
| NotificationCenter | `{items:{id,title,read,onOpen?}[],onRead:(id)=>void}`；本轮仅画廊与单测 |

DataTable 参数为 `${urlKey}.columns`（逗号分列）、`${urlKey}.density`、`${urlKey}.page`。更新保留其他 query/hash；使用 history.pushState，监听 popstate。查询字段不进入持久化存储。

前端品牌导出 `brand.product_name/product_name_en/product_descriptor/tagline` 和 `browserTitle`；后端导出 `BRAND` 字典。新增业务词应编辑源 JSON 并运行生成器，不直接改生成文件。应用版本继续读已有 `APP_VERSION`。

## 验证与证据

以下命令已通过；使用 Node 22.23.4，npm 缓存为本 worktree 的 `work/npm-cache`。

```bash
python scripts/gen_terms.py --check
python scripts/checks/check_tokens.py
python scripts/checks/check_terms.py
python -m unittest tests.checks.test_check_tokens tests.checks.test_check_terms
ruff check scripts/gen_terms.py scripts/checks tests/checks backend/core
ruff format --check scripts/gen_terms.py scripts/checks tests/checks backend/core
npm --prefix frontend test -- --run
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend run lint:styles
npm --prefix frontend run build
```

`build` 包含新增设计范围 strict 检查和原有宽松 tsc 构建。生产产物不含 `/dev/gallery`、`data-gallery`、`development-only` 或 Gallery.module 标记。旧产物大块体积提示仍在，未通过提高阈值掩盖。

独立目录 `work/offline-frontend` 用当前 package/lock 和 `npm ci --offline --cache "$PWD/work/npm-cache"` 成功安装 646 个包，随后构建通过；这是缓存离线模式验证，**不是断网容器验收**。R1-9 仍需完成完整断网安装/后端启动验证。运行期画廊实际浏览器公网请求 0、脚本错误 0；400/700 字体从本地 `/fonts/` 加载成功。画廊主视图实际文字仅 12/14/15/17/24px 五级；标签与徽标为 12px，其余正文至少 14px。原目录与离线副本的 HTML/CSS/JS 产物摘要一致。

浏览器截图与日志位于本 worktree（不提交大图）：

- `work/evidence/gallery-1366.png`：1366×768，页面宽与滚动宽均 1366。
- `work/evidence/gallery-effective-125.png`：125% 缩放等效 CSS 视口 1093×614，宽与滚动宽均 1093；这是布局等效测试，实际浏览器缩放验收交 R1-9。
- `work/evidence/gallery-omnibox.png`、`gallery-tasks.png`：查找弹窗与任务托盘；截图禁用过渡动画以避免捕获缩放中间态。
- `work/evidence-gallery.json`、`frontend-tests.log`、`build.log`、`offline-install.log`、`offline-build.log`：对应运行记录。

启发式/文案走查：每个状态有可执行下一步；失败说明保留情况；没有假业务统计；导航和操作都有可读名称；颜色同时配图标/文字；主动作聚焦、其余次按钮；本体/治理未混成集团一级工作区。画廊明确“演示预置”，没有将模拟界面解释为真实模型运行。布局在受测宽度没有横向溢出；缩到小屏时导航自动收起。真正业务编辑的小屏只读处理由 R1-5 页面接线承担。

## 合并后 root 必须补齐的两处

R1-3 同时拆分 `backend/main.py`，经 root 确认，本包不改此文件。完成 R1-3 合并后串行增加：

```python
from backend.core.brand import BRAND

# FastAPI(...) 的 title 参数替换为：
title=f"{BRAND['product_name']} · {BRAND['product_descriptor']}"
```

其余 FastAPI 参数、生命周期与路由注册不变。这是 R1-4 简报要求的应用标题，不能遗漏。

R1-5 迁移 main.tsx 时需保留开发条件懒加载和浏览器品牌标题；`index.html` 的两个 `__ZY_PRODUCT_*__` 占位由 Vite shared-brand-html 插件替换。R1-8 新系统信息页必须直接读品牌与 APP_VERSION，旧 Settings 的品牌展示只是过渡承接。root 统一追加 CI 命令、更新全轮交接材料，并运行全回归后合并；本包没有修改任何 workflow、requirements.txt，没有 push/PR/tag。

## 主干集成补充

主干完成 FastAPI 品牌标题接线，并将 meta.kinds 的原16种名称统一取自生成词表；项目种类按词表显示“场景工作室”。增加一致性契约，防止两份名称漂移。API 类型生成器也读取 BRAND；词表检查只认可两个逐字匹配生成器的确定产物，不扩大目录豁免，新增两个反例/一致性测试。

主干新增 CI 的词表、令牌、API类型一致性检查及前端单元测试。原82文件豁免须在R1-5搬迁时删除，不能让新入口继承豁免。完整集成结果见R1-PLAN第11节。

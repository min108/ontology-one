# R1-5 旧页面站内链接与旧外壳样式核对

## 站内导航值

仅替换 JSX `Link` 的 `to` 字面量，页面文字、请求及权限逻辑不变。

| 文件（frontend/src/legacy 下） | 旧目标 → 新目标 | 数量 |
| --- | --- | --- |
| components/AssetPlacement.tsx | /settings → /admin/model | 1 |
| components/DemoWorkspace.tsx | /administration → /admin/users | 1 |
| pages/Applications.tsx | /settings → /admin/model | 1 |
| pages/Authority.tsx | /settings → /admin/model | 1 |
| pages/DatasetDetail.tsx | /settings → /admin/model | 1 |
| pages/Experience.tsx | /settings → /admin/model | 1 |
| pages/IntegrationApplications.tsx | /administration → /admin/users | 1 |
| pages/MaterialDetail.tsx | /settings → /admin/model | 1 |
| pages/SkillDevelopment.tsx | /settings → /admin/model | 2 |

共 9 个文件、10 个链接。承接范围内没有 `/settings#demo` 或 `/security` 导航值，故未产生对应替换。按分工保留 legacy/App.tsx 原样，Overview/Settings 留给替换包；API 地址不属于导航值，未修改。完成后对承接范围全量检索，四种旧目标均无残留。

验证：TypeScript AST 提取所有待改 `to` 字面量并仅替换其字符区间，所得结果逐字等于实际 diff；9 文件均无 JSX 解析错误。`git diff --check` 通过。legacy/api.ts SHA256 仍为 `677bd70d30a3fefcec350d2fab76d47e339deccc932266257f02b37790315958`。本次不重复运行重构建，合并后的路由与页面冒烟由外壳包统一验证。

## legacy/styles.css 只读清理范围

本提交没有修改 CSS。以下基于 PostCSS 选择器解析及 src 中类名引用核对，只有旧 App 退役后才可清理；不得整行删除压缩样式或整个响应式区块。

| 范围 | 可删候选及依据 |
| --- | --- |
| 旧外壳网格与侧栏 | `.app-shell`、`.sidebar`、`.sidebar.open`、`.sidebar-bottom` 及其子规则；承接页面不渲染这些类。navigation.tsx 中 `.sidebar` 是判断旧侧栏链接来源的 DOM 查询，不是类的渲染者；其行为兼容适配由外壳包另行处理。 |
| 旧品牌区 | 独立 `.brand`、`.brand-logo`、`.brand-text`、`.brand-name`、`.brand-organization`、`.brand-mark` 及限定后代规则。后两者已无 TSX 使用；其余仅旧 App。不能按包含 brand 的文本批删。 |
| 旧导航 | `.nav-section`、`.nav-list`、`.nav-item`、`.nav-arrow`、`.nav-data-toggle`、`.nav-data-children` 的基础、状态、后代及媒体查询规则；仅旧 App 渲染。`.nav-data-group` 虽是旧 App 类，但本文件没有单独规则。 |
| 旧顶栏 | `.topbar`、`.top-context`、`.workspace-heading`、`.search-button`、`.ai-top-status`、`.mobile-menu`、`.avatar` 及其后代/状态/响应式规则；本文件 `.workspace-switch`、`.identity-menu`、`.harness-activity*` 相关规则均受 `.topbar` 限定，限定规则可随旧顶栏退役。其他样式表中的同名组件样式不在本次范围。 |
| 旧页脚与旧全局搜索 | `.footer-note`、`.workspace-label`、`.search-results`、`.search-hit` 及其后代/状态规则；TSX 只由旧 App 使用。独立 `.dot` 当前也仅旧 App 工作区标识使用，可随其清理。 |

必须保留或拆分处理：

- `.badge.red,.badge.danger,.badge.brand` 是共用状态徽标规则，不能随旧品牌区删除；`--brand` 等变量仍为旧业务页面所用。
- 打印规则 `.sidebar,.topbar,.footer-note,.briefing-links,.page-actions` 只去掉前三个选择器，保留业务汇报链接和页面按钮的打印隐藏行为。
- 所有媒体查询均应逐选择器处理；例如窄屏块还含页面标题、表格、卡片、网格和设置页布局，不能整块删除。
- `html` 的 `scroll-padding-top:98px/80px` 与旧顶栏高度相关，但作用于全局锚点，需由新外壳决定替换值；不直接归入安全删除范围。
- reset、body、字体、滚动条、减少动效、表单、按钮、卡片、徽标、表格、弹窗、Toast、loading/empty、页面与汇报页样式仍属承接功能。Overview、Settings 专属布局及本次口号移除留下的页面装饰规则是另一类清理，不能冒充旧 Shell 专属规则一起删。

根集成补充：Overview仍承接期间的1个 /settings#demo 链接改为 /admin/demo#demo；其余活动页面旧管理目标零残留。90项浏览器验收通过后退役旧App/main，并按上述选择器边界清理 styles.css 的103条、auth.css的27条旧Shell选择器。两文件分别保留232、56条规则，声明与媒体上下文逐项不变；.badge.brand、页面打印、身份控件、旧登录/安全/账号页、access-disabled/auth-error/sr-only均保留。旧html锚点偏移由新设计层html统一令牌值接替。精确清单在r1-evidence/r1-5。

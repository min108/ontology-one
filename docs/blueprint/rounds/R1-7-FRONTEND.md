# R1-7 身份前端交接

基点：`ebc24f86f802f1d886ce07471303f97f2af54664`。本包实现登录、账号安全、账号与权限双页签、机构目录、审计及本人视图偏好。后端契约由 R1-7 后端工作包提供；依赖图、旧请求封装和受保护流水线不改。

## 功能与操作对照

以下为实际实现与操作对照。独立隔离 scope 的 12 项真实浏览器验收已通过（最终源 `d416cfb`，含 ADM-06 原因审计、当前机构标签及稳定时间格式）；后续仅等价退役旧 UI。最终主干质量矩阵另行记录。

| 编号 | 新入口与保留行为 | 定向测试或浏览器契约 |
|---|---|---|
| L-01 | 登录保留账号、提交后清空密码；错误、停用、限速、服务不可达及重新连接 | account.test；identity browser 的凭据失败、429 UI 注入；真实限速由后端安全回归验证 |
| L-02 | 登录后进入工作台 | 新账号浏览器登录等待工作区导航 |
| L-03 | App 使用无改密门 AuthBoundary；等价对照后旧 AuthGate 退役 | account.test 旧标记为 true 仍渲染业务；真实会话两层标记恒 false |
| L-04 | 品牌配置、版本、联系管理员；不展示默认账号 | 新 LoginPage 与品牌配置；质量截图核对 |
| SEC-01 | 姓名、账号、当前机构、角色、当前权限和超管标记 | 三角色真实会话对照 |
| SEC-02 | 主动改密，12–128 字符、字母数字、两次一致；失败保留输入；成功清空并撤其它会话 | account.test；新账号双 context 改密、其它设备被退出 |
| SEC-03 | 设备、最近使用、登录、来源、到期、当前标记、刷新 | 新登录会话卡片；时间仅标注 timestamp 动态区域 |
| SEC-04 | 撤销前确认，取消零 DELETE；撤当前会话退出 | account.test；独立新账号撤其它及当前会话 |
| ADM-01 | 姓名账号、机构、角色、状态；本机构管理员仅看可见账户；本地过滤与分页 | 数据由 v2 受控列表提供；三角色权限 smoke |
| ADM-02 | 开通账号五项原字段，三种原角色；前置用户名、密码校验 | unit 验证规则与确认 DTO；浏览器新建账号 |
| ADM-03 | 编辑姓名角色、自身角色禁用；所有目标会话失效 | unit 自身 reload；独立新超管修改自己后退出 |
| ADM-04 | 重置密码，临时密码校验；不设强改密 | 新账号 UI 重置；后端验证新会话可直接访问业务 |
| ADM-05 | 停用启用，自身停用禁用；服务端保护最后超管 | unit 取消零调用；浏览器新账号停用启用；最后超管由后端事务测试保护 |
| ADM-06 | 改其他超管原因必填，其余可选；填写时至少 2 字 | unit 原因校验；真实新建 HQ 超管空原因禁用、唯一原因提交、审计 API 与对应 UI 详情核对 |
| ADM-07 | 授权账号、机构、范围、时间；双页签、本地过滤与分页 | 真实 HQ 列表与 grants unit |
| ADM-08 | 三种范围与适用限制，原因必填，同账号机构覆盖 | validation.test 矩阵；新 HQ 账号依次覆盖三范围并读回同一组合仅一条 |
| ADM-09 | 撤销前确认，取消零写 | 浏览器取消保留、确认后列表读回不存在 |
| ADM-10 | 时间、操作人、操作、对象机构、结果、详情和请求编号；每页 30 条；新增筛选 | unit 筛选及 Request ID；浏览器精确操作筛选、条数、详情；HQ 外禁止 |
| ADM-11 | 统一无权限状态，先鉴权再发特权读取 | 3 页面 unit 不调用 API；三角色直达路由 |
| 机构治理 | 树形新增、改名、上级和排序、停用启用；不可删除；总部保护 | unit HQ/停用/权限/编辑 guard；浏览器新机构开通、改名、开户登录、停用踢出、启用恢复 |
| 偏好 | GET 本人偏好，PUT simple/pro；最后选择串行保存，失败明确重试 | preferences.test 的晚 GET、晚身份、串行 PUT、失败重试；质量脚本真实 UI 切换并 GET 确认 |

## 稳定定位与权限

- 五页区域及 h1：`账号登录`、`账号安全`、`账号与权限`、`机构目录`、`审计记录`。页面区域 `aria-label` 同标题；四个业务区域 `aria-busy` 等待所需数据，账号页包含当前页签的数据状态。
- 用户页签名称为 `机构账号`、`跨机构授权`。机构树的 title `aria-label` 为机构真实名称；行按真实用户名定位，避免依赖排序或固定行号。
- 编辑弹窗都使用 dialog/title；字段 label 与 `terms.labels.identity_*` 一致。先点 `核对操作`，再 `确认保存`；取消与返回编辑有独立按钮。
- 页禁用统一 `role=status` 和词表 `states.forbidden.title`；HQ 外没有机构目录、审计写读入口，用户管理同时要求旧布尔权限与 `admin.users`。
- 新建角色选项只含 admin/modeler/reviewer；不因服务端 roles 返回新角色就开放创建。管理范围仍由服务端校验。
- 仅 `time[data-visual-dynamic="timestamp"]` 表示真实变动时间；业务文字和数据不遮罩。
- `foundation-identity.spec.mjs` 所有变更均面向 UI 新建的唯一账号或机构。改密、禁用、改名、会话撤销只触及新账号；不改 setup 三角色及 HQ seed 超管。HQ 写测试每次通过真实 UI 新登录取得独立会话，避免 Shell 切机构改变共用 cookie 的服务端工作区。浏览器 context 用完关闭，不导出这些临时登录状态。

## A.2 状态

1. 操作对照如上；只取消 H.2-1 的强制改密门，业务改密保留。
2. 新路由 `/account/security`、`/admin/users`、`/admin/orgs`、`/admin/audit`；既有 Shell 重定向保留。新页面无旧地址链接。
3. 管理列表调用 v2，写走已登记能力；登录/改密/会话仍用现有安全服务薄 REST 层，无第二套业务函数。
4. 列表直接消费真实服务端可见集，保留编号；机构不展示数据库名。存量迁移条数与 v1/v2 等价由后端测试提供。
5. 页面/入口按新权限键及原布尔桥收紧，服务端权限与数据隔离未改；六面验证由后端包和最终集成报告关联。
6. 独立 identity 操作脚本在真实历史夹具上完整 12/12 通过；创建的账号、机构与会话均由真实 UI 操作，不改共享 seed 身份。
7. 对照通过后退役 `legacy/pages/Security.tsx`、`Administration.tsx`、`auth.css`；`legacy/auth.tsx` 只保留 AuthProvider/useAuth/类型与清理导出桥。共享 `.access-disabled`、`.auth-error`、`.sr-only`、AssetDetail 还原弹窗仍使用的 `.admin-form` 三条规则及实体可见性间距逐字迁入 legacy/styles.css。legacy 清单由 80 缩至 77 文件，基线只登记这三项已验证删除。

## 状态、失败恢复与设计裁决

- 保留会话专用 AbortController，既有 65 项 session 单测（含 46 项竞争边界）不改；新 prefs context 有自己的取消控制。清工作区先取消偏好任务。
- 视图是即时选择，保存状态诚实标注。PUT 串行且后续选择合并为最后一次；旧失败不阻塞已排队的新选择。GET 只在当前身份尚未有本地选择时应用，重试也不能覆盖已有选择。切身份中止旧请求与旧队列。
- 编辑字段显式加入 guard，恢复原值即 clean；成功弹窗关闭或密码表单清空后 clean，失败保留输入及 dirty。过滤/审计条件不登记 dirty。没有新增上传。
- L3 管理写采用两阶段确认，呈现实际影响；不伪造 L3 确认令牌。必填字段沿用旧账号/授权契约和目录约束；新增原因来自服务端审计要求，不添加额外业务跳转。
- 小于 1024px 禁止编辑，仍可查阅；按钮禁用，已打开编辑器的字段也禁用。总部不可停用、不能改类别或上级；停用机构需先启用；开通中/失败不可在线启用或编辑。
- 输入、表格、树、弹窗使用 Ant Design；有限枚举用有明确 label 的原生 select，设计令牌控制样式。新代码不加入颜色/字号/阴影字面量，也无远程字体、CDN 或遥测。
- 词表 `identity_*`；审计操作与结果译名独立 `identity_audit_actions/outcomes`，原始操作标识放详情，未知操作如实标为“其他操作”。审计时间筛选明确北京时间并转换 UTC。
- 只在真实操作对照通过后退役旧身份页面；Windows、真实模型及主干完整质量矩阵不在本包通过声明中。

## 验证记录

实施过程中已有 validation 17、account 5、admin 初始 9、prefs 初始 6 和 session 65 项通过。后增反例证实偏好加载重试覆盖选择、停用机构仍可编辑，已修正。2026-11-16 15:36 UTC 定向运行遭宿主并发负载：admin 从 2.9 秒增长到 60.6 秒，触发原 5 秒超时，prefs/navigation 通过；按 root 调度暂停重测，未增加超时或重试。最终命令、通过数量与浏览器证据待集成后追加。

## 文案与启发式检查

此表是代码与契约自查；真实尺寸、键盘、可访问性和截图走查仍由质量工作包执行。

| 项目 | 本包处理与待验边界 |
|---|---|
| 状态可见 | 页面加载 aria-busy、请求错误/请求编号、提交中禁用、偏好保存中/失败/重试；浏览器核对可见状态 |
| 对应实际业务 | 机构、账号、授权均来自真实接口；不能把上级关系说成继承权限；停用保留数据 |
| 用户控制 | 写前核对影响，可取消或返回编辑；取消不发写请求；会话撤销和自身账号变更明确说明退出后果 |
| 一致性 | 页面标题、动作、状态统一词表；使用既有 Ant Design 和令牌；两个视图共用数据 |
| 错误预防 | 用户名、密码、原因、父子循环、角色与授权范围预检；总部保护；后端仍是最终权限边界 |
| 可识别性 | 字段有可见 label；密码要求、权限范围和原因规则贴近字段；审计编号/标识在详情可查 |
| 效率 | 用户和授权本地过滤、表格列与密度；审计精确筛选分页；服务器记住最后视图选择 |
| 简洁 | 不出现新角色创建、职责授予、删除机构；不展示库名或默认管理员账号；专业详情按需展开 |
| 错误恢复 | 请求失败保留表单和 dirty；错误有重试，目录开通失败说明离线检查；恢复原值可退出 dirty 状态 |
| 帮助 | 内联说明密码交付、授权覆盖、跨机构读取/维护差别、机构停用影响；不增加教学页或跳转 |

新增词条均在 `terms/zh-CN.json`，生成副本不手改。审计筛选明确为编号/标识精确匹配，展示译名与底层标识分开，防止用户把中文名称误作编号提交。除 H.2-1 取消强制改密外，没有取消旧操作。

### 2026-11-16 单 worker 复验

- 先红：新机构根选项、角色词表、未认证页面 DesignProvider、开通失败离线指引 4 项精确失败，另 35 项通过（work/r1-7-identity-red.log）。
- 修正后 48/48 定向通过；完整 20 文件 256/256 通过（75.39 秒，maxWorkers=1、no-file-parallelism），既有 session 65 项保持。
- 严格类型首次发现未知偏好值的 TypeScript 缩窄缺口，改为正向枚举判断；9 项 prefs 重验通过。strict/legacy 类型、CSS 隔离输出与生产 Vite 构建通过（3314 模块，14.01 秒）。
- ESLint、Prettier 通过；新 CSS 的规则间空行已按 stylelint 修正，不涉及旧 CSS。浏览器前还核对词表、令牌、生成物、隔离测试与离线静态检查；后续记录见实际 evidence。

### 首轮真实 A.2：保留失败证据

应用与测试提交 `45ebfd2`，证据 `work/evidence/r1/identity-a2-01`。12 项完整运行，7 项通过，5 项因 `/api/v2/orgs` 500 无法进入写流程。定位为真实 0.7.0 夹具的总部 `org_id=null` 与新 DTO 必填字符串冲突；由后端包修正可空契约及总部根解析，禁止改存量编号。runner.json 证明 4 库完整清理、服务全部停止。新登录、账号安全、审计已实际截图；账号截图保留目录错误状态，不作为通过证明。截图审查发现左侧账号卡被右侧会话列表拉伸，修正为顶部对齐，下一轮核对。

### 第二轮真实 A.2：精确字段名称

应用提交 `c4913ab`，证据 `work/evidence/r1/identity-a2-02`。完整 12 项中 11 项通过；机构流程在第一步字段定位失败，尚未发送机构写请求。浏览器原生选择器与数字输入器的标签计算混入选项/增减按钮文字，给业务控件补充与可见标签一致的 aria-label。保留原始严格定位和 deadline；新增精确 spinbutton 名称反例先红后绿，管理页 16/16、ESLint 和词表检查通过。其余真实账号/授权/两设备会话/审计第二页均通过；4 库全部清理、服务停止。下一轮仍完整运行 12 项，不仅重跑失败步骤。

第三轮 `identity-a2-03` 在严格类型阶段终止，未创建数据库或启动服务。新单测误给 RTL `getByRole` 传了只适用于 label 查询的 `exact` 选项；删掉不受支持的选项，`name` 字符串本身仍精确匹配，不放宽业务断言。失败原件完整保留。

### 第四轮真实 A.2 与旧页退役

`identity-a2-04/runner.json`：源 `0e206ae71a5f7058ca9d671f85bbab40907aa0cd`，真实历史夹具 `1359118`；12 项通过（2.2 分钟），0 失败/跳过/重试。strict 与 Vite 14.40 秒通过。namespace `e2e_r1_c9202ee57e75d803a37ab67f` 的 created_databases 与 cleaned_databases 完全相等，共 5 库（控制库、3 初始机构库、新增机构库）；services_stopped=true。保留前三轮失败原件，不覆盖或删除。

旧版对照使用 R1-6 的真实 `legacy-replacements-1` 证据，已实际查看 login-empty/security-admin/administration-hq 三图；新图在本包 `work/evidence/r1/identity-a2-04/results/` 下，各测试目录独立保存 identity-login/security/users/organizations/audit.png 五图。账号事实、改密、会话及账号字段动作均承接；旧管理审计页签移至独立审计路由。机构是新增能力。只取消 H.2-1 的整站强制改密，主动改密与安全限制保留。原始旧图与新图是实际运行结果，未拼接或改图。

| 对照点 | 真实检查结果 |
|---|---|
| 登录 | 品牌、版本、账号密码及失败恢复保留；删除默认账号说明；429/断网为明确 UI 故障注入，真实限速由后端安全回归负责 |
| 账号安全 | 身份五项、会话所有时间/来源/当前标记、刷新/撤销与主动改密保留；双设备真实验证改密和撤销失效；顶部对齐修复已实际看图 |
| 账号与授权 | 五列账号事实与全部旧动作保留；真实新账号编辑/重置/停用启用、自身改名退出；三档授权覆盖与取消撤销通过 |
| 机构治理 | 新建稳定 ID、改名/排序、开通账号并登录、停用后退出、启用恢复，全部 UI 驱动；无删除动作，总部保护通过 |
| 审计 | 真实筛选、详情请求编号、每页 30 条与第二页通过；原始操作/结果在详情保留，不修改审计数据 |

截图走查另修正账号安全“归属机构”误用为专用“当前机构”（值仍为当前 workspace.name），跨机构单测先红后绿。审计已知 `started` 状态显示“已开始”，原始标识仍留在详情。所有时间统一上海时区、双位月日时分秒与 tabular-nums；两种日期长度反例先红后绿，动态标记仅 time 和审计详情 request-id，不遮业务内容。

退役后最终全前端与隔离/构建结果见收尾记录。浏览器操作验收对应上述明确源；退役提交不伪称已再跑同一套浏览器。最终集成视觉与无障碍矩阵由质量工作包执行。

### ADM-06 补充对照

独立审查发现原 12 项中的 self-edit 用例未覆盖“修改其他超管”的真实必填原因审计，故在同一用例追加该链路，仍为 12 项，不改既有 deadline 或其他断言。所有目标仍为 UI 新建临时账号，不改 seed 超管。

第五轮 `identity-a2-05` 源 `61f69e7`，11/12 通过；新链路的无原因禁用、填写原因保存、GET 审计 details.reason 精确相等均通过，UI 详情定位误将完整可访问名“账号编号 hq”当成只有账号编号，等待到原 45 秒失败。依据原始页面快照改用完整 cell 名称精确匹配并断言唯一行，禁止 first() 或放宽超时。5 库全清、服务全停；失败原件保留。第六轮 `d416cfb` 完整复跑全部通过，之后才重新应用已暂存的退役差异。

### 最终真实 A.2 结果

`work/evidence/r1/identity-a2-06/runner.json`：源 `d416cfbf1d6a96c0343af5e99998b1a67061e869`，12/12 通过（1.2 分钟），0 flaky/skip，原有 45 秒单例 deadline 不变。新增 ADM-03/06 合并用例 9 秒完成；精确 action=user_update、目标账号+机构 cell 唯一行、原因 API 字段与 UI 详情均验证。fresh strict/Vite 11.52 秒通过。

创建与清理列表完全相等，namespace `e2e_r1_cb84f4bef20baa4c331115f3` 的 control 与四个 tenant 库全部清理（四个 tenant 库包含 UI 新机构），services_stopped=true。本轮原始五图位于 evidence 的 results：

- `foundation-identity-identi-adba2-lear-secret-and-allow-retry-foundation-identity/identity-login.png`
- `foundation-identity-admin--da84b-nd-headquarters-only-routes-foundation-identity/identity-security.png`
- `foundation-identity-headqu-87ba3-d-enable-only-a-new-account-foundation-identity/identity-users.png`
- `foundation-identity-headqu-46f95-eadquarters-and-no-deletion-foundation-identity/identity-organizations.png`
- `foundation-identity-headqu-fc0d3-terable-and-retains-details-foundation-identity/identity-audit.png`

已再次实际查看最终 security/audit：当前机构标签正确，时间双位，`started` 显示“已开始”，左侧卡片无拉伸。与第四轮相同布局的 login/users/orgs 已实际对照旧版或新增行为。本轮完成后按 root 新指示合入 backend 的 314d4a0/1198c4d/2693948，仅增加保留分支审计、跨版本真实 HTTP 比较与清理安全回归，不改产品代码/DTO，不伪称这三项参与过本轮浏览器源。

### 退役后的最终验证

- 全前端 20 文件、260/260 项通过（58.12 秒，maxWorkers=1/no-file-parallelism）；既有 session 65 项（含 46 项竞争边界）保持。
- 删除本 worktree 的 legacy 声明缓存后重新 strict/legacy 类型检查与 Vite 构建通过（13.54 秒）；29 张旧样式表、5044 个选择器完成隔离检查。
- ESLint、Prettier、stylelint 全绿；9 项 CSS/类型隔离测试通过；legacy inventory、词表、设计令牌、离线源码及生产 dist 检查全部 0 违规；terms/API 生成物一致。
- 后端追加验收脚本与动态 E2E 清理器的版本差异已修正，仅比较脚本冻结四候选并做精确 IN/结果子集校验。新增同 namespace 未预留合法 hash 反例先红后绿；7 项比较清理 + 21 项 E2E runner + 8 项 legacy shrink，共 36 项 pure 通过，Ruff 通过。运行 HTTP 采集与比较逻辑不改。
- 日志：`work/r1-7-final-verification.log` 的前端/构建/静态阶段全绿，但保留最初组合清理器的一个失败；修复后的结果单独在 `work/r1-7-cleanup-integration-green.log` 与 `work/r1-7-final-static.log`，未覆盖失败原件。
- 最终移除三旧文件，AuthProvider/useAuth 等兼容桥保留；六条共享 CSS 与实体可见性/响应式间距逐字保留。legacy/api.ts SHA256 仍为 `677bd70d30a3fefcec350d2fab76d47e339deccc932266257f02b37790315958`。package/lock、requirements 与受保护工作流字节未改。

限制：本包真实身份验收对应 `d416cfb`（旧 UI 仍在但路由已由新页接管），删除后的新路由及兼容桥由完整单元/冷缓存构建验证；最终主干完整 browser/63 图/无障碍矩阵另由质量包执行。Windows 与真实模型验收不在本包完成声明内。

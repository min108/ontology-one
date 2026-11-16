# R1-6 工作台与万能入口 v1

## 范围与基点

基点 `ebc24f86f802f1d886ce07471303f97f2af54664`，版本仍为 1.0.0。实现 `features/home`，将 `/` 指向新工作台，在现有 Shell 中挂载内存文件接入与最近访问记录。GlobalSearch 继续使用 `semantic.search`，增加已建成页面命令；不增加智能能力、后端业务接口、依赖或权限。旧 `Overview.tsx` 暂保留，等待质量工作包实际 A.2 操作与截图对照后另行退役，当前不把静态检查或单测视作浏览器等价证明。

## 数据口径与入口

| 区块 | 实际来源与范围 | 去向 |
| --- | --- | --- |
| 本机构概况 | `/overview` 的 assets、relations、counts.organization、data_rows；恰好四项 | `?detail=assets/relations/organizations/rows` 打开同源明细，保留查询参数与 hash；再到对应既有目录 |
| 本机构待处理 | `/work/catalog` 全机构任务；排除 completed、published、archived，与旧 Work 的 active 筛选一致 | `/work?status=active` |
| 需要补充 | 有建设权限且非只读：`/entities?asset_only=true&status=returned&page_size=1` | `/maintenance?tab=review&review_status=returned` |
| 等待审核 | 有审核权限且非只读：同上 status=review | `/maintenance?tab=review&review_status=review` |
| 等待关联审定 | 有审核权限且非只读：`/relations?status=candidate&page_size=1` | `/maintenance?tab=relations&relation_status=candidate` |
| 进行中 | `/harness/runs?limit=20` 与 `/agent/runs` 返回的当前账号、机构近期记录，去重后只列 queued/running/retry/cancelling | 复用原 activityHref；完整进度仍由原顶部后台任务入口承接 |
| 继续上次的工作 | 浏览器最近访问元数据，绑定账号、机构与 scope | 既有对象地址 |

统计请求失败呈现重试，不伪装为零。部分任务源失败呈现部分不可用提示；其他机构只读工作区不查询本人的后台运行，并准确提示切回所属机构。共建单位是业务组织对象数，不是登录机构目录条数。资产总量先展开四类明细，避免把四类总量直接跳到默认只列数据的目录。

## 万能入口与文件接入

Ctrl/⌘+K、顶栏和首页输入框共用 GlobalSearch/Omnibox。普通文字仍按 250 ms 防抖调用已有 `semantic.search`，结果严格解析并按业务种类显示；`/` 命令立即列出已建成入口，不调用模型或检索接口。回车使用选中项直达。跨机构检索入口继续受 federation.read 控制。空输入可列本身份最近访问。

文档 TXT/MD/PDF/DOCX/HTML/HTM 带入素材整理，XLSX 带入台账，任何包含 CSV 的批次先显式选择去向；混合/不支持/超限文件保留在选择器，可以分组处理或明确移除，不自动舍弃。支持类型对应旧后端能力，Materials 原本支持 CSV 的后端不变，仅补前端 accept、校验与词表说明。

文件只进入内存 Map 和原上传页队列。地址只携带随机交接编号，不含文件名或内容。交接绑定账号、机构、scope、目标页，成功插入队列后才确认接收；StrictMode 重放不重复接收。机构切换确认弹窗期间禁止读取/消费交接；取消后文件保持原状，通过继续入口显式重试。新转交按真实 File 引用去重，不因名称、大小、修改时间相同而丢弃不同文件；旧手工选择器规则保持。先插入旧页队列，下一次渲染确认整批引用均已在队列后才确认接收，切换保护没有提前释放的间隙。旧页忙碌或满队列时不确认，Shell 显示未接收批次的继续/清除入口。跨页后仍可恢复未接收文件。未选择批次、待交接批次以及旧页队列分别注册切换 guard，确认离开后清理内存；角色只读时不接收，不自动上传。原有 CSRF、workspace headers、批次上传租约、归属单位选择、失败单项重试保持。Materials 25 MB/40 份、Datasets 50 MB/20 份限制不变。

程序式导航通过可取消事件桥沿用旧 Authority 编辑器的原有离开确认；搜索被拒不关闭，文件被拒仍保留待交接批次，明确继续后可再次接受。原 Link/NavRail 保护不改，不把机构切换确认套到普通路由。

旧 FileIntake 已处理的 drop 不再被全局 DropOverlay 二次接收。Materials 的 upload 地址标记在关闭时清除，命令可以再次打开已关闭的上传窗。

## O-01 至 O-06 / A.2 对照清单

| 编号 | 新去向或裁决 | 验证操作 |
| --- | --- | --- |
| O-01 四项统计 | 当前机构同源四指标与明细抽屉；资产按 data/rule/skill/knowledge 进入 `/assets?kind=...`，关联到 `/graph`，业务单位到 `/graph?view=organization`，记录到 `/datasets` | 与真实 `/overview` 数值逐项比较；点击四项，核对明细、地址和返回 |
| O-02 业务领域覆盖 | 按 P-16 不复刻列表；本体目录 `/assets` 的领域筛选，旧 `/assets?domain=...` 仍有效 | 进入目录选择相同领域，打开对象 |
| O-02 近期沉淀成果 | 按 P-16 不复刻列表；本体目录知识/规则页签 `/assets?kind=knowledge`、`/assets?kind=rule` 继续承接 | 查询并打开同一知识/规则对象 |
| O-02 重点工作专题 | 按 P-16 不复刻列表；`/projects`，万能入口 `/projects` 命令 | 打开相同专题与任务 |
| O-02 工作动态 | 按 P-16 不复刻列表；`/maintenance?tab=history`，万能入口 `/history` 命令 | 查看办理记录及对象/版本入口 |
| O-03 业务汇报导览 | 工作台入口卡到 `/briefing` | 点击后到原导览页 |
| O-04 空机构下一步 | `/overview.assets=0` 且 manage_users 时提示到 `/admin/demo` | 空机构管理员可见；普通角色不可见 |
| O-05 三个快捷入口 | `/materials`、`/datasets`、`/methods` 命令分别到素材上传、台账导入、原 `/applications` | 键盘命令回车到真实页面，上传仍待用户确认 |
| O-06 口号和装饰图 | H.2-4 明确取消，不复刻 | 新工作台无口号横幅与装饰图 |

旧 Overview 未删除；退休登记与物理删除须在上述浏览器操作及新旧内容区截图核验完成后提交。原 25 路由 × 3 角色共 75 条保持；未删除或缩小既有断言。新脚本 `e2e/foundation-home.spec.mjs` 有搜索回车到真实对象、编辑真实 Authority 草稿后拖文档并取消/接受原确认、文件到旧上传窗且零自动上传请求两条；独立 `e2e/home.config.mjs` 精确选择两条且单 worker；最终项目匹配由质量代理统一纳入 foundation-pages。

## 自行裁决

1. `/work/catalog` 无可靠的人员筛选，因此标题为“本机构待处理”，不按展示姓名猜测个人归属。
2. 指标以同源明细抽屉承接第一跳，避免既有目录默认筛选使总量与落地页不符；高级明细不堆在首页。
3. 最近访问用独立 `zy-recent-v1:` 命名空间，不放宽旧 `ontology-one-` / `ontology-one.` 缓存清理。读取校验完整身份 envelope，每身份最多八个对象、按对象路径去重。只存对象路径、短 tab/view/section/page/锚点、通用对象编号标题和时间；不存任意查询正文、回跳地址、文件或业务明细。R4 服务端化前本机跨登录保留。
4. CSV 必须选择去向；混合批次允许将兼容的一组带入旧页面，其余文件继续留在 Shell 内存中。这是格式歧义所需的一次选择，没有新增业务必填字段。
5. 原 Overview 的四个长列表按 P-16 取消首页复刻，去向逐项保留；首页 simple/pro 使用相同必要信息结构，不隐藏业务操作，视图偏好仍由 Shell 负责。
6. 把阶段性网络失败与真实空数据区分；不读取占位 readiness 来宣称模型可用，不显示虚构任务或成果。

## 验证记录

测试先行：初始契约测试因未实现模块失败；追加用例先复现 CSV 混批自动路由、权限撤销仍接收、关闭动画残留文件名、未确认交接不可恢复、范围变化残留文件，分别修复后通过。

- 原 Home/search/UI 定向 81 项通过，新增范围隔离/确认态用例已覆盖。
- 真实 Materials/Datasets 接收器六项先出现四个预期失败、两项容量拒收通过；引用去重与完整入队后确认修复后全绿。模拟上传 FormData 逐份核对原始 File 引用，ack 事件时真实旧 DOM 队列已完整显示两行。
- 导航消费方取消两项先失败；接入 helper 后与 receiver/原搜索/文件用例合计 26 项通过。
- 九项实际 Authority 组件桥测试：临时恢复本树基点原文件时三项失败、六项通过；finally 恢复桥文件后九项全绿。旧原文件未提交。精确确认文案仅在测试中引用 home_ 词表，不改变原语义。
- 一轮与全后端重型任务重叠的 34 项定向运行因 5 秒超时失败：首个搜索用例耗 23.6 秒并产生后续 act 连锁失败，CSV 关闭用例耗 9.1 秒。保留 `work/home-load-failure.log`，不提高超时、不弱化断言，不把这次运行列为通过。
- ESLint、Prettier、Stylelint、词表、令牌、生成词表、legacy 收缩/导入边界检查通过。
- 空闲串行环境全前端 250/250 通过（21 文件），完整重跑覆盖此前负载失败的全部用例；legacy + strict TypeScript 通过，CSS/声明隔离 9/9 通过。
- 生产构建与真实新两条 E2E：产品 checkpoint 后由 run.py 导出该 HEAD 并构建/执行，完成后补实际结果；不得把以上单测视为浏览器通过。
- 最终原 75 + Shell 与 A.2 截图由质量工作包在集成 SHA 复跑。

`requirements.txt`、两条受保护 Windows workflow、`frontend/src/legacy/api.ts` 与基点逐字节不变。无新依赖、无密钥文件、无上传原文持久化、无 push/PR/tag。AGENTS/STATUS/PLAN 由根代理统一整合；代码地图新增 `features/home`、Shell 的 RecentRecorder/HomeIntake、`foundation-home.spec.mjs`。

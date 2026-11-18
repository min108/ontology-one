# R4-B（下）旧页面操作对照清单

> 规划方 2026-11-19 按交付提交 `5962caa` 的代码盘点，供 B 段（下）下线“业务资产、资产详情、知识专栏”三个旧页面时逐条对照（蓝图附录 A.2、守则 §2 第 21 条）。“新页面现状”一栏是读代码得出的，没有逐项在界面上实测；B 段（下）启动前由规划方复核并补成动作清单。旧页面在清单逐条通过之前不得删除。

权限说明：旧页面的按钮由 `legacy/components/AccessButton.tsx` 控制，没有显式权限时按按钮文字推断——新建、提交、编辑、修订、归档、确认、调整、移除、保存、导入需要编辑权限，审核通过、退回、发布需要审核权限。

## 1 业务资产列表（`/assets`，`legacy/pages/Assets.tsx`）

| # | 操作 | 条件 | 后端接口 | 新页面现状 |
|---|---|---|---|---|
| L1 | 切换数据、规则、技能、知识四类 | — | `GET /api/catalog` | 部分：目录页有种类选择 |
| L2 | 分类目录逐层展开、选择、清除分类、分组卡片 | — | `/api/asset-catalog/tree`、`/items` | 没有 |
| L3 | 规则按“业务主题”或“依据法规”两种方式查阅 | 规则 | 同上 | 没有 |
| L4 | 分面筛选：办理状态、资产来源、归集方式、规则类型、方法类型、资产归属 | — | `/api/asset-catalog/items` | 没有 |
| L5 | 检索、清空检索 | — | 同上 `q=` | 有 |
| L6 | 连同历史条目检索、恢复当前成果口径 | 有历史条目时 | 同上 `history=all` | 没有 |
| L7 | 展开全部条目、分页 | — | 同上 | 有分页；无“展开全部” |
| L8 | 打开资产 | — | — | 有 |
| L9 | 手工新建（数据、规则、知识） | 编辑权限、非只读 | `element.create_draft` | 部分：组件已在新目录下，但入口只挂在旧页，保存后跳旧详情页 |
| L10 | 下载标准模板、下载空模板 | 非知识类 | `GET /api/asset-import/template/{kind}` | 没有 |
| L11 | Excel 一键导入、导入结果（读取、新增、更新数与错误）、重新选择 | 编辑权限；`.xlsx`、不超过 30MB | `POST /api/asset-import/{kind}` | 没有（蓝图允许以承接块保留，调用原接口） |
| L12 | 查看已保存的来源台账、查看接入原表 | 导入后；数据类 | `/api/data-instances/summary` | 没有 |
| L13 | 进入技能开发、查看资产关联（图谱） | 技能类；— | — | 没有 |
| L14 | 知识目录（知识类时嵌入，见第 3 节） | 知识 | 见第 3 节 | 没有 |

## 2 资产详情（`/assets/:id`，`legacy/pages/AssetDetail.tsx` 及其组件）

历史条目与受控技能包不显示编辑、关联定位和流转按钮。

| # | 操作 | 条件 | 后端接口 | 新页面现状 |
|---|---|---|---|---|
| L20 | 编辑内容：名称、摘要、领域、各类字段、修改说明；已发布的保存后回到草稿 | 编辑权限 | `PATCH /api/entities/{id}` | 部分：要素页只能补门禁字段，不能改名称、摘要、领域和正文；已发布条目只有“前往原编辑页” |
| L21 | 知识文章的章节编辑：改标题与正文、增加、移除章节 | 编辑权限 | 同上 | 没有 |
| L22 | 保存并关联定位 | 编辑权限 | `PATCH` 后打开定位 | 没有 |
| L23 | 提交审核 | 草稿或已退回；编辑权限 | `POST /api/entities/{id}/action`（submit） | 部分：要素页“提交独立审核” |
| L24 | 审核通过、发布资产（两步） | 待审核 / 已审核；审核权限 | 同上（approve、publish） | 部分：审核中心把两步合成“通过并发布”；**已审核未发布的存量内容没有入口** |
| L25 | 退回修改 | 待审核；审核权限 | 同上（return） | 有 |
| L26 | 发起修订 | 非草稿；编辑权限 | 同上（revise） | 没有（后端已有 `element.start_revision`） |
| L27 | 归档 | 已发布等；编辑权限 | 同上（archive） | 没有 |
| L28 | 关联定位：AI 或本地两种方式、补充检索、载入已有记录、逐项审定、确认 | 确认需审核权限，草稿或已退回，写明审定人与说明 | `/api/placement/{id}/suggest`、`/candidates`、`/history`、`/confirm` | 没有（蓝图允许以承接块嵌入原面板） |
| L29 | 关联与依据：按已确认、候选、已排除筛选，分页，点开对象，在图谱中展开 | — | `GET /api/relations?node=` | 部分：只有文字列表 |
| L30 | 修订历史：列表、查看某一版内容 | — | `GET /api/entities/{id}/versions` | 部分：只列版本号、时间、原因 |
| L31 | 还原为新草稿 | 管理员且有编辑权限；非受控技能 | `POST /api/entities/{id}/restore` | 没有（后端已有 `element.restore_version`） |
| L32 | 按指定版本阅读（从专栏进入） | — | `GET /api/entities/{id}?version=` | 没有 |
| L33 | 数据实例：翻页、查看全部、完整原行、实例关系图、核对字段方案、刷新、进入数据导入 | 数据类 | `/api/data-instances/{id}`、`/rows` | 没有（蓝图允许以承接块嵌入） |
| L34 | 外部来源血缘 | 数据类 | `GET /api/sources/lineage/{id}` | 没有（同上） |
| L35 | 材料出处、原文定位、来源网页、法规原文 | — | — | 部分：有锚点的出处可在抽屉看原文 |
| L36 | 保存可见范围 | 编辑权限且后端允许 | `/api/federation/entities/{t}/{id}`、`…/visibility` | 没有 |
| L37 | 知识收录专栏：调整、确认、移除（须填理由） | 有章节的知识；编辑权限 | `/api/knowledge/memberships/{id}`、`/collections/{cid}/members` | 没有 |
| L38 | 受控技能包：打开技能工作台、升级为技能包 | 技能类 | — | 没有 |
| L39 | 其他跳转：关联图谱、变更复核、所属单位、编排权责内容、整理这项工作经验、开始新的智能体分析、查看最新版本、阅读整合后的完整成果、权责规则里的单位与人员 | 各自条件 | — | 只有“查看要素详情” |
| L40 | `/assets/:id` 还承载单位、案例、问题、分析记录等非资产对象 | — | — | 待核实：新要素页能否打开这些对象 |

## 3 知识专栏（`legacy/pages/Knowledge.tsx`，含 `/knowledge/collections/:id`）

新目录下目前没有任何专栏相关代码。

| # | 操作 | 条件 | 后端接口 | 新页面现状 |
|---|---|---|---|---|
| L50 | 四种阅读方式：业务主题专栏、专题成果集、经验札记、全部知识 | — | `GET /api/knowledge/collections?scope=` | 没有 |
| L51 | 检索、办理状态筛选、分页 | — | 同上、`/api/asset-catalog/items?kind=knowledge` | 没有 |
| L52 | 新建专栏：用途、所属专题、导读 | 编辑权限 | `POST /api/knowledge/collections` | 没有 |
| L53 | 进入专栏；按知识进度筛选（已发布、整理与审核中、全部） | — | `GET /api/knowledge/collections/{id}?state=` | 没有 |
| L54 | 收录知识：搜索、选择、填理由、确认 | 确认需编辑权限 | `POST …/{id}/members` | 没有 |
| L55 | 阅读目录、章节链接、阅读全文（需复核时带版本）、回看来源专题 | — | — | 没有 |
| L56 | 专栏的可见范围 | 同 L36 | 同 L36 | 没有 |

## 4 下线时要改的站内链接

- 前端：`app/navigation.ts`（菜单与两处映射）、`app/AppRoutes.tsx`、`app/EntityDossierRoute.tsx`、`app/LegacyOwnership.tsx`、`features/home/Home.tsx`、`features/home/commands.ts`、`features/ontology/ManualAssetCreate.tsx`、`ElementPage.tsx`（两处）、`ElementRepair.tsx`、`legacy/domain.ts` 的 `entityHref`（被全局搜索、`EntityLink`、导航等间接使用），以及仍保留的旧页面里指向 `/assets` 与 `/knowledge/collections` 的链接（素材、台账、数据源、审核维护、变更复核、我的工作台、图谱、技能开发、权责规范、任务、专项、经验萃取等）。
- 后端生成的地址：`backend/ai.py`、`kernel/gates.py`、`kernel/gate_fields.py`、`capabilities/elements.py`、`data_instances.py`、`legacy_harness_runs.py`。
- `e2e/` 与 `docs/` 里的旧地址未统计。

## 5 三个旧页面专属的 v1 接口

- 列表与知识目录：`/api/asset-catalog/tree`、`/api/asset-catalog/items`、`/api/data-instances/summary`、`/api/asset-import/{kind}`、`/api/asset-import/template/{kind}`。
- 资产详情：`/api/entities/{id}/restore`、`/api/placement/{id}/*`、`/api/sources/lineage/{id}`、`/api/data-instances/{id}`、`/api/data-instances/{id}/rows`。
- 知识专栏：`/api/knowledge/collections`、`/api/knowledge/collections/{id}`、`…/members`、`/api/knowledge/memberships/{id}`。

按蓝图 §13.8，v1 接口在调用方下线之前保持行为不变；新页面可以直接调用它们，或改调同一服务函数。

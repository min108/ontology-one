# R1-5 机构切换前保护：子包交接

起点：`6c5a969ada9674c68d80d3074011e55411125d2e`，VERSION 1.0.0。公共接口首提交 `bb58e23516e001aebb01c716cb8f277b7c3cbd32` 已供会话子包直接合并。此包不修改 `legacy/api.ts`、新会话/请求模块或外壳路由；沿用机械搬迁的专用旧弹窗 Portal 与 Toast 代次保护。

## 对外接口

`src/state/workspace-guard.ts` 提供 `registerWorkspaceGuard(readDirty)`、`beginWorkspaceUpload()`、`readWorkspaceGuards()`、`setWorkspaceTransition('idle'|'confirming'|'leaving')`、`isWorkspaceConfirmationOpen()`、`isWorkspaceLeaving()`。登记与上传返回幂等释放函数；使用独立 Symbol，旧释放回调不能删除新登记。`ontology:scope-cleared` 清内存登记和上传，保留 leaving；leaving 后不接受新登记、上传或暂存元数据写入。

`readWorkspaceGuards()` 返回 `dirtyCount/uploadCount/storedDraftCount`。这些是信号数、上传批次数和浏览器暂存键数，**不是文档份数**；外壳确认应按是否大于零选择说明。取消确认只恢复 idle，不清登记、缓存或草稿。

`app/useWorkspaceGuard.ts` 的 `useWorkspaceGuard(dirty)` 保持最新已提交 React 状态，卸载注销；非离开的清理事件后重新登记，覆盖账号改密后组件继续挂载的情况。附加辅助钩子：

- `useWorkspaceDraftGuard(value, initial?)`：仅比较传入的业务字段，返回提交成功后更新基线的函数；传入捕获的已提交快照，不能传保存完成时的新编辑值。
- `useWorkspaceDialogGuard(key, value)`：显式弹窗身份与业务字段；打开时捕获初始值，关闭即结束该弹窗草稿，不监听 DOM 输入。
- `useWorkspaceInstructionGuard(value, operation, entityId)`：按既有 `ontology:harness-run-started` 的操作、对象与已受理指令更新基线；不使用 Toast 推断保存成功。新输入仍保留 dirty，已受理后台任务不当作文件上传。

`trackStoredWorkspaceDraft(storageKey, dirty)` 仅供旧暂存钩子使用，只存 dirty/clean 元数据，不复制业务内容。

## 业务适配清单

| 文件（均在 src/legacy 下） | 信号与保存边界 |
|---|---|
| navigation.tsx / pages/AssetDetail.tsx | 现有六个 useSessionValue 以服务端初始值比较；存储元数据保留到弹窗关闭之后。原草稿键删除后忽略元数据，避免保存成功误报。历史无元数据键保守归为“浏览器暂存”，不声称内容已修改；历史恢复及办理意见也接入。 |
| pages/Authority.tsx | 复用 title/summary/spec 的现有 dirty；规范创建、人员登记、办理意见与未接入原始材料显式接入。已确认离开时跳过既有 beforeunload、链接、popstate 的第二次确认。 |
| pages/Experience.tsx | 候选编辑复用现有快照；创建/合并弹窗、未提交原话与调整指令；原话/调整指令只由匹配后台受理事件或真实成功更新基线。 |
| pages/MaterialDetail.tsx | 候选编辑字段；阅读工作台待保存标题、边界、建议标题修改与整理指令。标题仍按原先 onBlur 保存，失败仍脏，成功只清除对应已提交值。 |
| pages/DatasetDetail.tsx | 数据项草稿、标准对应字段、粒度弹窗及待采纳字段选择；候选查找和分页不纳入。 |
| pages/DataSources.tsx | 来源配置、语义映射表单与源访问授权；授权按账号比较既有权限和最近成功提交值，排除标准搜索与候选缓存。 |
| pages/SkillDevelopment.tsx | 技能创建、业务说明、办理理由、参考选择与未发送指令；继承参考和成功受理时推进基线。 |
| pages/Projects.tsx / ProjectDetail.tsx / SpecialProjectDetail.tsx / project-workspace.tsx | 专题创建与安排、任务创建、专项各业务弹窗、配置工作对象；搜索、类别筛选、展示页签不纳入。 |
| pages/TaskDetail.tsx | 办理说明单独保存基线；安排/办理意见弹窗。成功仅确认实际提交的说明。 |
| pages/Knowledge.tsx | 专栏创建、收录选择与理由；查找条件不纳入。 |
| pages/Maintenance.tsx / Impact.tsx / Group.tsx | 审核理由、复核任务表单、跨机构关联与审核理由。 |
| pages/Applications.tsx | 未发送问题；已有消息和后台运行不计为未提交输入。 |
| pages/Administration.tsx / IntegrationApplications.tsx | 账号/授权弹窗、集成应用编辑与凭据办理表单；列表搜索不纳入。 |
| components/AssetPlacement.tsx | 人工修改的关联复核列表与理由；读取既有审定记录设置基线，搜索框不纳入。 |
| components/EntityVisibility.tsx / HarnessConfiguration.tsx | 可见范围、模型配置明确字段；服务端初始载入与保存成功推进基线，操作测试不充当保存。 |
| components/ui.tsx | 新确认框打开时暂停旧 Modal 的 Escape/Tab、延迟首次聚焦和清理恢复聚焦；专用 Portal 不变。 |

上传生命周期逐一接在 `Materials`、`Datasets`、`Assets`、`SkillDevelopment`（压缩包与参考附件）、`Experience`（导入记录）、`Authority`（文件/描述原文）真实异步入口。整个批次共用租约，跨文件空隙仍在；卸载组件不提前结束实际上传。Authority 原文接入完成即释放，再等助手不算上传。待处理/失败队列属于未提交内容，成功队列不触发。没有将旧 `externalPending`（其中还含助手轮询）用作上传计数。

账号安全的 `legacy/auth.tsx/PasswordChange` 由会话子包负责接入同一钩子；Login 不登记。

## 验证

公共契约测试先出现模块缺失失败，再实现通过。新增单测覆盖：登记最新值/恢复/清理；非离开清理后继续编辑；匹配后台任务受理、旧指令不覆盖新输入；提交中新增修改；取消确认不改变登记或上传；离开后的旧效应不能复活登记；暂存初始值、恢复原值、历史键及保存删键。

真实旧组件集成覆盖：Authority 修改/恢复、保存失败、保存成功和离开确认只发生一次；Asset 暂存钩子关闭后仍可检测；Modal Escape/Tab/延迟聚焦暂停；真实台账两文件上传、两文件之间租约仍在、完成后无误报。

完整前端 73 测试（本包新增 18 项）、strict/build、5222 个旧 CSS 选择器检查均通过；新增保护测试还覆盖模型配置迟到加载不得把已输入的新密钥误记为已保存。lint、format、stylelint、令牌/词表/legacy 检查通过；最终提交 SHA 随交接给出。未执行本包无关的数据库/SDK 全回归，root 合并后统一验收。

## 自行裁决与边界

1. 不以输入事件、所有弹窗打开、所有请求或成功 Toast 推断业务 dirty。后续新增编辑块必须显式注册，不能把本方案描述为自动识别任意表单。
2. 只有 AssetDetail 已存在浏览器草稿持久化；本包仅补元数据，不另建自动保存流程。用户主动关闭普通弹窗仍按旧流程丢弃其临时编辑，不能恢复为持久化草稿。
3. 无元数据的历史暂存只能说明“浏览器暂存将清除”，不能准确推断是否较服务端有变化；确认文案由 root 外壳负责。
4. 原请求代次只在 fetch 返回处检查，旧业务仍依靠切换阻断、作废请求并整页重载；本包没有重写请求封装。新 client JSON 解析后的代次校验、Query 清缓存及成功/失败均重载由会话子包验收。
5. 本包保留旧页面保存、审核与助手流程；没有添加公共离开路由拦截器。Authority 已有的离开保护保留，其机构切换绕过仅在用户确认后 leaving 生效。
6. 新模块不增加可见页面或外网资源；整合后的外壳确认框与旧页面三角色冒烟、1366/125% 布局由 root 统一验证。

## 源访问授权补验

补齐 SourceGrants 的显式权限差异信号。选择现有授权不误报，恢复权限原值即清洁；新账号的待授予权限需确认。失败保存不推进基线，成功保存仅记录对应账号的已提交权限，保存期间继续修改仍保持未保存状态。未改变原有选择、撤销或保存请求流程。

先运行新增反例得到两项失败，再实现通过。旧业务集成测试共 8 项通过，新增 2 项覆盖权限恢复、失败/成功保存及提交期间继续编辑。TypeScript、lint、format 和词表检查通过；legacy/api.ts 未改动。

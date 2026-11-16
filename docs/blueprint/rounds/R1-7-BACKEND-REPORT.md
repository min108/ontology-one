# R1-7 后端交接

起点 `ebc24f86f802f1d886ce07471303f97f2af54664`，版本 1.0.0；此包不删除前端旧页。前端 A.2 与浏览器验收由身份前端工作包完成。

## 细项和接口

| 条目 | 实现和核验 |
|---|---|
| 7.b / H.2-1 | 强制改密条件全部移除；旧入参接受但忽略，数据库历史列保留，账户创建/重置/初始化显式写0，迁移0005清旧值。会话顶层和user.must_change_password恒false。主动改密、密码强度、限速、撤销、CSRF、角色/只读/身份类型守卫保留。 |
| 7.c / H.2-3 | 0002增加九个目录字段；既有ID、org_id、db_name冻结，只在空目录播种。新增org-加8位十六进制。HQ超管管理；不提供删除或自动重试开通。层级不继承授权。 |
| 7.d | 0003新增duties JSON空数组；_session_row显式投影，无额外热查询。只保存/读取，不提供职责授予入口；未知职责不产生权限，角色创建与修改仍仅admin/modeler/reviewer。保留Principal的JSON数组转tuple身份规范化；duties加入捕获身份和控制事务提交前复核，职责变更后旧捕获身份拒绝读写。 |
| 7.e | 会话原permissions布尔映射和roles保留；新增顶层permission_keys数组与password_change_recommended布尔值；whoami.permissions仍数组。查询范围仍本机构。 |
| 7.f | 0004创建sec_user_prefs；GET /api/v2/prefs、PUT /api/v2/prefs/{key} {value}均返回{preferences:{...}}。身份仅取认证上下文；write同事务追加pref_key审计，不记录值。view_mode simple/pro，前端professional须映射。 |
| 7.g | 四个能力org.manage/user.manage/grant.manage/audit.query登记；前三个state_change/L3，assistant与MCP不开放，无虚假持久幂等声明。用户/授权能力复用v1 store服务。独立包精确9，R1-8合入后root精确12并重生成类型。 |

读取路径为GET /api/v2/users、/grants、/orgs、/audit。users返回全部可管理账号（HQ全部、本机构管理员仅本机构）；grants仅HQ超管。orgs供管理选择器，HQ全目录含停用/失败，本机构管理员仅自己的active机构，不回数据库名称。org.tree维持普通会话可读的目录元数据能力，仅返回active目录并补parent_id/sort_order/level。

写入统一POST /api/v2/capabilities/{id}:invoke。精确DTO在backend/api/v2/{orgs,users,grants,audit,prefs}.py；生成物在frontend/src/api/generated。user.manage通过action=create+create旧UserCreate，或action=update+user_id+changes旧UserUpdate；grant.manage通过action=set+grant旧GrantCreate或action=revoke+grant_id；org.manage通过action和机构字段。写返回{id,ok:true}。既有错误外形{error:{code,message,next_action,request_id}}不变。

audit.query和GET /api/v2/audit仅HQ超管，支持actor_id/tenant_id/action/outcome精确筛选、含时区since/until、page/page_size（默认30，最大100）。P-18明确保留v1机构管理员查询本机构审计，未收窄或放宽原服务。AuditRow所有现有字段显式建模。

## 目录事务和失败处置

目录编辑持部署级命名锁，防止并发父子调整形成环。改名及层级/排序修改使用同一MySQL连接上的跨库InnoDB事务，目录、根节点标题/版本快照、追加式审计一起提交；审计失败回滚两库。ID、库名和历史版本保持。根快照明确写目标机构的owner_tenant_id/owner_org_id，不能沿用发起HQ请求的上下文归属。修改已停用机构须先启用。不能停用HQ，不能从当前工作区停用自己正在使用的机构，须先切回HQ。

新机构先提交status=provisioning的目录与pending审计，不能登录、不能进入联邦查询范围。内部调用provision_tenant(pending=True)，仍从已提交服务端目录派生库名、复用逐库迁移锁与fresh_only保护。机构完整初始化（包括根节点）后才active；失败为provision_failed并记录失败元数据，不自动DROP、不重试部分库、不允许在线enable失败目录。目录未激活前不会创建业务账号。

运行门仅忽略明确新org-标识的provisioning/provision_failed目录；active或disabled旧库的版本检查不变。未知部分表仍被备份分类/可信结构验证拒绝，绝不将未知结构塞进恢复白名单。

失败后操作步骤：停止API、智能体和任务worker等全部共享实例，保留目录、失败审计与完整数据库副本供离线检查；核对该ID、完整派生库名、迁移历史及最后成功阶段。已发布结构可用统一迁移/受信恢复工具处理；未知partial结构需要管理员先核对原始DDL失败原因并按已发布迁移恢复到可信状态，不能直接删除或把status改为active。若采用升级前完整备份回退，按现有独占维护锁流程整部署恢复（包括控制目录、机构库及上传原件），再重新迁移。此包不新增在线修复或通用repair CLI；未知partial没有一键自动恢复保证。

## 恢复契约和表分类

0001文件及v1.json原字节保留。可信恢复版本按已校验schema_migrations固定列/DDL、连续version、安装包name/checksum推导。显式control映射0/1→v1、2→v2、3→v3、4/5→v4；tenant仅0/1→v1。未知历史长度拒绝。0005只修改数据，沿用v4结构。不接受列集合并集，也不执行备份带来的SQL。验证、留存与实际恢复使用同一选择器。历史fixture loader仍可只加载stdlib recovery_schema.py，trusted_schema(kind)默认返回v1兼容旧调用。

sec_user_prefs为控制库用户偏好，进入完整部署备份/恢复，不进入机构业务备份；sec_tenants和sec_users新增列同属控制库，所有sec_audit触发器保持。entities 12列、relations 7列和机构业务表分类完全不变。新v2/v3/v4表定义由真实MySQL SHOW CREATE生成，审计触发器复用v1无DEFINER定义，不额外要求SET_ANY_DEFINER；MySQL二进制日志开启时仍遵守原有触发器权限/配置前提；校验仍使用原有仅规范化等价输出的normalized_ddl。

## H.2与测试环境变化清单

旧测试ID全部保留。额外的H.2-1间接边界：Principal.must_change_password保留传输和值，但compare=False，不再使旧队列/readonly/MCP快照仅因该位不同而拒绝；auth_version、tenant、role、duties等其他差异仍拒绝。只有以下旧语义断言改变：

| 文件与用例 | 原语义 → 新语义 | 授权 |
|---|---|---|
| scripts/check_security.py运行入口 | 初始账号flag真且业务403 → flag假且业务200；继续主动改密并验证旧会话401 | H.2-1 |
| tests/test_skills.py::test_application_readonly_and_reviewer_write_denied | flag主体也403 → flag主体可通过角色检查；application/readonly/reviewer写拒绝原断言保留 | H.2-1 |
| tests/test_applications.py::test_credential_and_delegation_fail_closed | 委托flag使凭据无效 → flag不影响；过期/撤销/停用/版本/角色等失败断言原样保留 | H.2-1 |
| tests/windows_portable_smoke.py初始化登录断言 | flag真 → flag假；不取消其他Windows检查 | H.2-1 |
| scripts/check_security.py::federation_scope | 固定51与各类别数量 → 目录ID唯一、唯一HQ、类别白名单与当前测试用到机构存在；全部授权/可见性断言保持 | H.2-3 |

测试装配/登记演进（不是H.2安全放宽）：test_jobs_mysql、test_demo_mysql、test_federation_mysql初始化改走run_migrations，保留全部用例及业务断言；真实0.7生成器与快照不变。test_baseline_preserves_every_old_row_and_column仍跑完整当前迁移，控制库旧列逐行投影比较（仅must_change_password按H.2归一0），新列集合另断言，其他表原hash不变；新专项测试逐字段核对九目录列。test_baseline_sequences_have_tests保持原ID，控制序列明确[1,2,3,4,5]、机构序列[1]；fresh control显式版本5，并专项断言连续五个文件名称和可信checksum。能力契约原用例ID保留，精确集合变9，逐能力effect/confirm及权限逐格保持，新增写输入使用合法DTO以继续测试到权限后置检查。

## 自行裁决与保留行为

- P-21用真实审计中最近一次创建/管理员credential_reset/主动password_change派生建议，不增列、不把偏好当安全状态；缺少相关历史时不捏造建议。
- prefs键白名单：三个枚举；onboarding为至多50个布尔完成项；graph_views为至多50个仅id/name/perspective/filters/focus字段对象；整体至多16KiB。后续页面若扩契约需显式验证。
- 创建机构类别不允许第二个headquarters；父级须有效active且无环。改名/层级写保留旧根快照，演示/mirror字段仅迁移存储，本轮无管理写入面。
- L3只接受页面绑定。前端按后果确认；不引入额外确认令牌、未登记幂等表或后台自动执行。
- 用户管理的自动HQ超管、最后超管保护、自改角色/停用限制、修改目标撤销所有会话、默认显式授权与管理原因保持原实现。

## 验证

新增可收集用例66项，当前收集838项（起点772）。验证按实际顺序记录，不能称为一次全绿：

| 检查 | 实际结果 | 日志 |
|---|---|---|
| 首次全后端，三个开关开启、真实tmpfs | 834项，794通过、39跳过、1失败，509.428秒；唯一失败为test_baseline_sequences_have_tests仍声明control仅[1] | work/full-backend.log |
| 版本清单修正及旧flag/职责身份边界补丁后的定向相关集 | 107项全部通过；含真实MySQL读写/提交复核、序列声明、readonly/MCP/queue帧、既有Principal JSON回归、应用身份和后台运行 | work/identity-final.log |
| P2先红后绿 | 先证明无DEFINER断言和HQ跨库归属断言失败；修复后定义、归属、跨库回滚3项绿；无SUPER/SET_ANY_DEFINER账户的整库恢复另1项绿 | work/p2-red.log、work/p2-green.log、work/restricted-restore.log |
| 8个旧回归脚本 | restore_v020/security/workflows/sources/mcp/entity_storage/agent_runtime首轮退出0；skills首轮20秒容器启动观察未出现，其他9组通过，原样第二轮10组全过、退出0（73.44秒）；未改时限/原断言 | work/regressions-first.json、work/regressions-final.json、work/check_skills-first.log、work/check_skills.log |
| 流水线 | 16项通过 | work/pipeline.log |
| 静态 | Ruff及259文件format、迁移登记、默认拒绝路由覆盖、生成API字节一致检查通过；git diff --check干净 | 包内命令输出 |

39项跳过与真实0.7基线跳过集合完全一致。运行依赖、两个受保护workflow、两份0001迁移、v1恢复定义及历史fixture原字节不变。所有本包测试服务和验证子进程均退出，临时恢复账户数0；数据库trigger trust还原原值0。首次skills等待失败保留为环境/时序观察失败证据，没有证明其根因，不能宣称修复了生产缺陷。

最终838项完整重跑由root在集成主干执行；本报告所属提交是最终交付代码，不把较早834项完整运行冒称为此提交的一遍全绿。前端strict编译、浏览器和A.2由对应包及root执行。本包未重建前端。工作日志位于ignored work/，不包含真实凭据。Linux的39个Windows原生跳过仍不是原生验收；两个Windows冒烟代码仅更新已授权断言，原生执行由负责人验证。前端types最终由root在R1-7/8组合后重生成，旧legacy/api.ts不改。

受限恢复测试使用仅本测试命名空间数据库权限的账户（无SUPER/SET_ANY_DEFINER）；因测试服务器开启binlog且原log_bin_trust_function_creators=0，用例以root临时保存/开启/最后恢复该变量，避免把数据库既有触发器前提误判为新增DEFINER权限。生产工具不修改该变量。

## 真实身份 E2E 后的总部兼容修复

身份浏览器发现GET /api/v2/orgs返回500：真实0.7控制目录中HQ的org_id为null，而DTO原先要求string。现明确允许null，未迁移或覆盖任何旧org_id。顺带核实原db.ensure_tenant_schema及真实HQ夹具：业务根ID恒为tenant id（hq），不能使用可空的目录org_id定位。根快照两归属字段均取目标tenant id，符合1359118的entity_json投影语义；目录org_id=None、实体自身org_id='hq'各自原样保留，不修改全局entity_json。

新增4项回归，修复前纯契约两项分别以DTO校验失败和根查询参数None失败；修复后共6项定向通过（24.213秒），包含真实迁移HTTP读取、从dept-01工作区调用HQ改名/归属/事务回滚，以及已有反方向HQ调用dept-01的归属和回滚。API生成物同步，Ruff/生成一致性通过。日志work/hq-null-red.log、work/hq-null-green.log。包内可收集数相应为842（新增70）；完整集成结果仍由root独立核验，不据此追加全量通过声明。

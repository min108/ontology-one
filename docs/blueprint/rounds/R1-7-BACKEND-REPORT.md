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

## H.2 最终静态逐项审计（代码 4f29db8）

本附表先只读核对源码与现有测试；发现候选过滤缺少直接反例后，按负责人补充指令新增并单独运行1项真实MySQL测试，未修改运行代码。对照已格式化基线1090384核对计划清单，再以包起点ebc24f86到4f29db8的差异隔离本包变化；不用格式化前行号作定位。除下述新增单例外，证据是现有测试代码的具体断言，不等同于本次重新运行全部测试。

### 计数口径和覆盖限制

R1-PLAN §7.b列出的清单确实是14个文件、20个显式位置：其中19个是Python拒绝/可用性条件，1个是application_options的SQL候选过滤。不能将其描述为20个独立的if拒绝分支。相对R1-7包起点还有R1-3增加的authorize_v2条件1处，以及applications._same、Principal dataclass比较2处间接身份边界，共计23个授权/可用性相关变更位置。载荷恒false、创建/重置置0、0005清值另计，不混入23。

初审时19项有直接的“旧flag允许，其他条件仍拒绝”测试，application_options仅有静态证据。现补MI.test_application_options_keep_flagged_active_local_candidates_and_manager_guards关闭这一缺口：完整真实0.7夹具迁移后，将本机构active builder旧flag置1、同机构另一用户停用，精确断言候选集合仍含builder且不含停用/其他机构账号；真实modeler登录及实际签发/认证的application主体均403。没有mock _manager、require_admin、SQL或数据库。20个位置现均有直接或共用guard反例证据，但不把单个反例说成穷举全部相邻条件。

缩写：`PG`为`tests/identity/test_password_guards.py::PasswordGuardTests`；`MI`为`tests/migrations/test_identity.py`内同名方法。表中PG测试用的主体P显式携带must_change_password=True；MI的live-store测试同样构造flagged主体。

| 计划编号 | 取消条件的位置 | 实际保留的守卫 | 现有测试证据与范围 |
|---|---|---|---|
| 01 | security/policy.py::authorize | session身份、旧角色集合、只读原始API限制、路由默认拒绝、动作/字段级角色校验 | PG.test_v1_password_flag_allowed_role_still_checked：旧flag读通过，viewer不在旧角色集合而拒绝；scripts/check_security.py::run_checks保留角色、CSRF、越权与联邦范围检查 |
| 02 | security/store.py::assert_principal_current | 空主体/身份类型、实时会话与机构有效性、身份字段比较、readonly写拒绝；受控只读任务写拒绝 | MI.test_password_flag_does_not_bypass_live_store_guards：flag写通过，readonly写拒绝，auth_version/tenant/role/duties变更与停用后读写拒绝；tests/test_jobs_mysql.py::test_tenant_bound_jobs_storage_cache_restart_and_revocation保留只读任务写拒绝 |
| 03 | security/store.py::assert_job_principal_current | session类型、捕获机构、实时会话与账号有效性、身份字段比较、readonly写拒绝 | 同一MI方法直接调用此函数：flag写通过，身份四字段变化分别在读/写模式拒绝，账号停用拒绝；原jobs MySQL用例保留撤权与捕获工作区边界 |
| 04 | security/store.py::authorized_control_connection | session类型、受控只读任务禁止控制写；锁定actor/session、入口身份比对、提交前账号/会话/机构再验证；credential/revoke/workspace原有窄后置条件 | 同一MI方法：flag事务通过，身份四字段变更及停用拒绝；事务中改duties提交失败且回滚；原jobs MySQL用例直接验证只读任务不能进入此事务。此函数不新增通用role门，业务管理入口原require_admin仍负责该限制 |
| 05 | security/context.py::readonly_session_execution | Principal类型、session、所属机构、非readonly、guard可调用；has_readonly_session_execution继续比较捕获身份并调用取消guard | PG.test_readonly_execution_flag_allowed_foreign_still_denied、test_readonly_execution_application_still_denied；原jobs MySQL用例guard返回False时仍拒绝 |
| 06 | security/applications.py::_principal | 应用启用、凭据未撤销/未过期、委托账号启用、机构active、所属机构一致、有效角色、delegate_auth_version一致 | PG.test_application_flag_does_not_override_expiry；tests/test_applications.py::ApplicationsTest.test_credential_and_delegation_fail_closed保留全部8个非flag失败变体 |
| 07 | security/applications.py::_delegate | 用户存在/启用、同机构、角色白名单 | PG.test_delegate_flag_allowed_inactive_still_denied：flag允许、停用拒绝；另外两个条件以源码差异确认保留，不称本用例已逐个覆盖 |
| 08 | security/applications.py::_public | 凭据撤销/过期、应用启用、委托启用/同机构/auth_version仍决定usable及invalid_reason | PG.test_application_credential_public_flag_and_auth_version：flag不使usable变假，版本变化使其不可用 |
| 09 | security/applications.py::application_options | _manager要求session；require_admin经实时身份/写权限再要求admin；SQL仍限定home_tenant_id=当前机构、is_active=1；映射仍published且源enabled | MI.test_application_options_keep_flagged_active_local_candidates_and_manager_guards：旧flag本机构active账号入选、停用/其他机构不入选；真实非管理员与已认证应用主体分别403 |
| 10 | ai.py::_principal | session与admin/modeler/reviewer集合；assert_execution_principal_current(require_write=False)实时执行身份检查 | PG.test_ai_password_flag_and_application：flag允许、application拒绝 |
| 11 | ai.py::require_admin | assert_storage_write写权限、session、admin | PG.test_ai_admin_role_remains_required：flag admin通过、reviewer拒绝；测试替身替代storage guard，不能据此声称它执行了真实存储写检查 |
| 12 | demo.py::_principal | session；write时admin、非readonly、当前=所属机构；实时主体检查 | PG.test_demo_password_flag_and_cross_tenant_write：flag允许、跨机构写拒绝 |
| 13 | mcp_tools.py::_SessionPolicy._current | 当前=捕获主体、session、非readonly、实时主体检查 | PG.test_mcp_password_flag_and_readonly；test_legacy_flag_survives_frames_without_changing_readonly_identity另覆盖auth_version/tenant/role/duties变化拒绝 |
| 14 | sources.py::_role | 实时主体、调用方指定角色集合、非readonly | PG.test_sources_password_flag_and_role：flag允许、reviewer不在指定admin集合而拒绝 |
| 15 | integrations.py::invoke_as_session | authenticate_session无效401；use_context后调用原_invoke工具策略 | PG.test_integrations_password_flag_and_invalid_session：flag允许、无效会话拒绝；_invoke为替身，本测试不冒称覆盖工具内部全部策略 |
| 16 | skill_harness.py::_principal | session、admin/build各自角色、写路径非readonly；实时主体require_write=admin或build | PG.test_harness_password_flag_and_readonly_write：flag build允许、readonly build拒绝 |
| 17 | harness_runs.py::RunStore.submit | store关闭、session类型、实时主体、总数/本人运行上限、同一活动任务不重放；后续运行所有权/捕获范围复核 | PG.test_harness_submission_old_flag_reaches_live_guard：flag到达真实身份guard调用点，application先拒绝；guard替身主动抛错，不宣称已完成真实任务。原tests/test_harness_runs.py的test_every_detail_and_cancel_are_owner_and_tenant_bound、test_duplicate_active_work_is_not_replayed、test_captured_scope_downgrade_discards_a_prepared_result保留 |
| 18 | agent_tasks.py::current | 当前=所属机构、session、assert_execution_principal_current(require_write=False) | PG.test_agent_tasks_password_flag_and_cross_tenant：flag允许、跨机构拒绝 |
| 19 | skills.py::_role | 实时主体、session、角色集合、非readonly | PG.test_skills_password_flag_and_readonly；原tests/test_skills.py::Governance.test_application_readonly_and_reviewer_write_denied仍逐个验证application/readonly/reviewer写拒绝 |
| 20 | agent_tools.py::SessionToolBridge._current | check_cancel开启时cancelled或stopping拒绝、session类型、实时主体 | PG.test_agent_tool_password_flag_and_identity、test_agent_tool_cancel_still_denies：旧flag允许、application与cancelled分别拒绝；stopping条件以差异确认保留 |

额外三处：

| 位置 | 改动与保留边界 | 直接证据 |
|---|---|---|
| security/policy.py::authorize_v2 | 删除flag条件，明确session身份；保留角色集合、路由默认拒绝与permission keys包含检查 | PG.test_v2_password_flag_allowed_application_still_denied；tests/identity/test_contracts.py::test_v2_old_password_flag_does_not_block |
| security/applications.py::_same | 仅从比较字段列表移除旧flag；identity_kind、application_id、credential_id、permission_version、user/username、home/tenant/db、role/auth_version、session_id、is_superadmin、readonly全保留 | PG.test_application_flag_does_not_override_expiry验证仅flag差异允许、permission_version差异拒绝；原ApplicationsTest.test_live_version_comparison_and_scope_permissions保留版本/机构拒绝 |
| security/context.py::Principal.must_change_password | 字段和值保留；compare=False使JSON往返旧快照不因该位不同失配；其他dataclass字段仍参与比较 | PG.test_legacy_flag_survives_frames_without_changing_readonly_identity同时测readonly上下文和MCP捕获主体；test_queued_legacy_flag_frame_reaches_live_guard确认队列保留旧值并经过live guard、错误tenant拒绝。后者用guard替身，不称真实队列全链路验收 |

载荷/写入变更也已核对：store._principal、_joined_principal、_user_public、session_payload返回兼容false；bootstrap_superadmin/create_user/update_user重置路径显式0；control0005仅清旧值。MI.test_old_password_flag_is_cleared、test_new_org_account_can_login_without_password_gate、test_password_advice_follows_reset_and_self_change覆盖迁移、创建及重置后的状态。密码强度、登录限速、CSRF、主动改密撤销旧会话未取消；scripts/check_security.py::run_checks仍验证旧cookie401。

### 所有旧断言改写的精确清单

以下按ebc24f86..4f29db8的tests/scripts差异列出；新文件中的新断言不算旧断言改写。计划列明的0.7语义变化只有前五行；Windows代码只做静态核对，未在Linux冒称原生执行。

| 文件 / 精确用例或作用域 | 改写 | 分类 |
|---|---|---|
| scripts/check_security.py::run_checks初始admin登录段 | user.must_change_password真→假；GET /entities 403→200；报告标签forced change→optional change；紧随其后的主动改密、cookie轮换和旧cookie401保持 | H.2-1 |
| tests/test_skills.py::Governance.test_application_readonly_and_reviewer_write_denied | 只把旧flag变体从403循环移为成功断言；application、readonly、reviewer写三个失败变体保留 | H.2-1 |
| tests/test_applications.py::ApplicationsTest.test_credential_and_delegation_fail_closed | 只把must_change_password=1从无效cases移为非None断言；其余8个变体及非法token不查库断言保留 | H.2-1 |
| tests/windows_portable_smoke.py::Acceptance.account_and_crud | 顶层flag真→假；错误标识INITIAL_PASSWORD_CHANGE_NOT_REQUIRED→INITIAL_PASSWORD_STILL_BLOCKS；其他HQ身份与CRUD检查保持 | H.2-1 |
| scripts/check_security.py::run_checks.federation_scope | 固定总数51/类别数量1、1、12、37→非空、ID唯一、HQ唯一、类别白名单、场景所需5个机构存在；报告标签相应更新；其后的搜索结果、草稿/已发布可见性和workspace拒绝均不变 | H.2-3 |
| tests/migrations/test_integration.py::MigrationMySQL.test_baseline_preserves_every_old_row_and_column | 完整当前迁移仍执行。sec_users/sec_tenants因新增列改为所有旧列逐行投影比较，只有sec_users旧flag归一0；另外精确断言新列集合。其他表hash及tenant entities12/relations7均保留 | R1迁移结构演进；其中flag数据期望是H.2-1 |
| tests/migrations/test_integration.py::MigrationMySQL.test_fresh_install_control_and_lazy_tenant | current 1→显式5，完整初始化/运行门路径保留 | R1迁移登记演进，非H.2 |
| tests/migrations/test_framework.py::Discovery.test_baseline_sequences_have_tests | control [1]→明确[1,2,3,4,5]；tenant仍[1] | R1迁移登记演进，非H.2 |
| tests/capabilities/test_contracts.py::ContractTests.test_all_five_contracts_have_valid_models | 旧ID保留；精确集合5→9；前三个写能力effect/confirm须state_change/L3，其他须read/L0；additionalProperties/tool_name断言保持 | R1能力登记演进，非H.2 |
| tests/capabilities/test_contracts.py::ContractTests.test_every_role_matches_declared_permissions | 只有新写能力调用参数换为合法最小DTO，继续进入原权限/handler断言；旧5能力仍{}，异常期望表达式不改 | R1新增能力输入装配，非H.2 |

纯schema初始化装配更新另列，不是断言改写、不是H.2：`tests/test_jobs_mysql.py::JobsMySQLTests.test_tenant_bound_jobs_storage_cache_restart_and_revocation`内部异步test、`tests/test_demo_mysql.py::DemoMySQL.test_fixtures_are_drafts_idempotent_tenant_local_and_safely_archived`、`tests/test_federation_mysql.py::FederationMySQLTests.test_real_sessions_visibility_citations_links_and_revocation`三处仅把store.initialize_control换为统一run_migrations。原ID、业务场景与断言保留。真实1359118生成器、历史fixture、0001迁移与v1恢复定义没有改动。

本附表补齐用例的实际运行：仅新增application_options单例，1/1通过，5.189秒，独立随机MySQL命名空间按tearDown清理；日志work/h2-options.log。Ruff和该文件format检查通过，git diff --check通过。没有重跑全后端、SDK或8脚本；此小提交仅文档与新增测试，不改运行代码或生成API。包内用例数因此从842增加到843（相对起点新增71），不据此声称843项已完整运行。

## 验收门7：真实旧版与迁移后授权/审计输出对照

新增独立演练`scripts/drills/compare_identity_v070.py`，不是当前v2与当前store互相比对。旧端须为干净的完整提交1359118dd672431dcd0cde2eb81f2b0f994eef37；两个独立Python解释器各自导入对应版本backend.main，通过真实ASGI HTTP请求穿过该版认证中间件、策略、v1路由和MySQL服务。使用TestClient传输，不启生命周期/网络监听/SDK；原夹具加载和显式run_migrations负责初始化，未mock授权、SQL、时间或UUID。可用--current-source指定最终集成目录，默认本脚本所在检出。

同一套原0.7夹具库中，先由原版创建HQ、HQ切dept-01、部门admin、modeler、reviewer的真实会话，完成这5次登录和1次工作区切换后才采集输出。当前版迁移后复用相同会话，不额外登录。两端分别读取GET /api/admin/grants及逐页GET /api/admin/audit（100条/页），所有响应body/status、每条记录及每个字段直接相等，未过滤字段、历史ID或错误内容；比较阶段无需任何namespace重映射（只在原夹具加载时按现有装载器派生隔离库名）。

当前服务来源314d4a07f142ed3de7ad343eb5f19afee2e4d183（旧服务来源1359118dd672431dcd0cde2eb81f2b0f994eef37）上实际执行成功：五个范围、98条授权、15条原夹具历史审计ID均保留。HQ及HQ切dept返回同一完整21条审计，部门admin只见本机构11条，modeler/reviewer的grants和audit均403，部门admin的grants也403；所有这些响应在两版精确一致。比原夹具多出的6条审计是比较开始前原版真实登录/切换产生，两版均原样读取，不排除它们来“对齐”结果。全套before/after原始JSON与来源SHA保存在ignored work/identity-v070-comparison.json，运行摘要在work/identity-v070-comparison.log；不保存会话token，token只在父子进程内存管道中传递。4个独立随机命名空间数据库按finally清理，无共享配置修改，所有探测进程已退出。

这是独立短演练，未新增unittest收集项，包内收集口径仍843；不把它说成843完整通过，也不代替最终主干完整验证。root合并R1-7与其他包后应以--current-source指向最终集成检出再执行同一演练，登记最终SHA与报告；本段只声称已核验上述包内代码。命令示例（MySQL环境按既有安全方式注入）：

```bash
python scripts/drills/compare_identity_v070.py --v070-source /path/to/clean-1359118 --current-source /path/to/final-checkout --output work/identity-v070-comparison.json
```

对照成功后仅补充证据输出保护与安全stdout摘要：保存前拒绝任何本次会话token或合成登录密码进入证据；stdout仅两个来源SHA、五范围、完整响应等式成功、计数和清理数量，不含cookie/token/password。该输出保护变更已静态核对并通过Ruff/format，按负责人指令不再次运行MySQL；CI应对最终脚本和集成代码重跑并收集安全摘要。

### 对照工具的清理边界修复

负责人复核指出1198c4d的finally按40-bit命名空间前缀发现并删除库；若fixture因碰撞而拒绝加载，原写法可能删除预先存在的库。该问题属于演练工具清理授权缺陷，不能以低碰撞概率替代修复。

现直接复用e2e.run的new_namespace/database_names/DatabaseScope：命名空间为e2e_r1_加24位十六进制（96bit随机），总长31，机构库派生不会截掉随机位。固定HQ、dept-01、sub-01加control共4个准确候选名，排除裸namespace；进入scope先用精确IN查询确认候选均不存在，成功才armed并调用loader。清理只处理这一已预约集合中实际存在的库，未知同前缀库不进入SQL查询或DROP授权；若发现结果异常包含未知库，任何DROP前拒绝。部分loader失败也只按该集合清理，报告记录reserved/created/cleaned准确集合与计数；passed仅在scope清理成功返回后设为true。

新增tests/checks/test_identity_comparison.py六项纯单元测试覆盖4名派生一致、96bit末位不被截断/长前缀拒绝、碰撞不armed不装载不删除、未知同前缀发现拒绝全部删除、部分装载失败只删准确已创建集合、发现SQL仅IN而无前缀选择。6/6通过（0.008秒），Ruff/format/diff检查通过；日志work/identity-cleanup-red.log、work/identity-cleanup-green.log。HTTP比较逻辑未修改，本轮没有连接MySQL、执行SDK或完整回归；最终CI须重跑修复后的真实对照。独立包收集口径因此从843增至849（新增77），不声称849项已完整通过。

### 前端验收组合时的清理器契约适配

合入动态浏览器 runner 后，比较脚本的原精确查询断言发现共享 DatabaseScope 已支持 UI 新建机构的目录授权发现。比较脚本现单独派生 ExactComparisonScope，冻结四个预留候选，使用参数化 IN 查询；返回结果不属于这四个候选立即拒绝，即使是同 namespace 的合法租户 hash，也不进入父类目录扩展。HTTP 采集、逐字段比较与共享 E2E runner 均未改。新增合法但未预留 hash 反例先红后绿，比较清理 7 项及 runner/legacy 合计 36 项 pure 测试通过；本改动不声称重新运行过跨版本真实 MySQL 演练，最终组合由统一质量验收执行。

# R1-2 迁移、备份与历史兼容工作包

## 交付范围

起点为 `1090384`，VERSION 为 `1.0.0`。新增控制库与机构库两条迁移序列、逐库 `schema_migrations`、源文件 SHA-256、连续编号检查及 MySQL 命名锁。发现迁移只解析源码；执行阶段才加载已经校验的同一份字节，避免发现阶段副作用及旧 `.pyc` 与摘要不一致。

`scripts/migrate.py` 是离线唯一入口。`run_migrations(provision_all=False, progress=None)` 在同一次非阻塞独占维护锁内完成状态检查、待执行判断、必要的完整备份及校验、控制库和逐机构迁移。Compose/Windows 开通和管理员引导调用同一入口；运行账号创建也在同次独占锁内完成。Windows 新增 `backup` 阶段，两处阶段词表同步，计数使用固定总数 10000 且不回退。没有待执行迁移时不备份。

API、智能体服务及 worker 启动只执行 `validate_runtime()`；它不修改数据库结构，并标记已经校验的旧队列缓存，避免 worker 首次领取任务时再次建表。登录/切换与后续机构创建使用 `provision_tenant(tenant_id)`；库名只来自服务端目录。全新空库执行全部租户迁移；非空旧库不能通过在线开通路径升级。`migration_status(kind, database)` 提供当前版本、待执行项与现有表。

基线仍调用原 `initialize_control`、`initialize_federation`、`ensure_queue` 与 `ensure_tenant_schema`，没有另写初始化逻辑；统一创建 `demo_installations`。机构元数据原先每次 `REPLACE` 会改变 `updated_at`，改为 `INSERT IGNORE`，确保已有历史标记也逐行不变。`entities`/`relations` 保持 12/7 列。

## 完整备份与恢复边界

`ONTOLOGY_BACKUP_DIR` 默认 `data/backups`；Compose 使用独立的 `ontology_backups` 持久卷。升级备份包含完整控制库、全部已开通机构库、未开通目录状态，以及每机构可独立恢复的原格式 ZIP 包与上传原件。运行配置、外部凭据及加密主密钥不进入备份。备份校验完成后才写入持久升级状态并执行迁移；同一部署只保留最近 3 份完整备份，其他部署共用备份卷时不删除其文件。

`.ontology-upgrade-state.json` 与维护锁位于同一存储父目录。异常或进程中止后状态继续阻断新版服务；已有部署须通过 `scripts/restore_upgrade_backup.py <目录> --maintenance` 完整恢复后重迁。全新安装开始前没有任何已有结构、也没有备份时，离线入口可重入其明确标记的首次安装；运行服务仍拒绝不完整状态。

恢复前建立严格的一一对应：控制目录、完整哈希派生库名、机构 JSON 快照、机构便携包及 `source_tenant`。拒绝重复条目、缺失原件包、未绑定成员与两份机构快照内容不一致。所有列、行数、逐表及逐文件摘要在任何 DROP/原件删除前验证。机构上传目录的完整路径及祖先在破坏性操作前检查，删除前再次检查。

恢复绝不执行备份提供的 SQL。`migrations/restore/v1.json` 是随程序发布的可信恢复结构，来源为真实 `1359118` 的 SHOW CREATE，补充当前迁移登记表；它不是另一套初始化代码。`backend/infra/recovery_schema.py` 校验完整表/列定义和两条固定审计触发器。仅容许 MySQL 显示中的同义字符集写法与数值自增计数差异，不规整引号内的文字。恢复只执行安装包里的受信任定义；自增值经整数解析后使用固定 ALTER 模板。恢复后再次核对结构、触发器与逐表哈希，全部成功才解除阻断。历史夹具装载器复用同一验证器及可信 SQL，不保留另一个未经验证的 SQL 执行入口。

机构备份清单覆盖原 34 张表加 `demo_installations`；`schema_migrations` 归类为部署状态，不进入机构导出。机构恢复白名单覆盖全部导出表，空工作区检查容许迁移表。Windows 包、源码包、Docker 与安全回归沙箱均包含 `migrations/`；沙箱在启动服务前运行统一迁移入口。

## 历史夹具与证据

`tests/fixtures/v070/generate.py` 在未修改的真实 `1359118dd672431dcd0cde2eb81f2b0f994eef37` 检出运行，拒绝其他提交或脏的历史源码。控制库及 hq/dept-01/sub-01 原结构、原数据、逐表行数/摘要、0.7.0 实际导出的机构 ZIP、配套原件及公开合成账户均已保存。dept-02 保持未开通。三角色账户、总部超管、强制改密账户和详情编号见 `manifest.json`；`browser`/`objects` 已供根代理运行真实旧界面逐路由基线。

夹具生成仅调用原版本代码和业务函数，没有直接编造业务行。包含演示安装回执、普通资产及独立审核发布历史、原文/锚点、台账、专题/任务、权责规范、知识专栏、技能与数据源映射。`load.py` 只装载原快照，不运行当前迁移；必要的目录库名按目标隔离命名空间重映射，其他编号和历史保留。

## 验证命令与口径

新增测试统一由 `unittest discover -s tests` 收集；真实 MySQL 项沿用 `RUN_JOBS_MYSQL_TESTS=1`，没有新增环境开关。当前共 64 项新增用例：35 项轻量契约/夹具/检查、20 项正常 MySQL 迁移/恢复、9 项恶意备份 MySQL 边界。后两组最终验收必须实际执行，不能算作未执行跳过。

```sh
RUN_JOBS_MYSQL_TESTS=1 python -m unittest discover -s tests/migrations -v
python -m unittest discover -s tests/checks -v
python scripts/checks/check_migrations.py
python scripts/drills/upgrade_from_v070.py --v070-source <1359118检出> --output work/upgrade-drill.json
ruff check backend scripts ops tests migrations
ruff format --check backend scripts ops tests migrations
```

包级验证包括全部既有行哈希、完整列/索引/外键结构比较、并发只执行一次、校验和拒绝、失败停点、备份失败零迁移、重入、新机构、原 0.7.0 演示机构备份恢复、完整恢复、持久失败及进程中止。9 项恶意备份测试重新计算有效行/文件摘要后测试 trigger/DDL 注入、删除审计触发器、删除机构原件包、同前缀目录重定向、重复目录、错误便携包机构、uploads 父级符号链接和夹具装载器。每项都实际调用恢复入口，确认拒绝且当前数据/原件以及目录外哨兵库不变。

既有初始化/启动器/升级器与三个 MySQL 包验证 39 项通过（仅原有 Windows DACL 原生项在 Linux 跳过）；原恢复边界脚本 7 项通过；真实安全脚本 12 项通过。初次旧版本恢复演练完成“原夹具→迁移→真实 DDL 后注入失败→拒启动→原备份恢复与逐表哈希一致→真实 0.7.0 API 健康检查”。加固后的最终复验为迁移目录 60/60、检查目录 4/4，全数实际执行、零跳过；真实 1359118 进程恢复演练再次通过。完整日志与摘要位于 `docs/blueprint/rounds/r1-evidence/r1-2/`。R1-10 另以 14 项纯校验/内存桩和 4 项单测独立复核，最初发现的 4 项 P1 与保留策略 P2 均已关闭。

## 自行裁决与交接

1. 首次安装中止且不存在可恢复备份时，允许唯一离线入口重试该明确标记的首次安装；已有数据升级失败不自动续跑，必须整体恢复。
2. 升级恢复使用版本化可信结构清单，任何未知结构一律拒绝。R1-7 及后续轮次增表/改列时，须按备份迁移版本选择对应可信恢复结构，保留旧版本定义，恢复后再执行机构迁移；不可仅扩充当前列集合后接收所有历史包。
3. 新装与升级结构比较使用 information_schema 的列、索引与外键完整语义；不以 MySQL 同义的 SHOW CREATE 字符串差异判失败。
4. 新增 `backup` 阶段的进度采用固定 10000 工作刻度；空跑没有备份，备份各表完成均推进。Windows 600 秒总限/180 秒停滞限保持原约束。
5. 包内没有修改 requirements.txt、受保护工作流、security/store.py、旧测试、基线用例清单或旧路由策略。未推送、未建 PR、未打标签。根代理统一处理 AGENTS、STATUS、R1-PLAN 与整轮报告；须提醒后续 R5 的 `backend/agent/runtime/` 仍受打包排除目录规则影响。

风险边界：维护锁是单机共享文件系统锁，所有实例必须指向同一存储根；既有部署失败恢复需要停止全部共享实例并使用有建库权限的维护账号。Linux 结果不代替 Windows 原生验收。更大版本的结构升级需要新增对应可信恢复结构和兼容测试，不能绕过校验。

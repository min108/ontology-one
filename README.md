# Ontology One · 集团本体平台

`0.4.0` 本体 MCP 服务工程预览版。React / TypeScript 前端、FastAPI 服务、MySQL 8 持久化，提供素材整理、数据/规则/算法/知识建模、审核发布、图谱、数据导入、项目任务、版本还原与机构备份。

本版继承真实账户、机构隔离及外部源映射，新增独立应用身份、标准 MCP 服务端和受控跨源关联。仓库只保留运行、部署和验证必需的代码与说明；不含原始资料包、业务数据、上传原件、模型密钥或运行凭据。首次部署创建指定的51个机构目录与空工作区，业务成果由用户建立。

## 账户与数据边界

账户归属一个机构；机构对应一个租户及独立业务数据库。组织目录包括总部实体、派驻机构、12个总部职能部门和37家子公司。总部实体不在图谱显示根节点，其自身成果仍可显示；其他机构成果归于自己的机构根节点。

| 身份 | 本机构操作 | 跨机构能力 |
| --- | --- | --- |
| 管理员 | 账户、角色、建模、审核、版本还原和运维 | 总部实体管理员自动为超管，可切换工作区运维；操作留审计 |
| 本体建模师 | 建模、导入、编辑、提交审核 | 总部账号经显式授权汇总查询和提出跨机构关联 |
| 审核官 | 查询、准入审核及闭环 | 总部账号经显式授权查看、审核跨机构关联 |

子公司、派驻机构默认严格隔离。总部汇总账号通过持久化集团授权查看全局，包括标为“仅本机构”的成果。总部部门可以把已发布成果设为“总部公开”，供总部及其他总部部门查看；未公开成果对其他部门不可见，子公司不继承这一公开范围。普通总部账号的跨机构权限限于联邦查询和关联；完整跨机构运维限于总部超管。

登录采用不可预测的服务端会话，数据库只存令牌摘要；首次登录改密、口令限速、CSRF、来源白名单、会话列表/撤销、账户停用和角色变更均已接入。每次请求重新读取有效身份，权限撤销从下一次请求生效。正在执行的业务写入及后台任务在提交前再次校验身份；本版不声称跨数据库事务具有严格线性化撤权语义。

## 容器部署

推荐 Linux 或 Windows/macOS Docker Desktop 的 Linux 容器模式，需要 Docker Compose v2。默认仅监听本机 `127.0.0.1:8765`，MySQL不向宿主机开放端口。

```bash
cp .env.example .env
# 在 .env 中填写两个不同的随机数据库密码（各至少20位）
docker compose up -d --build
docker compose exec api python scripts/bootstrap_admin.py --username group.admin
```

初始化命令交互输入初始登录密码，不在命令行或日志中保存明文。打开 `http://127.0.0.1:8765`，首次登录必须改密。没有内置固定账号或默认密码。`.env` 不得提交。修改端口时同时调整 `ONTOLOGY_TRUSTED_ORIGINS`；跨机器提供服务须配置可信 HTTPS 反向代理、精确来源白名单及 `ONTOLOGY_COOKIE_SECURE=1`。

镜像多阶段编译前端，应用以非root用户运行。Compose分开运行数据库迁移、4个API进程及持久任务worker。只有迁移容器取得数据库schema owner凭据，用于安装审计禁止修改/删除的触发器；API和worker只获得本部署 `ontology_one_v020_*` 命名空间权限，不获得全局SUPER。默认每API进程的身份库连接上限16、全部机构业务连接总上限16（含空闲连接）；Compose项目名和数据库前缀保留v020以兼容原有卷，应用版本为0.4.0。MySQL设置256个连接，为4个API进程、worker、最多16个MCP临时执行进程及运维留出预算。MCP临时进程独立创建连接，扩容API或MCP并发时须同步核对数据库总连接预算。数据库管理员仍拥有恢复和变更数据库约束的能力。

升级已有部署前备份，停止API/worker，再运行迁移并启动：

```bash
docker compose stop api worker
docker compose up --build --force-recreate migrate
docker compose up -d --no-deps api worker
```

本版支持单机或共用同一存储卷的部署。维护锁要求所有API、worker和恢复命令使用同一卷；不包含跨主机分布式锁、自动故障转移或经验证的高可用方案。

## 本地开发

Python 3.11+、Node.js 22.12+、已配置的MySQL 8，以及支持`flock`的Linux/macOS。Windows原生启动请使用Docker。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
```

设置 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`（部署前缀）、`MYSQL_USER`、`MYSQL_PASSWORD`。也支持优先级更高的 `ONTOLOGY_MYSQL_URL`。先由迁移账号执行 `.venv/bin/python scripts/migrate.py --provision-all`，然后改用限制在该前缀内的运行账号。迁移账号需有安装触发器的权限；不得为此给运行账号授予SUPER。

```bash
.venv/bin/python scripts/bootstrap_admin.py --username group.admin
bash start.sh
# 开发热更新界面：另开终端运行 npm --prefix frontend run dev
```

本地默认允许内置任务worker；独立部署可设置 `ONTOLOGY_WORKER_ENABLED=0`，另运行 `.venv/bin/python scripts/run_worker.py`。`ONTOLOGY_STORAGE_ROOT` 默认 `data/tenants`，必须与所有进程一致。

## 外部数据源：发现、映射、发布、源端查询

「数据导入」保留Excel/CSV台账上传，并提供「数据源管理」入口。外部表接入仅保存连接引用、表/视图/已有查询定义的元数据、结构快照、字段映射、血缘与审核版本；不生成台账数据副本，也不把查询预览当作已入库的本体实例。

1. 部署管理员配置源系统的只读账号或限定只读范围的独立MCP令牌，在受控凭据文件中绑定机构、协议和精确目标。机构管理员在界面登记连接、凭据引用及允许访问的对象名称。
2. 管理员给建模师、审核官分别授予「结构发现」「源端查询」权限。平台角色与源授权共同生效；本机构管理员具有源管理能力，总部超管须明确切换到对应机构运维。
3. 发现允许对象的列、类型、可空性和主键，形成结构版本；不抽取样本行。建模师选择资源，将字段关联到本机构已发布的标准数据资产，逐项确认业务含义、记录粒度、单位、对象标识与关联键。
4. 提交后由另一名有源发现授权的审核人员说明理由并发布。每次编辑、提交、审核和修订保留版本；界面提供来源结构和映射历史。
5. 按已发布映射选择字段、条件、排序及数量，平台生成受控只读查询。每次查询重新核验结构和标准版本；源行更新在下次查询体现，结构变化或标准版本变化会阻止旧映射查询，须重新发现、修订与审核。

本阶段映射执行字段投影与重命名，不自动换算单位、聚合或改变粒度；需要这些变换时，应由源系统提供有明确语义和版本的视图或固定查询，再经人工审核映射。

### 凭据配置

复制 `ops/source-credentials.example.json` 到仓库、上传目录之外的受控路径，由部署管理员填写。文件格式为 `{"refs":{"引用名":{...}}}`，条目必须包含真实 `tenant_id`、`kind`，并与连接登记的MySQL主机/端口/库名或MCP完整端点精确匹配。平台不会把浏览器会话、集团令牌透传给源系统。

本机进程设置 `ONTOLOGY_SOURCE_CREDENTIALS_FILE=/absolute/private/source-credentials.json`。文件只允许当前服务身份读取，权限0600；更新时用同目录临时文件原子替换，源调用会重新读取，不缓存凭据。MySQL生产账号仅授予所需表/视图的SELECT权限（使用视图还需SHOW VIEW以验证定义版本），不给写入、DDL或管理员权限。默认使用TLS校验；只有部署端条目显式配置 `allow_insecure:true` 才允许隔离开发环境的明文连接。需要专用CA时在条目中配置 `tls_ca` 并把证书只读挂载给服务。

Docker可使用可选覆盖配置，`.env`中设置 `ONTOLOGY_SOURCE_CREDENTIALS_PATH` 为宿主机绝对路径。Linux宿主机文件须由容器服务UID 10001读取且权限0600；Docker Desktop也须确认容器内可读和权限检查通过。此文件不放入镜像、工作区备份或交付包。

```bash
docker compose -f docker-compose.yml -f ops/compose.sources.yml up -d --build
```

### MCP客户端接入契约

固定使用官方Python SDK `mcp==2.3.0` 的Streamable HTTP传输。源系统需提供 `ontology-source/1` 适配工具：

| 工具 | 输入 | 结构化输出 |
| --- | --- | --- |
| `ontology_source_discover` | `contract_version:"1.0"` | `contract_version`、`resources`；对象含key、name、kind、columns、primary_key |
| `ontology_source_query` | `contract_version:"1.0"`、resource、selection | `contract_version`、columns、rows、truncated、elapsed_ms |

`kind`支持table、view、saved_query；已有查询必须由源系统预注册固定定义，提供只读`definition`说明与`definition_version`。selection仅包含columns、filters、order_by、limit。定义文本仅供业务核对，不作为SQL执行。平台校验工具schema与结构化结果，只调用这两个约定工具；不把任意MCP输出解释成数据库，也不接受上层传入SQL。源端MCP工具同样须使用只读账号和授权范围，工具名称或readOnlyHint不能替代源系统的权限约束。兼容性问题会明确报错。可运行的标准MCP对接示例位于 `tests/fixtures/source_mcp_server.py`，仅供隔离测试，不作为企业源系统的生产网关。

### 留存与限额

源端查询结果仅存在于本次响应和当前页面内存，离页或切换机构清除；服务端不缓存结果，不持久化本次查询的过滤值、执行SQL或返回行；源端视图和已有查询的定义属于治理元数据，按结构版本保存。响应使用`Cache-Control: no-store`。查询审计只记录操作人、租户、来源/映射/结构版本、结果数量、耗时及安全错误码；元数据和审计按平台治理要求持久保留，本版不自动清理。部署侧代理、APM和源系统日志须同样关闭请求/响应正文采集；平台不能替外部系统决定其留存期限。

单次查询最多200行、12个条件、IN列表50项、结果2MiB；MySQL只执行由白名单字段生成并绑定参数的SELECT，采用只读事务、语句/连接超时。MCP调用有总时限，不跟随重定向。授权、启停、配置和映射版本在远端调用返回后再核验，不能把执行期间已经撤销的结果交给调用者。结果持久化导出和智能体执行尚未开放。

## 面向上层应用的 MCP 服务

管理员在「系统设置 → 集成应用」创建调用方，选择本机构的有效委托账号、工具和已发布映射。令牌有效期1～90天，默认30天；签发与轮换时仅展示一次，数据库只保存摘要。页面关闭、机构切换后不保留明文。可停用应用、撤销凭据或轮换；轮换立即撤销旧凭据。改变账号状态、角色、密码或源授权也会影响委托访问。账号授权版本变化后须重新签发凭据。

有效权限取应用白名单、委托账号权限、源授权和映射状态的交集。应用固定在创建时机构，不继承总部超管或联邦跨机构运维能力；跨源关联首版限同一机构。上层令牌只在平台认证，源端使用部署侧独立只读凭据，不接受调用方覆盖机构、操作人、源连接或SQL。网页版会话不能用于MCP，应用凭据也不能登录管理界面。

端点为 `http://127.0.0.1:8765/mcp/`，协议为官方SDK 2.3.0的无状态Streamable HTTP，使用 `Authorization: Bearer <应用令牌>`。每次请求重新认证；会话初始化不固定权限。工具契约版本为`1.0`，通过`tools/list`获取完整输入/输出schema。所有工具只读，未知参数和类型错误明确拒绝。

| 工具 | 能力与边界 |
| --- | --- |
| `ontology_discover` | 当前机构已发布本体要素与显式授权映射；最多100条，不返回原始payload |
| `ontology_query` | 按`mapping_id`及语义字段、条件、排序源端查询；最多200行 |
| `ontology_traverse` | 沿已确认关系穿透已发布可见节点；最多3层、100节点、200条边，不借隐藏节点穿透 |
| `ontology_explain` | 按实体或映射解释口径、粒度、单位、标识键、归属和版本；失效映射仅返回最小状态诊断，不泄露未发布修订内容 |
| `ontology_join` | 两个不同数据源按已审核关联键分步查询；最多50个左侧行/键、200个右侧行、200条关联结果 |

关联键须绑定同一已发布标准及版本、相同单位和已审核的对象标识/关联声明；首版只接受完整的单字段对象键，不接受复合标识的一部分，也不自动把字符串键转换为数字。中间行只在单次执行内存中使用。返回结果明确标注截断；右侧被截断时，未命中不代表源端不存在。默认任何一步失败都不返回业务行。只有显式`allow_partial:true`且右侧发生已识别的超时或服务不可用，才可返回`status:partial`及`unjoined_left`，关联结果`rows`为空并标记失败步骤；认证、授权、结构漂移和映射失效始终整体拒绝。

统一响应包含`contract_version`、`request_id`、`status`、`data`、`error`、`warnings`、`provenance`和`retention`。失败状态不含业务数据；安全错误码包括`INVALID_ARGUMENT`、`FORBIDDEN`、`UNAUTHENTICATED`、`STALE_MAPPING`、`TIMEOUT`、`BUSY`、`RESULT_LIMIT`及`SOURCE_UNAVAILABLE`。HTTP认证错误使用401/403，工具执行错误使用结构化响应。调用方应检查`status`与`warnings`，不能把部分或截断结果当作完整结果。

每个工具调用使用固定入口临时进程，限制768MiB地址空间、15秒CPU时间、文件大小和文件句柄；调用方不能指定程序。执行时限由`timeout_ms`指定，范围1～30秒，包括启动和最终授权复验；超时/断连后终止并回收执行进程，清理和失败审计另有最多3秒预算。请求不排队，每API进程默认4个同时执行，满额返回`BUSY`；`ONTOLOGY_MCP_MAX_CONCURRENT`允许1～16，须结合机器内存与连接容量调整。子进程最终复验所有涉及的应用授权、源授权、映射及结构版本后才交付结果；这不等于跨系统严格线性化事务。远端查询仍受源端自身超时及断连处理约束。

MCP结果不进入数据库、磁盘文件、任务队列或结果缓存，响应最多2MiB且`Cache-Control:no-store`。平台关闭该传输的默认OpenTelemetry工具正文采集，SDK传输日志不记录参数或结果；安全审计仅保留调用方、委托账号、机构、工具、版本、数量、时长、错误码等元数据。应用元数据和凭据摘要属于控制库，不随机构业务备份导出。调用方及内网代理应按自身治理要求设置日志策略。

内网部署无需外部身份服务、云托管或模型API。将实际内网域名加入`ONTOLOGY_MCP_ALLOWED_HOSTS`（精确主机及端口），浏览器来源加入`ONTOLOGY_TRUSTED_ORIGINS`，配置HTTPS反向代理并关闭该路由缓存和正文日志；代理应支持SSE、不缓存响应，超时留出40秒以上。默认只接受本机Host，不能用任意Host覆盖名单。依赖和镜像须预先准备；不声称交付包包含离线依赖仓库。

提供标准SDK调用方示例，默认只输出状态、数量与版本，不打印查询值。运行后交互输入应用令牌，或由受控进程环境注入`ONTOLOGY_MCP_TOKEN`；不把令牌放进命令行或示例文件。

```bash
.venv/bin/python scripts/mcp_client_example.py
# 查询参数经标准输入传入；mapping-id和field-name替换为已发布映射中的值
printf '%s' '{"mapping_id":"mapping-id","query":{"fields":["field-name"],"limit":10}}' \
  | .venv/bin/python scripts/mcp_client_example.py --tool ontology_query --stdin
```

## 工程结构与接入边界

- **身份控制库**：组织、账户、角色、授权、会话、安全审计和持久任务队列。服务端根据会话和目录派生数据库名，不采信客户端租户/数据库覆盖。
- **机构业务库**：实体、关系、版本、原文追溯、台账和业务日志。实体内容采用原生JSON及列表排序索引；旧文本字段升级前验证JSON有效性，非法记录会阻止迁移。接口读取使用只读事务；写入、任务与文件操作复用同一身份上下文。
- **联邦层**：受权跨库查询、发布可见性过滤、带归属的复合图谱ID和跨机构关系审核；返回前复核授权。总部原件运维通过明确切换工作区进行。
- **文件与缓存**：租户专属路径，拒绝路径穿越、绝对路径和符号链接；缓存键包含租户、数据库和权限身份，业务提交触发失效。
- **任务层**：固定操作白名单、持久排队、身份及租户快照、幂等、租约/心跳、失败重试和撤权取消；切换界面不会把原任务迁移到新租户。
- **集成扩展**：`/api/auth/oidc/config` 明确返回未接入状态；`/api/integrations/capabilities`报告能力，旧`/api/integrations/tools/call`继续使用登录身份，`/mcp/`使用独立应用身份。两种入口共用账户、机构与源授权服务。集团SSO协议适配尚未接通，不能使用未验证请求头代替登录。

API返回请求编号，安全审计保留真实操作人、目标机构、结果和请求编号，不记录密码/令牌。在线AI按机构单独配置；未配置时仍可使用人工建模和本地整理功能。

## 验证与性能验收

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/check_restore_v020.py
# 下列命令只应连接独立测试MySQL，账号须可创建/删除测试库及安装触发器
.venv/bin/python scripts/check_security.py
.venv/bin/python scripts/check_workflows.py
.venv/bin/python scripts/check_sources.py
.venv/bin/python scripts/check_mcp.py
.venv/bin/python scripts/check_entity_storage.py
RUN_FEDERATION_MYSQL_TESTS=1 RUN_JOBS_MYSQL_TESTS=1 \
  .venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/load_test.py --workers 4
```

脚本为每次运行生成随机数据库命名空间并清理，报告写入被忽略的 `work/`。安全回归涵盖越权、伪造机构/操作人、原件路径、跨租户同ID、缓存/任务、公开范围、CSRF、会话和角色撤销。业务回归另覆盖标准发布、CSV映射与导出、本地访谈草稿、权责流程和任务审核闭环。MCP验收使用真实MySQL源、标准MCP源和官方SDK调用方，验证五个工具、两源关联、漂移、撤权、部分失败及不留存业务行；源数据为隔离测试数据，不能代替企业系统联调。前端构建执行TypeScript检查。

约定性能基线为50个真实并发会话、20个租户混合读写：普通查询P95≤500ms、写入P95≤1s、总部汇总P95≤2s。默认压测每机构1000个资产和500条关系，10秒预热、90秒测量；输出错误率、各类延迟、吞吐量和API进程树CPU/内存。达标结论须结合当次报告中的硬件、进程数和数据量，不以开发环境结果代替集团生产容量规划。

## 备份与恢复

系统设置导出当前机构业务快照及原件，不包含其他机构、账户会话、模型密钥或外部源凭据。外部源结构、映射及其版本属于元数据，会随当前机构备份；源端查询结果不进入备份。恢复时数据源自动停用并清除源授权，管理员须重新核对凭据、启用和授权。实体历史中管理员可说明原因后还原为草稿，须重新审核。

离线灾后恢复必须停止所有共享此存储卷的API和worker，仅允许恢复到同机构的空工作区（允许目录根节点）。控制库、非空工作区、跨机构备份和无机构标记的旧备份均被拒绝。备份请另存到受控存储，定期验证恢复。

```bash
docker compose stop api worker
# 将backup.zip挂载到临时容器，只读访问；交互输入真实管理员登录密码
docker compose run --rm --no-deps -v /absolute/path/backup.zip:/tmp/backup.zip:ro api \
  python scripts/restore_backup.py /tmp/backup.zip --tenant-id sub-01 \
  --maintenance --username group.admin --reason '灾后恢复'
docker compose up -d --no-deps api worker
```

恢复命令用目录派生目标库，检查完整表/列白名单及文件路径，记录审计。总部超管可恢复任一机构，本机构管理员只能恢复自身。所有服务必须共用维护锁存储；恢复过程中不应在另一卷启动旁路实例。

源码或离线分发包不包含工作区：先构建前端，再运行 `.venv/bin/python scripts/package_release.py --output work/ontology-one-0.4.0.zip`。

## 后续交付顺序

0.4.0完成第三批MCP服务基础，后续阶段沿用同一租户、源授权、映射查询和审计边界，不另建绕过治理的智能体通道。

- **0.5：技能资产与技能开发。** 迁移算法资产标识及已有关系，落地Agent Skills包编辑、校验、隔离测试、审核、版本与导入导出；脚本和工具依赖须经过平台授权，包内说明不能授予权限。
- **0.6：Harness受控运行。** 集成固定版本官方DeepSeek Harness，把AI辅助及本体应用迁移到统一运行通道。平台负责身份和资产治理，独立运行单元负责会话隔离、凭据注入、资源限制、取消与故障回收。

Harness官方目前标注为开发预览。此前核实GitHub `dsh-v0.2.2-rc.1` 与PyPI SDK `0.2.1rc2` 并非同一版本；后续集成须重新核对一致源码/SDK并验证。其默认会话日志上传和运行权限不符合本项目默认策略，须显式关闭、隔离并测试；请求超时也不能被视为实际取消。0.4.0尚未启用Harness，现有AI辅助保留原行为，不将外部查询结果自动发送给模型。

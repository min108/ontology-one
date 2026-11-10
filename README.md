# Ontology One · 集团本体平台

`0.5.0` 技能开发与 Harness 创作工程预览版。React / TypeScript 前端、FastAPI 服务、MySQL 8 持久化，提供素材整理、数据/规则/技能/知识建模、审核发布、图谱、数据导入、项目任务、版本还原与机构备份。

本版继承真实账户、机构隔离、外部源映射和 MCP 服务，新增 Agent Skills 技能包治理、隔离脚本测试及官方 DeepSeek Harness 辅助创作。仓库只保留运行、部署和验证必需的代码与说明；不含原始资料包、业务数据、上传原件、模型密钥或运行凭据。首次部署创建指定的51个机构目录与空工作区，业务成果由用户建立。

## 账户与数据边界

账户归属一个机构；机构对应一个租户及独立业务数据库。组织目录包括总部实体、派驻机构、12个总部职能部门和37家子公司。总部实体不在图谱显示根节点，其自身成果仍可显示；其他机构成果归于自己的机构根节点。

| 身份 | 本机构操作 | 跨机构能力 |
| --- | --- | --- |
| 管理员 | 账户、角色、建模、审核、版本还原和运维 | 总部实体管理员自动为超管，可切换工作区运维；操作留审计 |
| 本体建模师 | 建模、导入、编辑、提交审核 | 总部账号经显式授权汇总查询和提出跨机构关联 |
| 审核官 | 查询、准入审核及闭环 | 总部账号经显式授权查看、审核跨机构关联 |

子公司、派驻机构默认严格隔离。总部汇总账号通过持久化集团授权查看全局，包括标为“仅本机构”的成果。总部部门可以把已发布成果设为“总部公开”，供总部及其他总部部门查看；未公开成果对其他部门不可见，子公司不继承这一公开范围。普通总部账号的跨机构权限限于联邦查询和关联；完整跨机构运维限于总部超管。

登录采用不可预测的服务端会话，数据库只存令牌摘要；首次登录改密、口令限速、CSRF、来源白名单、会话列表/撤销、账户停用和角色变更均已接入。每次请求重新读取有效身份，权限撤销从下一次请求生效。正在执行的业务写入及后台任务在提交前再次校验身份；本版不声称跨数据库事务具有严格线性化撤权语义。

## Windows 10/11 64 位便携运行

Windows 发行资产为 `ontology-one-0.5.0-windows-x64.zip`，与 GitHub 自动生成的源码 ZIP 不同。完整发行包内置固定版本的 Python、MySQL、官方 Harness、应用依赖和编译后的前端；用户电脑无需预装 Python、Node.js、MySQL、Docker 或 WSL，启动时不下载安装依赖。

1. 将完整运行包解压到当前用户可写的本地 NTFS 目录。支持中文、空格和 `!` 路径；不要在 ZIP 预览中直接运行，也不要放到网络共享或受保护的系统目录。
2. 双击 `启动平台.bat`。首次运行初始化数据库和51个空机构工作区，交互设置总部超管 `group.admin` 的初始密码，至少12位并包含字母和数字。没有内置通用密码；首次登录按页面要求改密。
3. 就绪后自动打开本机浏览器，默认地址 `http://127.0.0.1:8765`。这里的127地址属于运行此包的用户电脑。关闭启动窗口不会停止平台；双击 `关闭平台.bat` 才会停止本安装实例。

本地 MySQL 默认只监听 `127.0.0.1:18765`，数据库初次设密阶段关闭TCP网络。数据库随机凭据、控制令牌与模型配置只授予当前Windows用户和SYSTEM访问。正常关闭及重复启停保留 `data/`；已有数据或凭据异常时不会自动删除、重置数据库。端口被其他程序占用时明确报错，不按进程名或旧PID强杀。首次失败会回收本次启动的进程；查阅 `data/local/last-result.json` 中的固定错误码以及本实例数据库日志进行诊断，不在聊天或Git中提交私有配置。

便携模式用于本机体验及单机部署，默认不开放局域网访问。Windows安全策略若禁止PowerShell、AppContainer或包内程序执行，启动或隔离测试会明确失败，不会绕过系统策略。BAT使用ASCII/CRLF，中文PowerShell入口使用UTF-8 BOM；直接从源码检出不能代替带runtime的发行包。升级前关闭平台并备份，保留原目录及其 `data/`，避免把新包覆盖到仍运行的实例。

## Linux 容器部署

Linux服务部署使用Docker Compose v2。默认仅监听本机 `127.0.0.1:8765`，MySQL不向宿主机开放端口。Windows便携包不依赖此流程。

```bash
cp .env.example .env
# 在 .env 中填写两个不同的随机数据库密码（各至少20位）
docker compose up -d --build
docker compose exec api python scripts/bootstrap_admin.py --username group.admin
```

初始化命令交互输入初始登录密码，不在命令行或日志中保存明文。打开 `http://127.0.0.1:8765`，首次登录必须改密。没有内置固定账号或默认密码。`.env` 不得提交。修改端口时同时调整 `ONTOLOGY_TRUSTED_ORIGINS`；跨机器提供服务须配置可信 HTTPS 反向代理、精确来源白名单及 `ONTOLOGY_COOKIE_SECURE=1`。

镜像多阶段编译前端，应用以非root用户运行。Compose分开运行数据库迁移、4个API进程及持久任务worker。只有迁移容器取得数据库schema owner凭据，用于安装审计禁止修改/删除的触发器；API和worker只获得本部署 `ontology_one_v020_*` 命名空间权限，不获得全局SUPER。默认每API进程的身份库连接上限16、全部机构业务连接总上限16（含空闲连接）；Compose项目名和数据库前缀保留v020以兼容原有卷，应用版本为0.5.0。MySQL设置256个连接，为4个API进程、worker、最多16个MCP临时执行进程及运维留出预算。MCP临时进程独立创建连接，扩容API或MCP并发时须同步核对数据库总连接预算。数据库管理员仍拥有恢复和变更数据库约束的能力。

升级已有部署前备份，停止API/worker，再运行迁移并启动：

```bash
docker compose stop api worker
docker compose up --build --force-recreate migrate
docker compose up -d --no-deps api worker
```

本版支持单机或共用同一存储卷的部署。维护锁要求所有API、worker和恢复命令使用同一卷；不包含跨主机分布式锁、自动故障转移或经验证的高可用方案。

## 本地开发

以下是Linux源码开发流程：Python 3.11+、Node.js 22.12+和已配置的MySQL 8。Windows普通用户使用上面的完整便携包；发行构建在原生Windows CI中执行，不要求用户安装构建工具。

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

本机进程设置 `ONTOLOGY_SOURCE_CREDENTIALS_FILE` 为项目目录之外的绝对路径。Linux文件只允许当前服务身份读取，权限0600；Windows使用当前用户及SYSTEM的受保护DACL，拒绝共享目录、重解析点和额外读取身份。便携启动器会传递这个路径配置，源凭据不会进入Harness进程。更新时用同目录临时文件原子替换，源调用会重新读取，不缓存凭据。MySQL生产账号仅授予所需表/视图的SELECT权限（使用视图还需SHOW VIEW以验证定义版本），不给写入、DDL或管理员权限。默认使用TLS校验；只有部署端条目显式配置 `allow_insecure:true` 才允许隔离开发环境的明文连接。需要专用CA时在条目中配置 `tls_ca` 并把证书只读提供给服务。

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

## 技能资产与技能开发

「技能资产」是本体节点，规范类型标识为`skill`。「技能开发」维护可发布的 Agent Skills 包：`SKILL.md`说明、适用条件、输入输出契约、业务步骤、脚本、参考资料、工具依赖和合成测试用例。建模师或管理员编辑，另一名审核官或管理员审核发布；最后编辑者及提交者不能自审。编辑、测试与审核均受当前机构、角色和账户状态约束。

工作流程为：创建或导入草稿 → 编辑/采纳 Harness 提案 → 保存 → 校验 → 测试 → 提交 → 独立审核发布。发布绑定包摘要、资产依赖版本及工具映射/来源版本；更改内容或依赖后必须重新测试。来源撤权、映射失效、关联标准变化或技能撤回后，加载入口拒绝旧授权或旧证明。通用资产编辑、台账覆盖、历史还原和关联定位不能绕过技能包的治理流程。

技能可关联本机构已发布的数据、规则和知识，依赖经发布后作为已确认关系进入本体图谱。工具依赖采用平台固定 MCP 工具名和已发布映射白名单；声明工具名称或包内`allowed-tools`不产生任何授权。当前测试核验工具依赖的元数据与权限，不连接外部业务源运行测试，也不把业务明细制作成测试数据。

### 开放格式与版本

兼容 [Agent Skills 开放格式](https://agentskills.io/specification)：必需`SKILL.md`及`name`、`description`头部，支持脚本、参考资料及资源目录。平台扩展通过`metadata.ontology-one.manifest`指向`assets/ontology-one.json`，包含可移植的输入输出、步骤和合成用例；本机构账号、数据源授权、审核状态及测试证明不写入可移植清单。导入其他平台的包只生成草稿，需补齐治理信息并在本机构重新选择依赖、测试和审核。

本版支持 UTF-8 文本包，最多64个文件、单文件256KiB、总量及上传ZIP均2MiB，路径最多240字符、8层。ZIP在内存校验，不直接解压；拒绝路径穿越、链接、重复路径、异常压缩、歧义YAML/JSON和已知凭据格式。此检测不是完整的数据防泄露系统；包内仅应保存技能代码、说明、参考材料及合成示例。输入输出采用有界 JSON Schema 子集，支持类型、属性、枚举和长度/数值范围，不接受远程引用、正则或递归解析。

技能版本和状态变化留历史，界面可查看历史内容及差异、导出指定版本。旧“算法”说明升级为技能节点时保留原ID、关联、原文、历史快照及审计；不会自动变成可执行技能包。新模板使用S编号，旧A编号及旧表头仍可导入。历史快照只在读取时规范化显示，原记录不重写。MCP发现接口输出`skill`，接受旧`algorithm`查询参数作为兼容别名。

### 脚本隔离测试

说明型技能的测试只核验声明契约与合成示例，界面明确标记“契约测试”。带脚本的技能必须通过真实隔离执行，缺少运行器时显示不可用，不能用静态校验代替脚本测试。

首版执行Python标准库脚本：选择`scripts/`中的`.py`入口，从标准输入读取一个JSON值，向标准输出写一个JSON值。期望输出保留在隔离进程外比较，不交给待测脚本。其他语言文件可以编辑及导出，尚不提供执行环境或自动安装依赖。

Windows便携包通过原生低权限AppContainer（LPAC）与Job Object执行测试。每个用例使用独立身份，不授予网络能力；Python标准库副本、技能文件及AppContainer目录只读，结果仅通过JSON管道返回。脚本不支持写工作文件。每个用例最多192MiB合计内存、1个进程、5秒CPU时间及5秒执行时限，限制输出并回收整个Job；隔离不可用时拒绝执行，不退回普通宿主进程。运行时可用性通过实际隔离探针确认。

完整容器部署启用可选运行器配置：

```bash
docker compose -f docker-compose.yml -f ops/compose.skills.yml up -d --build
# 同时接外部数据源时，加上 -f ops/compose.sources.yml
```

此配置构建固定的测试镜像与独立运行器。只有受信的运行器服务接入Docker，API只挂载受控Unix socket。每个用例使用新建的无网络容器，非root运行、只读根文件系统、删除Linux能力、禁止提权，限制进程数、CPU、内存、临时空间和输出；不挂载平台工作区、Docker socket或凭据。待测代码只接收本次包及合成输入，不能自行选择镜像、命令、挂载或环境。

可信启动程序在读取包和运行脚本前设置不可上调的POSIX硬限制：每进程地址空间48MiB、每UID最多4个进程、CPU时间5秒、64个文件描述符、单文件16MiB及禁止core dump；临时文件系统64MiB。外层另配置Docker内存256MiB、PIDs 64和1核CPU配额，其生效依赖宿主cgroups。当前云测试环境的rootless Docker不支持这些cgroup配额，本轮实际验证的是POSIX内存/进程限制及超时清理。单运行器最多2个并发用例，已验证两路普通脚本同时执行；进程限制按真实UID共享，多运行器部署须规划独立宿主或UID隔离。

脚本执行时限5秒，整次请求预算30秒（包含容器准备），终止与资源回收另计。测试进程和容器在超时、取消、断连后清理；Docker异常时不得宣称测试通过。测试报告只保留用例名称、状态、错误码、数量和耗时，不记录原始输出。运行器本身属于受信运维组件，应限制其socket访问并使用独立部署命名空间。

宿主开发模式可先构建测试镜像并启动运行器，再给API设置socket路径：

```bash
docker build -f ops/skill-runner.Dockerfile -t ontology-skill-test:0.5.0 .
.venv/bin/python ops/skill_runner.py --socket /tmp/ontology-skill-runner.sock
# 在启动API的终端配置；socket只授权给API服务身份
export ONTOLOGY_SKILL_RUNNER_SOCKET=/tmp/ontology-skill-runner.sock
```

### Harness 辅助创作

本版使用用户指定的官方 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)，固定源码`dsh-v0.2.1-rc.2`与`deepseek-harness-sdk==0.2.1rc2`、同版`deepseek-harness-runtime-bin`。来源、提交及Linux/Windows x64二进制校验摘要见`ops/harness-runtime.lock.json`。运行前校验官方SDK及二进制摘要，不自动追踪上游最新版。

机构管理员在技能开发配置独立的Harness模型服务。官方适配器使用Messages协议（`/v1/messages`与`x-api-key`），默认地址`https://api.deepseek.com/anthropic`、模型`deepseek-v4-flash`。现有OpenAI格式的`/chat/completions`地址不能直接套用。密钥仅在当前机构的受控运行配置保存，界面不回显，备份和交付包不包含；更换服务地址必须重新提供对应密钥或明确清除旧密钥。模型服务未配置或连接失败时，手工开发仍可使用。

Harness通过真实官方运行时完成多轮创作，只获得固定的内存工具`read_skill_file`和`propose_skill_files`，可阅读当前包、提出说明/脚本/测试修改。模型看不到平台数据库凭据、源系统令牌或外部业务查询行；内置终端、主机文件、通用网络工具及动态插件加载均关闭。包内文字是待处理内容，不能扩展工具权限。修改建议保留在本次响应和当前页面内存中，用户明确采纳后才保存为草稿，审核与发布始终由平台执行。

Linux上的每个创作请求使用独立进程组及0700权限的tmpfs会话目录，运行结束销毁；禁用上游会话上传和插件清单上传，不持久保留SDK对话日志。宿主模式默认使用`/dev/shm`，必须是可写tmpfs，不能退化到普通磁盘。固定官方运行时须从私有临时目录加载原生模块，因此该tmpfs需要允许执行映射。Compose仅为API配置专用的256MiB `/run/ontology-harness` tmpfs（`exec,nosuid,nodev`），并设置`ONTOLOGY_HARNESS_TMP_ROOT`；`/tmp`和`/dev/shm`保留默认禁止执行配置。宿主开发时也可通过此变量指定符合条件的tmpfs。该配置仅供固定官方运行时使用，用户技能脚本仍在独立无网络、临时目录禁止执行的测试容器中运行。每API进程默认允许2个并发作者请求，最多8个模型步骤、单步输出8192 tokens，总执行预算90秒；输入上下文最多512KiB，较大的包仍可手工开发。超时、取消及断连会终止并回收本地进程；调用前和生成结束后复核账户、配置与技能依赖，期间撤权或配置变更的结果不返回。远端模型请求的最终计费或停止语义取决于模型服务。

本次工程验收使用真实官方SDK和运行时、受控的Messages模型夹具验证工具调用、凭据边界、取消及人工采纳；没有使用集团真实模型密钥，不能据此声称已完成真实模型质量验收。后续配置真实服务后，还需以业务技能样本评估生成质量。

Windows上的每个创作请求由独立Job Object管理，启动前设置1GiB合计内存、最多4个进程和25秒CPU时间上限；整体90秒执行时限及取消策略相同。固定官方配置关闭会话持久化组件，草稿通过一次性鉴权的本机内存通道交给固定插件；不将上下文、模型响应、事件日志或密钥写到临时目录。私有磁盘目录只存放固定配置、官方原生模块等非业务运行文件，结束后回收。该可信Harness进程与执行用户脚本的LPAC测试进程使用不同边界。

若部署需要出网代理，Harness仅继承部署进程中的HTTP/HTTPS/ALL/NO_PROXY及其小写别名，以及`NODE_EXTRA_CA_CERTS`。未指定Node证书时，可使用部署的`SSL_CERT_FILE`或`REQUESTS_CA_BUNDLE`作为附加CA文件。证书须在容器内只读可读；不继承任意Node选项、关闭TLS验证的变量、数据库凭据或源系统令牌。代理设置不会绕过网络域名允许列表。审计仅记录数值型token用量；未报告用量时标记未知，不估算费用。

真实模型验收入口为`scripts/check_harness_live.py`。先用`--list-cases`离线查看场景，再交互式隐藏输入密钥，或通过部署密钥注入`DEEPSEEK_API_KEY`；不接收命令行密钥参数。默认串行验证创作、修订两例，`--suite full`选择六例，涵盖修错、信息不足、提示注入和越权请求；不自动重试，失败停止。Linux需配置`ONTOLOGY_SKILL_RUNNER_SOCKET`，Windows便携包使用内置LPAC运行器；仅在实际隔离运行器可用时验证脚本结果，使用测试程序独立定义的期望值；没有运行器或自然语言语义尚需判断时明确标记待人工评估。报告不保存模型原文、技能包内容或密钥，既有90秒/8步/8192 tokens限制不代表货币费用上限。

```bash
.venv/bin/python scripts/check_harness_live.py --list-cases
.venv/bin/python scripts/check_harness_live.py --output work/harness-live-smoke.json
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
.venv/bin/python scripts/check_skills.py
.venv/bin/python scripts/check_entity_storage.py
RUN_FEDERATION_MYSQL_TESTS=1 RUN_JOBS_MYSQL_TESTS=1 \
  .venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/load_test.py --workers 4
```

脚本为每次运行生成随机数据库命名空间并清理，报告写入被忽略的 `work/`。安全回归涵盖越权、伪造机构/操作人、原件路径、跨租户同ID、缓存/任务、公开范围、CSRF、会话和角色撤销。业务回归另覆盖标准发布、CSV映射与导出、本地访谈草稿、权责流程和任务审核闭环。MCP验收使用真实MySQL源、标准MCP源和官方SDK调用方，验证五个工具、两源关联、漂移、撤权、部分失败及不留存业务行；源数据为隔离测试数据，不能代替企业系统联调。前端构建执行TypeScript检查。

约定性能基线为50个真实并发会话、20个租户混合读写：普通查询P95≤500ms、写入P95≤1s、总部汇总P95≤2s。默认压测每机构1000个资产和500条关系，10秒预热、90秒测量；输出错误率、各类延迟、吞吐量和API进程树CPU/内存。达标结论须结合当次报告中的硬件、进程数和数据量，不以开发环境结果代替集团生产容量规划。

## 备份与恢复

系统设置导出当前机构业务快照及原件，不包含其他机构、账户会话、模型密钥或外部源凭据。外部源结构、映射及其版本属于元数据，会随当前机构备份；源端查询结果不进入备份。恢复时数据源自动停用并清除源授权，管理员须重新核对凭据、启用和授权。技能包及历史版本随备份保留，恢复后删除测试证明、降为草稿并重建候选依赖，须重新测试和审核。普通实体历史可由管理员说明原因后还原为草稿；受治理技能通过专用版本流程维护。

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

Git源码分发包不包含工作区、便携runtime或Docker镜像；Windows完整运行包通过独立Release资产下载。源码打包：先构建前端，再运行 `.venv/bin/python scripts/package_release.py --output work/ontology-one-0.5.0.zip`。Linux内网容器部署需预先准备依赖和镜像；Windows运行包已内置应用运行依赖。两者均不依赖云托管平台，在线模型和外部数据源只在对应功能调用时访问。Windows二进制分发附带许可、固定依赖摘要和同版本MySQL对应源码资产，二次分发时保留这些资产。

## 后续交付顺序

0.5.0完成第四批技能治理，并提前引入Harness承担技能创作。0.6继续把现有AI辅助和本体应用迁入同一受控运行通道，接入已发布技能和授权MCP查询。当前其他AI小功能仍保留原有行为；技能作者通道不会执行任意业务查询或自动发布成果。

GitHub历史版本分别保留在`release/0.2.0`、`release/0.3.0`、`release/0.4.0`及对应`v0.2.0`、`v0.3.0`、`v0.4.0`标签，各版源码可独立拉取；本版开发不覆盖历史标签。

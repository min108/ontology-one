# Ontology One · 集团本体平台

`0.2.0` 集团账户与租户隔离试用版。React / TypeScript 前端、FastAPI 服务、MySQL 8 持久化，提供素材整理、数据/规则/算法/知识建模、审核发布、图谱、数据导入、项目任务、版本还原与机构备份。

本版用真实账户替换演示角色切换。仓库只保留运行、部署和验证必需的代码与说明；不含原始资料包、业务数据、上传原件、模型密钥或运行凭据。首次部署创建指定的51个机构目录与空工作区，业务成果由用户建立。

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

镜像多阶段编译前端，应用以非root用户运行。Compose分开运行数据库迁移、4个API进程及持久任务worker。只有迁移容器取得数据库schema owner凭据，用于安装审计禁止修改/删除的触发器；API和worker只获得本部署 `ontology_one_v020_*` 命名空间权限，不获得全局SUPER。默认每API进程的身份库连接上限16、全部机构业务连接总上限16（含空闲连接）；MySQL设置256个连接，为4个API进程、worker及运维留出预算。数据库管理员仍拥有恢复和变更数据库约束的能力。

升级已有部署前备份，停止API/worker，再运行迁移并启动：

```bash
docker compose stop api worker
docker compose up --build --force-recreate migrate
docker compose up -d --no-deps api worker
```

本版支持单机或共用同一存储卷的部署。维护锁要求所有API、worker和恢复命令使用同一卷；不包含跨主机分布式锁、自动故障转移或经验证的高可用方案。

## 本地开发

Python 3.10+、Node.js 22.12+、已配置的MySQL 8，以及支持`flock`的Linux/macOS。Windows原生启动请使用Docker。

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

## 工程结构与接入边界

- **身份控制库**：组织、账户、角色、授权、会话、安全审计和持久任务队列。服务端根据会话和目录派生数据库名，不采信客户端租户/数据库覆盖。
- **机构业务库**：实体、关系、版本、原文追溯、台账和业务日志。接口读取使用只读事务；写入、任务与文件操作复用同一身份上下文。
- **联邦层**：受权跨库查询、发布可见性过滤、带归属的复合图谱ID和跨机构关系审核；返回前复核授权。总部原件运维通过明确切换工作区进行。
- **文件与缓存**：租户专属路径，拒绝路径穿越、绝对路径和符号链接；缓存键包含租户、数据库和权限身份，业务提交触发失效。
- **任务层**：固定操作白名单、持久排队、身份及租户快照、幂等、租约/心跳、失败重试和撤权取消；切换界面不会把原任务迁移到新租户。
- **集成扩展**：`/api/auth/oidc/config` 明确返回未接入状态；`/api/integrations/capabilities` 与 `/api/integrations/tools/call` 提供复用登录身份的受控工具入口。集团SSO协议适配与完整MCP传输服务尚未接通，不能使用未验证请求头代替登录。

API返回请求编号，安全审计保留真实操作人、目标机构、结果和请求编号，不记录密码/令牌。在线AI按机构单独配置；未配置时仍可使用人工建模和本地整理功能。

## 验证与性能验收

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/check_restore_v020.py
# 下列命令只应连接独立测试MySQL，账号须可创建/删除测试库及安装触发器
.venv/bin/python scripts/check_security.py
RUN_FEDERATION_MYSQL_TESTS=1 RUN_JOBS_MYSQL_TESTS=1 \
  .venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/load_test.py --workers 4
```

脚本为每次运行生成随机数据库命名空间并清理，报告写入被忽略的 `work/`。安全回归涵盖越权、伪造机构/操作人、原件路径、跨租户同ID、缓存/任务、公开范围、CSRF、会话和角色撤销。前端构建执行TypeScript检查。

约定性能基线为50个真实并发会话、20个租户混合读写：普通查询P95≤500ms、写入P95≤1s、总部汇总P95≤2s。默认压测每机构1000个资产和500条关系，10秒预热、90秒测量；输出错误率、各类延迟、吞吐量和API进程树CPU/内存。达标结论须结合当次报告中的硬件、进程数和数据量，不以开发环境结果代替集团生产容量规划。

## 备份与恢复

系统设置导出当前机构业务快照及原件，不包含其他机构、账户会话或模型密钥。实体历史中管理员可说明原因后还原为草稿，须重新审核。

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

源码或离线分发包不包含工作区：先构建前端，再运行 `.venv/bin/python scripts/package_release.py --output work/ontology-one-0.2.0.zip`。

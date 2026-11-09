# Ontology One · 集团本体平台

通用本体平台 DEMO，版本 `0.1.0-demo`。提供素材整理、数据/规则/算法/知识资产管理、组织权责流程、项目任务、数据导入与字段映射、本体图谱、应用分析、版本复核和工作区备份。

这是项目的初始工程基线。React + TypeScript + Vite 前端，FastAPI 后端，MySQL 8 持久化。角色切换用于体验，尚不提供生产身份认证、权限隔离或高可用部署。未配置模型时可使用人工整理、本地流程解析和数据分析；在线 AI 需在系统设置中配置并验证。

## 启动

需要 Python 3.10+、Node.js 20.19+（或 22.12+），以及 Docker Compose 或已有 MySQL 8。

首次在项目根目录编译前端：

```bash
npm --prefix frontend ci
npm --prefix frontend run build
```

macOS / Linux 执行 `bash start.sh`；Windows 双击 `启动平台.bat`。启动器创建 Python 虚拟环境、安装依赖，并使用 Docker Compose 启动本机 MySQL。浏览器打开 `http://127.0.0.1:8765`。再次启动会继续使用已有工作区。

已有 MySQL 可先创建一个空数据库，再设置：

```bash
export MYSQL_HOST=127.0.0.1
export MYSQL_PORT=3306
export MYSQL_DATABASE=ontology_one
export MYSQL_USER=ontology
export MYSQL_PASSWORD='替换为数据库密码'
bash start.sh
```

也支持 `ONTOLOGY_MYSQL_URL=mysql://user:password@host:3306/ontology_one`，其优先级高于分项配置。`ONTOLOGY_PORT` 可调整应用端口，默认 8765。Windows PowerShell 使用 `$env:MYSQL_HOST="127.0.0.1"` 等形式。内置 Docker 账号仅用于本机 DEMO，数据库端口仅绑定回环地址。

## 演示内容与提交边界

首次启动自动生成少量虚构组织、人员、素材、资产、流程和项目，以及两张共 24 行的示例台账；原文和 CSV 保存在 `data/uploads/`。示例用于体验创建、映射、追溯和计算，不代表任何真实机构或业务事实。后续用户导入的数据和修订会持久保存。

仓库仅包含必要源码、依赖锁文件、启动配置、静态字体/图标及回归脚本。不包含原始资料包、法规库、历史文档、预置工作区、上传文件、模型密钥、依赖目录或编译产物。`data/` 为运行时目录，无需预先拷贝数据。

建议按以下路径体验：

1. **素材整理 → 业务资产**：阅读示例原文、查看资产来源，创建草稿并审核发布；数据、规则、算法支持 Excel 模板下载及导入。
2. **数据导入 → 本体图谱**：查看两张示例台账、已确认标准字段及来源行；也可导入自己的数据并调整映射。
3. **本体应用**：运行交付期限、材料齐备性、采购付款账户关联分析；查看结果与原始记录。
4. **权责规范 / 专题工作室 / 经验萃取室**：体验流程、任务和知识整理；变更复核与审核维护负责版本及关联管理。

## 开发与验证

```bash
# 已安装依赖且 MySQL 可连接时
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
npm --prefix frontend run dev

# 集成回归：使用独立临时数据库，账号需有建库/删库权限
.venv/bin/python scripts/check_demo.py
.venv/bin/python scripts/check_experience.py
```

回归脚本使用当前 `MYSQL_*` 服务器设置，创建并清理自己的临时数据库，不使用业务库。前端构建同时执行 TypeScript 检查。

系统设置可导出工作区备份。恢复时先停止平台，指定一个空数据库：

```bash
.venv/bin/python scripts/restore_backup.py backup.zip --database ontology_one_restore --yes --with-files
```

恢复目标由 `--database` 决定；已有数据表的库会被拒绝。恢复后设置对应连接参数再启动。模型密钥位于本机 `data/runtime/ai.json`，不进入备份或安装包。

如需离线分发已编译界面，先构建前端，再执行：

```bash
.venv/bin/python scripts/package_release.py --output work/ontology-one-demo.zip
```

# 0.7.0 合成升级夹具

生成器必须在未修改的 `1359118` 检出目录运行。它只导入该检出的代码，使用原有账户、演示、阅读、权责、知识专栏、审核发布与备份函数；未直接插入业务数据。所有姓名、业务记录与口令均为合成测试数据。来源完整提交、每张表行数和摘要见 `manifest.json`。

所有普通登录账户的公开测试口令是 `SyntheticFixture9!`。`fixture.builder`（内部角色 `modeler`）、`fixture.reviewer`、`fixture.admin` 的所属机构均为 `dept-01`，无需强制改密。总部超管为 `fixture.superadmin`；`fixture.change-password` 单独保留强制改密标记。不要把这些公开账户用于真实部署。

`manifest.browser` 提供浏览器登录账户；`manifest.objects` 提供固定详情编号。总部、总部部门 `dept-01`、子公司 `sub-01` 有业务库；目录中 `dept-02` 尚未开通。部门包含演示安装回执、台账、原件、原文版本和锚点、技能包、数据源元数据及映射、专题任务、知识专栏、公开资产及独立审核历史。`workspace-v070.backup` 是原版本实际导出的 ZIP 格式机构备份，扩展名避免镜像忽略规则；`originals.backup` 是快照配套原件。

设置独立的 `MYSQL_*` 和空目录 `ONTOLOGY_STORAGE_ROOT` 后，从要启动的代码检出目录执行：

```sh
python /path/to/tests/fixtures/v070/load.py
```

装载器只还原原始结构与数据，不调用当前版本初始化或迁移。控制目录中的库名按目标命名空间重新派生，其余编号、关联、历史和审计数据原样保留。目标库或原件目录已存在时拒绝覆盖。随后可以直接启动原 0.7.0 API；升级测试则执行当前 `scripts/migrate.py`。迁移前后哈希比较应使用装载后的同一隔离库，避免把必要的目录库名重映射误当数据变化。

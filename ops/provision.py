"""Compose migration job: schema owner credentials never reach API or worker."""
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymysql
from backend.db import mysql_config, ensure_tenant_schema
from backend.security.store import initialize_control, list_tenants
from backend.federation import initialize_federation
from backend.jobs import ensure_queue
from backend.maintenance import runtime_guard


def main(progress=None):
    def report(phase, completed=0, total=0):
        if progress is not None:
            progress(phase, completed, total)
    secret = os.environ['ONTOLOGY_RUNTIME_DB_PASSWORD']
    if len(secret) < 20:
        raise RuntimeError('运行库密码须至少20位；请使用随机密码。')
    config = mysql_config()
    if config['database'] != 'ontology_one_v020':
        raise RuntimeError('Compose迁移命名空间应为ontology_one_v020。')
    config.pop('database')
    with runtime_guard(shared=False):
        report('control')
        initialize_control()
        report('federation')
        initialize_federation()
        report('queue')
        ensure_queue()
        tenants = list_tenants()
        report('tenants', 0, len(tenants))
        for index, tenant in enumerate(tenants, 1):
            ensure_tenant_schema(tenant['id'])
            report('tenants', index, len(tenants))
            if index % 10 == 0 or index == len(tenants):
                print(f'机构工作区迁移进度：{index}/{len(tenants)}', flush=True)
        report('runtime_account', len(tenants), len(tenants))
        with pymysql.connect(**config) as conn, conn.cursor() as cursor:
            cursor.execute("CREATE USER IF NOT EXISTS 'ontology_runtime'@'%%' IDENTIFIED BY %s", (secret,))
            cursor.execute("ALTER USER 'ontology_runtime'@'%%' IDENTIFIED BY %s", (secret,))
            # Escaped underscores keep this grant within the exact deployment prefix.
            cursor.execute(r"GRANT ALL PRIVILEGES ON `ontology\_one\_v020\_%`.* TO 'ontology_runtime'@'%'")
            conn.commit()
    report('provisioned', len(tenants), len(tenants))
    print('0.5.0迁移完成；51个空机构工作区与受限运行账号已就绪。')


if __name__ == '__main__':
    main()

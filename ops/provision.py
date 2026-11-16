"""Compose migration job: schema owner credentials never reach API or worker."""

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymysql
from backend.version import APP_VERSION
from backend.db import mysql_config
from backend.infra.migrations import run_migrations


def main(progress=None):
    def report(phase, completed=0, total=0):
        if progress is not None:
            progress(phase, completed, total)

    secret = os.environ["ONTOLOGY_RUNTIME_DB_PASSWORD"]
    if len(secret) < 20:
        raise RuntimeError("运行库密码须至少20位；请使用随机密码。")
    config = mysql_config()
    if config["database"] != "ontology_one_v020":
        raise RuntimeError("Compose迁移命名空间应为ontology_one_v020。")
    config.pop("database")

    def runtime_account() -> None:
        report("runtime_account", 10000, 10000)
        with pymysql.connect(**config) as conn, conn.cursor() as cursor:
            cursor.execute("CREATE USER IF NOT EXISTS 'ontology_runtime'@'%%' IDENTIFIED BY %s", (secret,))
            cursor.execute("ALTER USER 'ontology_runtime'@'%%' IDENTIFIED BY %s", (secret,))
            # Escaped underscores keep this grant within the exact deployment prefix.
            cursor.execute(r"GRANT ALL PRIVILEGES ON `ontology\_one\_v020\_%`.* TO 'ontology_runtime'@'%'")
            conn.commit()

    run_migrations(provision_all=True, progress=progress, finalize=runtime_account)
    report("provisioned", 10000, 10000)
    from backend.security import store

    print(f"{APP_VERSION}迁移完成；{len(store.list_tenants())}个机构工作区与受限运行账号已就绪。")


if __name__ == "__main__":
    main()

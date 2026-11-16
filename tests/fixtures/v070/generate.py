"""Generate synthetic fixture using only code from a clean 1359118 checkout.

Run from that checkout: python /path/to/generate.py --output /path/to/v070.
MYSQL_* and ONTOLOGY_STORAGE_ROOT must select an isolated disposable deployment.
This generator never alters the historical checkout or existing databases.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from contextlib import closing
from pathlib import Path

SOURCE = Path.cwd().resolve()
COMMIT = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
if not COMMIT.startswith("1359118") or (SOURCE / "VERSION").read_text().strip() != "0.7.0":
    raise SystemExit("Generator requires the actual 1359118 checkout.")
if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], text=True).strip():
    raise SystemExit("Historical tracked files must be unchanged.")
sys.path.insert(0, str(SOURCE))

from backend import asset_catalog, authority, db, demo, demo_data, materials, system  # noqa: E402
from backend.federation import initialize_federation  # noqa: E402
from backend.jobs import ensure_queue  # noqa: E402
from backend.main import _create_entity, _entity_action, detail  # noqa: E402
from backend.security import store  # noqa: E402
from backend.security.context import use_context  # noqa: E402

PASSWORD = "SyntheticFixture9!"
TENANTS = ("hq", "dept-01", "sub-01")


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def snapshot(database: str) -> dict:
    with closing(db._raw_connection(database)) as raw:
        conn = db.Connection(raw)
        names = [r[0] for r in conn.execute("SHOW TABLES")]
        result = {"tables": {}, "ddl": {}, "columns": {}, "triggers": {}}
        for name in names:
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM `{name}`")]
            result["tables"][name] = sorted(rows, key=canonical)
            result["ddl"][name] = conn.execute(f"SHOW CREATE TABLE `{name}`").fetchone()[1]
            result["columns"][name] = [r[0] for r in conn.execute(f"SHOW COLUMNS FROM `{name}`")]
        for row in conn.execute("SHOW TRIGGERS"):
            name = row["Trigger"]
            result["triggers"][name] = conn.execute(f"SHOW CREATE TRIGGER `{name}`").fetchone()[2]
    return result


def login(username: str) -> object:
    raw = store.login(username, PASSWORD, "127.0.0.1", "historical-synthetic-fixture")
    return store.authenticate_session(raw, "fixture")[0]


class Request:
    async def json(self) -> dict:
        return {"title": "合成知识专栏", "scope": "topic", "intro": "0.7.0 升级回归资料"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = store._config()
    with closing(db._raw_connection("information_schema")) as raw, raw.cursor() as cursor:
        cursor.execute(
            "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (config["database"],)
        )
        if cursor.fetchone():
            raise RuntimeError("Fixture generation requires a new database namespace.")
    store.initialize_control()
    initialize_federation()
    ensure_queue()
    store.bootstrap_superadmin("fixture.superadmin", "InitialSynthetic9!")
    raw = store.login("fixture.superadmin", "InitialSynthetic9!", "127.0.0.1", "fixture")
    owner = store.authenticate_session(raw, "fixture")[0]
    raw = store.change_password(owner, "InitialSynthetic9!", PASSWORD, "127.0.0.1", "fixture")
    owner = store.authenticate_session(raw, "fixture")[0]
    accounts = [
        {
            "username": "fixture.superadmin",
            "password": PASSWORD,
            "role": "admin",
            "tenant_id": "hq",
            "superadmin": True,
            "must_change_password": False,
        }
    ]
    for name, role, forced in (
        ("admin", "admin", False),
        ("builder", "modeler", False),
        ("reviewer", "reviewer", False),
        ("change-password", "modeler", True),
    ):
        username = "fixture." + name
        store.create_user(
            owner,
            username=username,
            display_name="合成" + name,
            tenant_id="dept-01",
            role=role,
            password=PASSWORD,
            must_change_password=forced,
        )
        accounts.append(
            {
                "username": username,
                "password": PASSWORD,
                "role": role,
                "tenant_id": "dept-01",
                "must_change_password": forced,
            }
        )
    for tenant in TENANTS:
        db.ensure_tenant_schema(tenant)
    builder, reviewer = login("fixture.builder"), login("fixture.reviewer")
    with use_context(login("fixture.admin")):
        loaded = demo.load(demo.Confirm(confirm=True))
    with use_context(builder):
        details = {
            "material": "DEMO07-MATERIAL-RULES",
            "project": "DEMO07-PROJECT-VERIFY",
            "task": next(i["id"] for i in loaded["items"] if i["kind"] == "task"),
            "dataset": "DEMO07-DATASET-CONTRACTS",
            "source": demo_data.SOURCE_ID,
            "skill": next(i["id"] for i in loaded["items"] if i["kind"] == "skill"),
        }
        entity = _create_entity(
            {
                "id": "FIXTURE-KNOWLEDGE-001",
                "kind": "knowledge",
                "title": "合成升级核对说明",
                "summary": "保留编号、历史和关联",
                "payload": {"content": "本条为合成回归资料。"},
            }
        )
        details["asset"] = entity["id"]
        auth = authority.create_asset(
            authority.SaveRequest(
                title="合成岗位权责",
                org_id="dept-01",
                spec=authority.Spec(
                    type="position",
                    purpose="明确核对职责",
                    basis="合成制度第一版",
                    scope="合成资料",
                    org_ids=["dept-01"],
                    sections=[authority.Section(id="check", title="核对职责", content="核对合成记录并记录结果")],
                ),
            )
        )
        details["authority"] = auth["entity"]["id"]
        collection = asyncio.run(asset_catalog.create_collection(Request()))
        details["collection"] = collection.get("entity", collection.get("collection", collection))["id"]
        # The original reading workflow creates precise anchors and historical versions.
        materials.reading_workspace(details["material"])
        with closing(db.connect()) as conn, conn:
            item = materials.material(conn, details["material"])
            reading, source = materials._reading_draft(conn, details["material"])
            materials._confirm_reading(conn, item, reading, source)
        for ident in [
            details["asset"],
            details["authority"],
            *[i["id"] for i in loaded["items"] if i["kind"] == "data"],
        ]:
            _entity_action(
                ident,
                {"action": "submit", "reason": "合成资料提交独立审核", "expected_version": detail(ident)["version"]},
            )
            with use_context(reviewer):
                _entity_action(
                    ident,
                    {"action": "approve", "reason": "独立核对合成资料", "expected_version": detail(ident)["version"]},
                )
                _entity_action(
                    ident,
                    {"action": "publish", "reason": "发布合成回归资料", "expected_version": detail(ident)["version"]},
                )
    with use_context(login("fixture.admin")):
        demo.prepare_mappings(demo.Confirm(confirm=True))
    # Export through the original 0.7.0 endpoint, including its exact ZIP format.
    with use_context(login("fixture.admin")):
        response = system.backup()
        shutil.copyfile(response.path, output / "workspace-v070.backup")
        if response.background:
            asyncio.run(response.background())
    manifest = {
        "source_commit": COMMIT,
        "version": "0.7.0",
        "tenants": list(TENANTS),
        "unprovisioned_tenant": "dept-02",
        "accounts": accounts,
        "details": details,
        "files": {},
        "tables": {},
        "database_namespace": db.mysql_config()["database"],
        "permissions": {
            "reviewer": "只读详情与独立审核；不允许创建、修改或加载演示资料",
            "builder": "建设；不允许审核发布",
            "admin": "机构管理及建设审核；仍遵守独立审核规则",
        },
    }
    manifest["browser"] = {
        "workspace": "dept-01",
        "accounts": {
            ("superadmin" if account.get("superadmin") else account["role"]): {
                "username": account["username"],
                "password": account["password"],
            }
            for account in accounts
            if not account["must_change_password"]
        },
    }
    manifest["objects"] = details
    databases = {"control": config["database"], **{tenant: store.get_tenant(tenant)["db_name"] for tenant in TENANTS}}
    for name, database in databases.items():
        value = snapshot(database)
        (output / (name + ".json")).write_text(canonical(value) + "\n", encoding="utf-8")
        manifest["tables"][name] = {
            table: {"rows": len(rows), "sha256": hashlib.sha256(canonical(rows).encode()).hexdigest()}
            for table, rows in value["tables"].items()
        }
    with zipfile.ZipFile(output / "originals.backup", "w", zipfile.ZIP_DEFLATED) as archive:
        storage = Path(os.environ["ONTOLOGY_STORAGE_ROOT"])
        for file in sorted(storage.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(storage).as_posix())
    for name in ["control.json", *[t + ".json" for t in TENANTS], "workspace-v070.backup", "originals.backup"]:
        manifest["files"][name] = hashlib.sha256((output / name).read_bytes()).hexdigest()
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source_commit": COMMIT, "output": str(output), "tenants": list(TENANTS)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Provision browser identities or clear only the derived index for the S12 rebuild drill."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import closing

from run import ROOT, database_names

from backend import db
from backend.security import store
from backend.security.context import use_context

TENANT = "sub-01"
ROLES = {"builder": "modeler", "reviewer": "reviewer", "admin": "admin", "viewer": "viewer"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("accounts", "clear"))
    args = parser.parse_args()
    namespace = os.environ["MYSQL_DATABASE"]
    allowed = database_names(namespace, [TENANT])
    manifest = json.loads((ROOT / "tests/fixtures/v070/manifest.json").read_text())
    password = manifest["browser"]["accounts"]["modeler"]["password"]
    with store.control_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT db_name FROM sec_tenants WHERE id=%s", (TENANT,))
        if cursor.fetchone()["db_name"] not in allowed - {namespace, namespace + "_control"}:
            raise ValueError("Search fixture must belong to this runner's disposable tenant")
    agent = "r4b-search-fixture"
    if args.action == "accounts":
        username = manifest["browser"]["accounts"]["superadmin"]["username"]
        token = store.login(username, password, "127.0.0.1", agent)
        principal = store.authenticate_session(token, agent)[0]
        for name, role in ROLES.items():
            ident = store.create_user(
                principal,
                username="b2.search." + name,
                display_name={
                    "builder": "合同建设人员",
                    "reviewer": "合同审核人员",
                    "admin": "机构管理员",
                    "viewer": "查阅人员",
                }[name],
                tenant_id=TENANT,
                role="modeler" if role == "viewer" else role,
                password=password,
                must_change_password=False,
            )
            if role == "viewer":
                # This represents an existing read-only account before its first session.
                # R4-B upper retains the three original account-management role choices.
                with store.control_connection() as connection, connection.cursor() as cursor:
                    cursor.execute("UPDATE sec_users SET role=%s WHERE id=%s", (role, ident))
        print(json.dumps({"tenant_id": TENANT, "roles": list(ROLES.values())}))
        return
    token = store.login("b2.search.admin", password, "127.0.0.1", agent)
    principal = store.authenticate_session(token, agent)[0]
    if principal.tenant_id != TENANT or principal.role != "admin":
        raise ValueError("S12 requires the institution's own administrator")
    with use_context(principal), closing(db.connect()) as conn, conn:
        before = conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0]
        if before < 1:
            raise ValueError("S12 must exercise a populated index")
        conn.execute("DELETE FROM search_index")
        after = conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0]
        if after:
            raise ValueError("Derived index was not cleared")
    print(json.dumps({"tenant_id": TENANT, "before": before, "after": after}))


if __name__ == "__main__":
    main()

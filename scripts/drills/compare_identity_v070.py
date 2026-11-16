"""Compare complete legacy grants/audit HTTP results before and after migration.

Both releases run their own ASGI app in separate interpreters. Only the original
process logs in; the current process reuses those sessions against the same DBs.
No authorization, route, SQL, time, or UUID function is mocked.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = "1359118dd672431dcd0cde2eb81f2b0f994eef37"


def probe() -> None:
    # This script stays installation-owned; imports resolve to the selected release.
    sys.path.insert(0, str(Path.cwd()))
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.security import store

    payload = json.load(sys.stdin)
    sessions = payload.get("sessions")
    if sessions is None:
        sessions = {}
        for label, username, workspace in (
            ("hq", "fixture.superadmin", None),
            ("hq_in_department", "fixture.superadmin", "dept-01"),
            ("department_admin", "fixture.admin", None),
            ("modeler", "fixture.builder", None),
            ("reviewer", "fixture.reviewer", None),
        ):
            raw = store.login(username, "SyntheticFixture9!", "127.0.0.1", "cross-version-identity")
            principal = store.authenticate_session(raw, "identity-comparison")[0]
            if workspace:
                store.switch_workspace(principal, workspace)
            sessions[label] = raw
    outputs = {}
    # No lifespan: this is an HTTP contract comparison, not a bootstrap test.
    # The fixture/explicit migration own schema initialization. Real middleware,
    # auth/session lookup, policy, handlers and MySQL queries execute unchanged.
    for label, raw in sessions.items():
        client = TestClient(app)
        try:
            client.cookies.set("ontology_session", raw)
            grants = client.get("/api/admin/grants")
            pages = []
            page = 1
            while True:
                audit = client.get("/api/admin/audit", params={"page": page, "page_size": 100})
                body = audit.json()
                pages.append({"status": audit.status_code, "body": body})
                if audit.status_code != 200 or page * 100 >= body["total"]:
                    break
                page += 1
            outputs[label] = {"grants": {"status": grants.status_code, "body": grants.json()}, "audit": pages}
        finally:
            client.close()
    json.dump({"sessions": sessions, "outputs": outputs}, sys.stdout, ensure_ascii=False)


def call_probe(source: Path, sessions: dict | None = None) -> dict:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--probe"],
        cwd=source,
        env=environment,
        input=json.dumps({"sessions": sessions}),
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    return json.loads(result.stdout)


def audit_rows(outputs: dict, scope: str) -> list:
    return [row for page in outputs[scope]["audit"] for row in page["body"]["items"]]


def compare(before: dict, after: dict, historical_ids: set[int]) -> dict:
    # Exact response comparison: no dropped fields, ID normalization or filters.
    if before != after:
        raise AssertionError("Cross-version grants/audit HTTP responses differ; inspect the retained report")
    for scope in ("hq", "hq_in_department"):
        assert before[scope]["grants"]["status"] == 200
        assert before[scope]["grants"]["body"]["items"]
        assert all(page["status"] == 200 for page in before[scope]["audit"])
        rows = audit_rows(before, scope)
        assert historical_ids <= {row["id"] for row in rows}
        assert len({row["tenant_id"] for row in rows}) > 1
    assert before["hq"] == before["hq_in_department"]
    local = before["department_admin"]
    assert local["grants"]["status"] == 403
    assert all(page["status"] == 200 for page in local["audit"])
    assert audit_rows(before, "department_admin")
    assert {row["tenant_id"] for row in audit_rows(before, "department_admin")} == {"dept-01"}
    assert audit_rows(before, "department_admin") == [
        row for row in audit_rows(before, "hq") if row["tenant_id"] == "dept-01"
    ]
    for scope in ("modeler", "reviewer"):
        assert before[scope]["grants"]["status"] == 403
        assert len(before[scope]["audit"]) == 1
        assert before[scope]["audit"][0]["status"] == 403
    return {
        "scope_count": len(before),
        "grant_count": len(before["hq"]["grants"]["body"]["items"]),
        "historical_audit_count": len(historical_ids),
        "hq_audit_count": len(audit_rows(before, "hq")),
        "department_audit_count": len(audit_rows(before, "department_admin")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v070-source", type=Path, required=True)
    parser.add_argument("--current-source", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, current = args.v070_source.resolve(), args.current_source.resolve()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=source, text=True
    ).strip()
    if commit != ORIGINAL or dirty or (source / "VERSION").read_text().strip() != "0.7.0":
        raise RuntimeError("Comparison requires the unmodified actual accepted 1359118 checkout")
    sys.path.insert(0, str(current))
    from backend import db
    from backend.infra import migrations
    from tests.fixtures.v070.load import load_fixture

    namespace = "ontology_r17compare_" + secrets.token_hex(5)
    work = ROOT / "work"
    work.mkdir(exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    historical = json.loads((ROOT / "tests/fixtures/v070/control.json").read_text())["tables"]["sec_audit"]
    report = {
        "source_commit": commit,
        "current_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=current, text=True).strip(),
        "namespace": namespace,
        "passed": False,
    }
    with (
        tempfile.TemporaryDirectory(prefix="identity-compare-", dir=work) as temporary,
        patch.dict(
            os.environ,
            {
                "MYSQL_DATABASE": namespace,
                "ONTOLOGY_STORAGE_ROOT": str(Path(temporary) / "tenants"),
                "ONTOLOGY_BACKUP_DIR": str(Path(temporary) / "backups"),
                "ONTOLOGY_WORKER_ENABLED": "0",
            },
        ),
    ):
        os.environ.pop("ONTOLOGY_MYSQL_URL", None)
        original = {}
        try:
            load_fixture()
            original = call_probe(source)
            report["before"] = original["outputs"]
            migrations.run_migrations()
            migrated = call_probe(current, original["sessions"])
            report["after"] = migrated["outputs"]
            report["summary"] = compare(report["before"], report["after"], {row["id"] for row in historical})
            report["passed"] = True
        finally:
            with closing(db._raw_connection("information_schema")) as raw:
                conn = db.Connection(raw)
                names = [
                    row[0]
                    for row in conn.execute(
                        "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE LEFT(SCHEMA_NAME,?)=?",
                        (len(namespace), namespace),
                    )
                ]
                for name in names:
                    if not name.startswith(namespace):
                        raise RuntimeError("Refusing cleanup outside comparison namespace")
                    conn.execute(f"DROP DATABASE `{name}`")
            report["cleaned_databases"] = names
            encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            authentication = ["SyntheticFixture9!", *original.get("sessions", {}).values()]
            if any(value in encoded for value in authentication):
                raise RuntimeError("Refusing to persist authentication material in comparison evidence")
            args.output.write_text(encoded)
    print(
        json.dumps(
            {
                "source_commit": report["source_commit"],
                "current_commit": report["current_commit"],
                "passed": report["passed"],
                "exact_response_equality": True,
                "scopes": list(report["before"]),
                "cleaned_database_count": len(report["cleaned_databases"]),
                **report["summary"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    if sys.argv[1:] == ["--probe"]:
        probe()
    else:
        main()

"""Search index migration, native ngram behavior and trusted recovery."""

import copy
import hashlib
import json
import os
import unittest
import zipfile
from contextlib import closing
from unittest.mock import patch

from backend import db, system
from backend.infra import backup, migrations, recovery_schema
from backend.maintenance import runtime_guard
from backend.security import store
from scripts import restore_backup
from tests.fixtures.v070.load import FIXTURE
from tests.migrations import test_integration as foundation
from tests.migrations import test_r4b1 as institution


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class SearchMigration(unittest.TestCase):
    setUp = foundation.MigrationMySQL.setUp
    tearDown = foundation.MigrationMySQL.tearDown
    fixture = foundation.MigrationMySQL.fixture
    upgrade = institution.InstitutionMigration.upgrade
    principal = institution.InstitutionMigration.principal
    restore_archive = institution.InstitutionMigration.restore_archive
    clear_fixture_workspace = institution.InstitutionMigration.clear_fixture_workspace

    def test_native_ngram_indexes_search_title_alias_and_body_separately(self) -> None:
        names = self.fixture()
        migrations.run_migrations()
        with closing(db._raw_connection(names["dept-01"])) as raw:
            conn = db.Connection(raw)
            ddl = conn.execute("SHOW CREATE TABLE search_index").fetchone()[1]
            for index, columns in (("ft_si_title", "`title`,`aliases`"), ("ft_si_body", "`body`")):
                (line,) = (line for line in ddl.splitlines() if f"`{index}`" in line)
                self.assertIn(f"FULLTEXT KEY `{index}` ({columns})", line)
                self.assertIn("WITH PARSER `ngram`", line)
            conn.execute(
                "INSERT INTO search_index(doc_id,doc_type,title,aliases,body,updated_at) VALUES(?,?,?,?,?,?)",
                ("el:b2-grammar", "element", "碧海明珠", "银杏田园", "苍松流泉", db.now()),
            )
            conn.commit()

            def hits(columns: str, query: str) -> int:
                return conn.execute(
                    f"SELECT COUNT(*) FROM search_index WHERE doc_id=? AND MATCH({columns}) AGAINST(? IN BOOLEAN MODE)",
                    ("el:b2-grammar", query),
                ).fetchone()[0]

            for columns, query, expected in (
                ("title,aliases", "海明", 1),
                ("title,aliases", "杏田", 1),
                ("body", "松流", 1),
                ("body", "海明", 0),
                ("title,aliases", "松流", 0),
            ):
                with self.subTest(columns=columns, query=query):
                    self.assertEqual(hits(columns, query), expected)

            def infix_matches() -> tuple[int, int, int]:
                return hits("title,aliases", "海明"), hits("title,aliases", "杏田"), hits("body", "松流")

            for index, columns, expected in (
                ("ft_si_title", "title,aliases", (0, 0, 1)),
                ("ft_si_body", "body", (1, 1, 0)),
            ):
                with self.subTest(default_parser=index):
                    conn.execute(f"ALTER TABLE search_index DROP INDEX {index}")
                    conn.execute(f"ALTER TABLE search_index ADD FULLTEXT KEY {index} ({columns})")
                    self.assertEqual(infix_matches(), expected)
                    with runtime_guard(shared=False), self.assertRaises(backup.BackupError):
                        backup.create_upgrade_backup(store.list_tenants())
                    conn.execute(f"ALTER TABLE search_index DROP INDEX {index}")
                    conn.execute(f"ALTER TABLE search_index ADD FULLTEXT KEY {index} ({columns}) WITH PARSER ngram")
                    self.assertEqual(infix_matches(), (1, 1, 1))

    def assert_source_documents(self, database: str) -> dict[str, dict]:
        snapshot = backup.snapshot_database(database)
        docs = {row["doc_id"]: row for row in snapshot["tables"]["search_index"]}
        entities = {row["id"]: row for row in snapshot["tables"]["entities"]}
        for ident in ("FIXTURE-KNOWLEDGE-001", "DEMO07-RULE-PAYMENT"):
            source = entities[ident]
            document = docs["el:" + ident]
            self.assertEqual(
                (document["doc_type"], document["entity_id"], document["title"], document["status"]),
                ("element", ident, source["title"], source["status"]),
            )
        self.assertEqual(docs["el:DEMO07-RULE-PAYMENT"]["status"], "draft")
        (chunk,) = snapshot["tables"]["chunks"]
        self.assertIn(chunk["content"], docs["chunk:" + chunk["id"]]["body"])
        self.assertFalse(any(row["doc_type"] == "artifact" for row in docs.values()))
        return docs

    def private_report_and_poisoned_cache(self, database: str) -> None:
        # Adversarial storage fixtures: a real private report, a spurious indexed copy,
        # and a cache row falsely marking a draft published. Both caches must be disposable.
        with closing(db._raw_connection(database)) as raw:
            conn = db.Connection(raw)
            now = db.now()
            conn.execute(
                "INSERT INTO artifacts(id,title,template,owner_user_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("ART-b2-private", "内部报告保密文字", "report", "fixture-owner", now, now),
            )
            conn.execute(
                "INSERT INTO search_index(doc_id,doc_type,entity_id,status,title,body,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("art:ART-b2-private", "artifact", "ART-b2-private", "published", "污染检索名称", "污染检索正文", now),
            )
            changed = conn.execute(
                "UPDATE search_index SET title='污染检索名称',status='published' WHERE doc_id=?",
                ("el:DEMO07-RULE-PAYMENT",),
            )
            self.assertEqual(changed.rowcount, 1)
            conn.commit()

    def test_upgrade_fresh_provision_and_rebuild_replay_are_lossless_and_deterministic(self) -> None:
        from backend.infra.search_index import rebuild_index

        names = self.fixture()
        old = backup.snapshot_database(names["dept-01"])
        migrations.run_migrations()
        self.assert_source_documents(names["dept-01"])
        before = backup.snapshot_database(names["dept-01"])
        for table, digest in old["hashes"].items():
            self.assertEqual(before["hashes"][table], digest)
        self.assertEqual(migrations.run_migrations(), {"backup": None, "migrated": []})
        with closing(db._raw_connection(names["dept-01"])) as raw:
            conn = db.Connection(raw)
            rebuild_index(conn)
            conn.commit()
        self.assertEqual(backup.snapshot_database(names["dept-01"])["hashes"], before["hashes"])
        migrations.provision_tenant("dept-02")
        fresh = backup.snapshot_database(store.get_tenant("dept-02")["db_name"])
        (root,) = fresh["tables"]["entities"]
        (document,) = fresh["tables"]["search_index"]
        self.assertEqual((document["doc_id"], document["title"]), ("el:dept-02", root["title"]))
        self.assertEqual(fresh["ddl"]["search_index"], before["ddl"]["search_index"])

    def test_both_portable_generations_derive_documents_after_local_restore(self) -> None:
        database = self.upgrade()["dept-01"]
        person = self.principal()
        self.private_report_and_poisoned_cache(database)
        current_archive = self.root / "current.backup"
        with closing(db._raw_connection(database)) as raw:
            system.export_workspace_backup(
                db.Connection(raw), "dept-01", self.root / "tenants" / "dept-01", current_archive
            )
        with zipfile.ZipFile(current_archive) as archive:
            portable = json.loads(archive.read(restore_backup.SNAPSHOT))
            self.assertNotIn("search_index", portable["tables"])
            self.assertIn("ART-b2-private", [r["id"] for r in portable["tables"]["artifacts"]])
            self.assertNotIn("污染检索名称", backup.canonical(portable))
            for counterfeit in ([], [{"doc_id": "art:ART-b2-private"}]):
                forged = copy.deepcopy(portable)
                forged["tables"]["search_index"] = counterfeit
                with self.assertRaises(restore_backup.RestoreError):
                    restore_backup.validate_snapshot(
                        forged, "dept-01", restore_backup.schema_columns(include_rebuildable=True)
                    )
        for path in (current_archive, FIXTURE / "workspace-v070.backup"):
            with self.subTest(archive=path.name):
                self.clear_fixture_workspace(person)
                result = self.restore_archive(path, person)
                self.assertGreater(result["rows"], 100)
                self.assertGreater(result["files"], 0)
                docs = self.assert_source_documents(database)
                self.assertNotIn("污染检索名称", backup.canonical(docs))
                self.assertNotIn("内部报告保密文字", backup.canonical(docs))
                snapshot = backup.snapshot_database(database)
                for skill in snapshot["tables"]["skill_documents"]:
                    self.assertEqual(docs["el:" + skill["entity_id"]]["status"], "draft")

    def test_full_recovery_rebuilds_from_sources_and_excludes_poisoned_cache(self) -> None:
        database = self.upgrade()["dept-01"]
        clean = self.assert_source_documents(database)
        self.private_report_and_poisoned_cache(database)
        with runtime_guard(shared=False):
            archive = backup.create_upgrade_backup(store.list_tenants())
            _, checked = backup.verify_upgrade_backup(archive)
            persisted = checked[database]
            self.assertEqual(
                recovery_schema.validate_schema(persisted, "tenant"), recovery_schema.trusted_schema("tenant", 9)
            )
            self.assertEqual(persisted["tables"]["search_index"], [])
            self.assertIn("内部报告保密文字", backup.canonical(persisted["tables"]["artifacts"]))
            self.assertNotIn("污染检索名称", (archive / "dept-01.json").read_text())
            with closing(db._raw_connection(database)) as raw:
                conn = db.Connection(raw)
                conn.execute("DELETE FROM search_index")
                conn.execute("DELETE FROM artifacts WHERE id='ART-b2-private'")
                conn.commit()
            backup.restore_upgrade_backup(archive)
        recovered = backup.snapshot_database(database)
        self.assertEqual(self.assert_source_documents(database), clean)
        self.assertIn("ART-b2-private", {r["id"] for r in recovered["tables"]["artifacts"]})
        self.assertEqual(
            recovery_schema.normalized_ddl(recovered["ddl"]["search_index"]),
            recovery_schema.normalized_ddl(persisted["ddl"]["search_index"]),
        )
        self.assertEqual(recovered["ddl"]["search_index"].count("WITH PARSER `ngram`"), 2)
        migrations.validate_runtime()

    def test_full_recovery_rejects_each_changed_parser_and_forged_cache_before_writes(self) -> None:
        database = self.upgrade()["dept-01"]
        with runtime_guard(shared=False):
            archive = backup.create_upgrade_backup(store.list_tenants())
        path, manifest_path = archive / "dept-01.json", archive / "manifest.json"
        original, manifest = json.loads(path.read_text()), json.loads(manifest_path.read_text())
        with closing(db._raw_connection(database)) as raw:
            conn = db.Connection(raw)
            conn.execute("UPDATE ont_modules SET scope_note='恢复前保留' WHERE id='MOD-DEFAULT'")
            conn.commit()
        before = backup.snapshot_database(database)
        for index in ("ft_si_title", "ft_si_body", "forged-cache"):
            for replacement in ("", "mecab") if index != "forged-cache" else ("row",):
                value = copy.deepcopy(original)
                if index == "forged-cache":
                    forged = {**before["tables"]["search_index"][0], "status": "published", "body": "私有成果伪造索引"}
                    value["tables"]["search_index"] = [forged]
                    value["hashes"]["search_index"] = {
                        "rows": 1,
                        "sha256": hashlib.sha256(backup.canonical([forged]).encode()).hexdigest(),
                    }
                else:
                    ddl = value["ddl"]["search_index"]
                    (line,) = (line for line in ddl.splitlines() if f"`{index}`" in line)
                    clause = "/*!50100 WITH PARSER `ngram` */"
                    self.assertIn(clause, line)
                    new_line = line.replace("`ngram`", "`mecab`") if replacement else line.replace(clause, "")
                    value["ddl"]["search_index"] = ddl.replace(line, new_line)
                    self.assertEqual(value["ddl"]["search_index"].count("WITH PARSER `ngram`"), 1)
                path.write_text(backup.canonical(value))
                manifest["files"][path.name] = backup.digest_file(path)
                manifest_path.write_text(backup.canonical(manifest))
                with (
                    self.subTest(index=index, replacement=replacement),
                    runtime_guard(shared=False),
                    self.assertRaises(backup.BackupError),
                ):
                    backup.restore_upgrade_backup(archive)
                self.assertIsNone(migrations.read_state())
                self.assertEqual(backup.snapshot_database(database)["hashes"], before["hashes"])

    def test_v8_backup_rejects_future_table_and_current_v9_rejects_v8_history(self) -> None:
        prefix = institution.InstitutionMigration.v8_migrations(self)
        with patch.object(migrations, "ROOT", prefix):
            names = self.upgrade()
        database = names["dept-01"]
        v8 = backup.snapshot_database(database)
        self.assertEqual(recovery_schema.validate_schema(v8, "tenant"), recovery_schema.trusted_schema("tenant", 8))
        migrations.run_migrations()
        v9 = backup.snapshot_database(database)
        self.assertEqual(recovery_schema.validate_schema(v9, "tenant"), recovery_schema.trusted_schema("tenant", 9))
        for field in ("tables", "ddl", "columns"):
            v8[field]["search_index"] = v9[field]["search_index"]
        with self.assertRaises(ValueError):
            recovery_schema.validate_schema(v8, "tenant")
        v9["tables"]["schema_migrations"] = v8["tables"]["schema_migrations"]
        with self.assertRaises(ValueError):
            recovery_schema.validate_schema(v9, "tenant")

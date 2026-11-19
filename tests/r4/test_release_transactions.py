"""Exercise the release transaction on migrated, original 0.7 MySQL data."""

import json
import os
import unittest
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
from unittest.mock import patch

from backend import db, sources
from backend.api.v1.core import entity_action_in_transaction
from backend.core.errors import ApiError
from backend.kernel import elements, releases
from backend.security import store
from backend.security.context import Principal, use_context
from tests.r2 import test_build as construction


class ReleaseFixture:
    @classmethod
    def setUpClass(cls) -> None:
        construction.BuildMySQL.setUpClass.__func__(cls)

        def person(username: str) -> Principal:
            token = store.login(username, "SyntheticFixture9!", "127.0.0.1", "release-test")
            return store.authenticate_session(token, "release-test")[0]

        cls.admin = person("fixture.admin")
        cls.builder = person("fixture.builder")
        cls.reviewer = person("fixture.reviewer")
        for name, role in (("release.other", "admin"), ("release.viewer", "viewer")):
            ident = store.create_user(
                cls.admin,
                username=name,
                display_name=name,
                tenant_id="dept-01",
                role="modeler" if role == "viewer" else role,
                password="SyntheticFixture9!",
                must_change_password=False,
            )
            if role == "viewer":
                # Pre-provisioned read-only role, before its first authenticated session.
                with store.control_connection() as conn, conn.cursor() as cursor:
                    cursor.execute("UPDATE sec_users SET role=%s WHERE id=%s", (role, ident))
        cls.other = person("release.other")
        cls.viewer = person("release.viewer")

    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)

    def committed(self, person: Principal, fun: Callable, *args: object) -> dict:
        with use_context(person), closing(db.connect()) as conn, conn:
            return fun(conn, person, *args)

    def run_as(self, person: Principal, fun: Callable, *args: object, **kwargs: object) -> dict:
        with use_context(person):
            return fun(self.conn, person, *args, **kwargs)

    def create(self, **kwargs: object) -> dict:
        return self.run_as(self.admin, releases.create_draft, **kwargs)

    def submit(self, draft: dict) -> dict:
        return self.run_as(self.other, releases.submit, draft["id"])

    def publish(self, draft: dict) -> dict:
        self.submit(draft)
        return self.run_as(self.reviewer, releases.publish, draft["id"])

    def archive(self, ident: str) -> None:
        with use_context(self.admin):
            entity_action_in_transaction(self.conn, ident, {"action": "archive", "reason": "归档已停用的标准"})


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in release/MySQL integration")
class ReleaseTransactions(ReleaseFixture, unittest.TestCase):
    def setUp(self) -> None:
        self.conn = db.Connection(db._raw_connection(self.names["dept-01"]))
        self.addCleanup(self.conn.close)

    def test_first_release_enumerates_only_published_assets_and_exact_external_refs(self) -> None:
        draft = self.create()
        expected = {
            row["id"]
            for row in self.conn.execute(
                "SELECT id FROM entities WHERE kind IN ('data','rule','knowledge','skill','term',"
                "'object_type','link_type','metric','process') AND status='published'"
            )
        }
        members = [item for item in draft["items"] if item["item_role"] == "member"]
        refs = [item for item in draft["items"] if item["item_role"] == "ref"]
        self.assertEqual(len(expected), 8)
        self.assertEqual(
            (draft["suggested_version"], draft["diff"]["added"], draft["member_count"]),
            ("1.0.0", sorted(expected), len(expected)),
        )
        self.assertEqual({item["entity_id"] for item in members}, expected)
        self.assertEqual({item["entity_id"] for item in refs}, {"dept-01"})
        self.assertEqual(set(refs[0]["snapshot"]), {"id", "kind", "title", "version"})
        self.assertEqual({check["entity_id"] for check in draft["gates"]}, expected)
        self.assertTrue(any(not check["passed"] for check in draft["gates"]))
        self.assertTrue(all(not check["blocking"] for check in draft["gates"]))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ont_release_items").fetchone()[0], 0)

    def test_creator_and_different_submitter_must_both_use_another_reviewer(self) -> None:
        draft = self.create()
        self.submit(draft)
        for person in (self.admin, self.other):
            with self.subTest(person=person.username), self.assertRaises(ApiError) as caught:
                self.run_as(person, releases.publish, draft["id"])
            self.assertEqual(caught.exception.code, "SEPARATION_OF_DUTIES")
        published = self.run_as(self.reviewer, releases.publish, draft["id"])
        self.assertEqual(
            (published["created_by"], published["submitted_by"], published["reviewed_by"]),
            (self.admin.user_id, self.other.user_id, self.reviewer.user_id),
        )
        self.assertEqual((published["status"], published["id"]), ("published", published["current_release_id"]))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM ont_release_items WHERE release_id=?", (draft["id"],)).fetchone()[
                0
            ],
            draft["member_count"] + draft["ref_count"],
        )

    def test_publication_requires_candidate_and_worker_must_match_approved_version_and_hash(self) -> None:
        draft = self.create()
        with self.assertRaisesRegex(ApiError, "先提交"):
            self.run_as(self.reviewer, releases.publish, draft["id"])
        self.submit(draft)
        for args in ({"expected_version": "1.0.1"}, {"expected_manifest_hash": "a" * 64}):
            with self.subTest(args=args), self.assertRaises(ApiError) as caught:
                self.run_as(self.reviewer, releases.publish, draft["id"], **args)
            self.assertEqual(caught.exception.code, "VERSION_CONFLICT")
        receipt = self.run_as(
            self.reviewer,
            releases.publish,
            draft["id"],
            expected_version=draft["version"],
            expected_manifest_hash=draft["manifest_hash"],
        )
        self.assertEqual(receipt["manifest_hash"], draft["manifest_hash"])

    def test_no_change_after_operational_reapproval_reserves_no_new_version(self) -> None:
        previous = self.publish(self.create())
        row_id = "DEMO07-FIELD-PAID_YUAN"
        self.conn.execute(
            "UPDATE entities SET version=version+4,updated_at=?,payload=JSON_SET(payload,'$.r2_review',"
            "JSON_OBJECT('last_editor_id','ignored')) WHERE id=?",
            (db.now(), row_id),
        )
        result = self.create()
        self.assertTrue(result["no_changes"])
        self.assertEqual((result["id"], result["manifest_hash"]), (previous["id"], previous["manifest_hash"]))
        self.assertTrue(all(not values for values in result["diff"].values()))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ont_releases").fetchone()[0], 1)

    def test_first_freeze_only_grandfathers_unmodified_pre_upgrade_publications(self) -> None:
        sheet = self.conn.execute(
            "SELECT dataset_id,sheet_name,payload FROM dataset_sheets ORDER BY dataset_id,sheet_name LIMIT 1"
        ).fetchone()
        column = json.loads(sheet["payload"])["columns"][0]
        evidence = {
            "kind": "dataset",
            "dataset_id": sheet["dataset_id"],
            "sheet": sheet["sheet_name"],
            "column": column["name"] if isinstance(column, dict) else column,
        }
        with use_context(self.builder):
            added = elements.create_draft(
                self.conn,
                {
                    "kind": "term",
                    "title": "账龄核验范围",
                    "payload": {"schema": "term/1", "definition": "取自核对台账的范围", "source_refs": [evidence]},
                },
            )
            entity_action_in_transaction(self.conn, added["id"], {"action": "submit", "reason": "有已登记台账出处"})
        with use_context(self.reviewer):
            for act in ("approve", "publish"):
                entity_action_in_transaction(self.conn, added["id"], {"action": act, "reason": "分别核对定义及出处"})
        first = self.create()
        self.assertTrue(next(check["passed"] for check in first["gates"] if check["entity_id"] == added["id"]))
        with use_context(self.builder):
            conflict = elements.create_draft(
                self.conn,
                {
                    "kind": "term",
                    "title": added["title"],
                    "payload": {"schema": "term/1", "definition": "另一种未确认的定义"},
                },
            )
        preview = self.create()
        self.assertEqual(preview["id"], first["id"])
        self.assertEqual(preview["manifest_hash"], first["manifest_hash"])
        failed = [check for check in preview["gates"] if not check["passed"]]
        self.assertEqual([check["entity_id"] for check in failed if check["blocking"]], [added["id"]])
        self.assertTrue(any(not check["blocking"] for check in failed))
        with self.assertRaises(ApiError) as blocked:
            self.submit(preview)
        self.assertEqual(blocked.exception.code, "GATE_NOT_PASSED")
        for check in failed:
            entity = self.conn.execute("SELECT status FROM entities WHERE id=?", (check["entity_id"],)).fetchone()
            self.assertEqual(entity["status"], "published")
        self.archive(conflict["id"])
        self.assertEqual(self.publish(first)["manifest_hash"], first["manifest_hash"])

    def test_newly_changed_legacy_deficiency_blocks_but_unchanged_legacy_survives(self) -> None:
        self.publish(self.create())
        # Deliberately break a historical record; no write fabricates a passing gate.
        self.conn.execute("UPDATE entities SET summary='缺证据的修订' WHERE id='DEMO07-FIELD-PAID_YUAN'")
        draft = self.create()
        self.assertEqual(draft["suggested_version"], "1.0.1")
        blocking = {item["entity_id"] for item in draft["gates"] if item["blocking"]}
        self.assertEqual(blocking, {"DEMO07-FIELD-PAID_YUAN"})
        with self.assertRaises(ApiError) as caught:
            self.submit(draft)
        self.assertEqual(caught.exception.code, "GATE_NOT_PASSED")

    def test_withdrawal_preserves_rows_rolls_back_head_and_uses_historical_version_floor(self) -> None:
        first = self.publish(self.create())
        self.archive("DEMO07-FIELD-PAID_YUAN")
        second = self.publish(self.create())
        self.assertEqual(second["suggested_version"], "2.0.0")
        before = self.conn.execute(
            "SELECT entity_id,content_hash,snapshot FROM ont_release_items WHERE release_id=? ORDER BY entity_id",
            (second["id"],),
        ).fetchall()
        reverted = self.run_as(self.reviewer, releases.withdraw, second["id"])
        self.assertEqual((reverted["status"], reverted["current_release_id"]), ("withdrawn", first["id"]))
        self.assertEqual(self.run_as(self.reviewer, releases.withdraw, second["id"]), reverted)
        self.assertEqual(
            before,
            self.conn.execute(
                "SELECT entity_id,content_hash,snapshot FROM ont_release_items WHERE release_id=? ORDER BY entity_id",
                (second["id"],),
            ).fetchall(),
        )
        self.assertEqual(self.create()["suggested_version"], "3.0.0")
        with self.assertRaises(ApiError):
            self.run_as(self.reviewer, releases.publish, second["id"])

    def test_same_institution_published_only_reader_sees_withdrawn_and_no_drafts(self) -> None:
        draft = self.create()
        self.submit(draft)
        self.assertEqual(self.run_as(self.viewer, releases.list_releases), [])
        with self.assertRaises(ApiError) as caught:
            self.run_as(self.viewer, releases.get_release, draft["id"])
        self.assertEqual(caught.exception.code, "NOT_FOUND")
        self.run_as(self.reviewer, releases.publish, draft["id"])
        self.run_as(self.reviewer, releases.withdraw, draft["id"])
        visible = self.run_as(self.viewer, releases.get_release, draft["id"])
        self.assertEqual(visible["status"], "withdrawn")
        self.assertTrue(visible["items"])
        self.assertEqual(visible["gates"], [])
        self.assertEqual([row["id"] for row in self.run_as(self.viewer, releases.list_releases)], [draft["id"]])

    def test_owner_can_submit_but_cannot_withdraw_unless_review_permission_is_present(self) -> None:
        self.conn.execute("UPDATE ont_modules SET owner_user_id=? WHERE id='MOD-DEFAULT'", (self.builder.user_id,))
        draft = self.run_as(self.builder, releases.create_draft)
        self.run_as(self.builder, releases.submit, draft["id"])
        self.run_as(self.reviewer, releases.publish, draft["id"])
        with self.assertRaises(ApiError) as caught:
            self.run_as(self.builder, releases.withdraw, draft["id"])
        self.assertEqual(caught.exception.code, "PERMISSION_DENIED")

    def test_explicit_upward_choice_is_preserved_and_cannot_be_lowered_on_submit(self) -> None:
        with self.assertRaises(ApiError):
            self.create(version="0.9.9")
        draft = self.create(version="1.9.2")
        self.assertEqual((draft["suggested_version"], draft["version"]), ("1.0.0", "1.9.2"))
        with self.assertRaises(ApiError):
            self.run_as(self.admin, releases.submit, draft["id"], version="1.3.0")
        receipt = self.run_as(self.admin, releases.submit, draft["id"], version="2.0.0")
        self.assertEqual(receipt["version"], "2.0.0")
        self.assertEqual(self.run_as(self.reviewer, releases.publish, draft["id"])["version"], "2.0.0")


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in source binding/release integration")
class ReleaseBindings(ReleaseFixture, unittest.TestCase):
    def test_source_review_freezes_only_published_binding_and_honors_current_grants(self) -> None:
        with use_context(self.admin), closing(db.connect()) as conn:
            mapping = conn.execute("SELECT id,source_id,version FROM source_mappings ORDER BY id LIMIT 1").fetchone()
            metadata = [
                json.loads(row["metadata"])
                for row in conn.execute(
                    "SELECT metadata FROM source_resources WHERE source_id=?", (mapping["source_id"],)
                )
            ]

        def action(person: Principal, name: str, version: int) -> dict:
            with use_context(person):
                return sources.mapping_action(
                    mapping["source_id"],
                    mapping["id"],
                    sources.MappingAction(action=name, expected_version=version, reason="核对已登记的来源结构"),
                )

        candidate = action(self.admin, "submit", mapping["version"])
        # Only the external discovery response is synthetic. The real submitter,
        # independent source reviewer, grants and frozen mapping are exercised.
        with patch.object(sources, "_remote_discover", return_value=metadata):
            published_mapping = action(self.other, "publish", candidate["version"])
        draft = self.committed(self.admin, releases.create_draft)
        self.committed(self.admin, releases.submit, draft["id"])
        reviewed = self.committed(self.reviewer, releases.publish, draft["id"])
        self.assertFalse(any(item["snapshot"].get("bindings") for item in reviewed["items"]))
        published = self.committed(self.admin, releases.get_release, draft["id"])
        bound = [item for item in published["items"] if item["snapshot"].get("bindings")]
        self.assertTrue(bound)
        for item in bound:
            self.assertEqual(
                {(b["id"], b["version"]) for b in item["snapshot"]["bindings"]},
                {(mapping["id"], published_mapping["version"])},
            )
        action(self.admin, "revise", published_mapping["version"])
        frozen = self.committed(self.admin, releases.get_release, draft["id"])
        self.assertEqual(frozen["items"], published["items"])
        next_draft = self.committed(self.admin, releases.create_draft)
        self.assertFalse(any(item["snapshot"].get("bindings") for item in next_draft["items"]))
        self.assertNotEqual(next_draft["manifest_hash"], published["manifest_hash"])


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in concurrent release/MySQL integration")
class ReleaseRaces(ReleaseFixture, unittest.TestCase):
    def archive_asset(self, ident: str) -> None:
        with use_context(self.admin), closing(db.connect()) as conn, conn:
            entity_action_in_transaction(conn, ident, {"action": "archive", "reason": "历史标准停止使用"})

    def race(self, pairs: list[tuple]) -> list[str]:
        barrier = Barrier(len(pairs))

        def invoke(pair: tuple) -> str:
            person, fun, args = pair
            barrier.wait(timeout=10)
            try:
                return self.committed(person, fun, *args)["status"]
            except ApiError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=len(pairs)) as executor:
            return list(executor.map(invoke, pairs))

    def test_concurrent_reviewers_publish_once_and_adjacent_withdrawals_skip_each_other(self) -> None:
        first = self.committed(self.admin, releases.create_draft)
        self.committed(self.admin, releases.submit, first["id"])
        outcomes = self.race([(reviewer, releases.publish, (first["id"],)) for reviewer in (self.reviewer, self.other)])
        self.assertCountEqual(outcomes, ["published", "PRECONDITION_FAILED"])
        all_ids = [first["id"]]
        for ident in ("DEMO07-FIELD-PAID_YUAN", "DEMO07-FIELD-PAYMENT_ID"):
            self.archive_asset(ident)
            draft = self.committed(self.admin, releases.create_draft)
            self.committed(self.admin, releases.submit, draft["id"])
            self.committed(self.reviewer, releases.publish, draft["id"])
            all_ids.append(draft["id"])
        outcomes = self.race(
            [(self.other, releases.withdraw, (all_ids[1],)), (self.reviewer, releases.withdraw, (all_ids[2],))]
        )
        self.assertEqual(outcomes, ["withdrawn", "withdrawn"])
        with use_context(self.admin), closing(db.connect()) as conn:
            self.assertEqual(
                conn.execute("SELECT current_release_id FROM ont_modules WHERE id='MOD-DEFAULT'").fetchone()[0],
                all_ids[0],
            )
            for index, ident in enumerate(all_ids):
                detail = releases.get_release(conn, self.admin, ident)
                self.assertEqual(detail["status"], "published" if index == 0 else "withdrawn")
                self.assertEqual(len(detail["items"]), detail["member_count"] + detail["ref_count"])
            self.assertEqual(conn.execute("SELECT COUNT(DISTINCT release_id) FROM ont_release_items").fetchone()[0], 3)

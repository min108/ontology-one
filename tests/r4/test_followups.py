"""R4-A A7: inherited report and task startup trust-boundary regressions."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from backend import jobs
from backend.api.v1.core import _create_entity
from backend.core.errors import ApiError
from backend.domains.artifacts import service
from backend.security import store
from backend.security.context import use_context
from tests.r2 import test_build as construction


class Followups(unittest.TestCase):
    def test_server_rejects_missing_or_whitespace_name_before_writing(self) -> None:
        with patch("backend.api.v1.core.connect") as connect:
            for body in ({}, {"title": ""}, {"title": " \t\n"}, {"title": "\u3000"}):
                with self.subTest(body=body):
                    with self.assertRaises(HTTPException) as error:
                        _create_entity({"kind": "rule", **body})
                    self.assertEqual(error.exception.status_code, 400)
                    self.assertEqual(error.exception.detail, "请填写名称。")
            connect.assert_not_called()

    def test_recipe_startup_does_not_report_failure_before_tenant_commit(self) -> None:
        person = SimpleNamespace(tenant_id="tenant", user_id="user", session_id="login", auth_version=1)
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        tenant = MagicMock()
        tenant.execute.return_value.fetchone.return_value = None
        for state in ("queued", "running", "retry", "completed", "failed", "cancelled"):
            cursor.fetchall.return_value = [
                {
                    "job_id": "recipe",
                    "kind": "recipe_run",
                    "entity_id": "material",
                    "status": state,
                    "created_at": None,
                    "updated_at": None,
                }
            ]
            with (
                self.subTest(state=state),
                patch.object(jobs, "assert_principal_current", return_value=person),
                patch.object(jobs, "ensure_queue"),
                patch.object(jobs, "control_connection", return_value=connection),
                patch("backend.db.connect", return_value=tenant),
                patch("backend.domains.build.status", side_effect=ApiError("NOT_FOUND")),
            ):
                item = jobs.harness_queue_summaries(person)[0]
            if state in {"queued", "running", "retry"}:
                self.assertEqual(item["status"], "queued")
                self.assertEqual(item["stage"], "queued")
                self.assertEqual(item["href"], "/build/intake?run=recipe")
                self.assertIn("正在等待整理", item["message"])
            else:
                self.assertEqual(item["status"], "failed")
                self.assertEqual(item["href"], "/materials/material")
                self.assertIn("原材料仍保留", item["message"])

    def test_report_marks_original_answer_from_another_configuration(self) -> None:
        person = SimpleNamespace(user_id="owner")
        for changed in (False, True):
            session = {
                "ledgers": [],
                "runs": {
                    "original": {
                        "status": "completed",
                        "answer": "付款制度要求30日内申请，另有2项要求。",
                        "_report_configuration_signature": b"old" if changed else b"current",
                    },
                    "revised": {
                        "status": "completed",
                        "answer": "付款制度要求30日内申请。",
                        "revision_of": "original",
                        "_report_configuration_signature": b"current",
                    },
                },
            }
            with (
                self.subTest(changed=changed),
                patch.object(service, "require_context", return_value=person),
                patch.object(service, "assert_principal_current", return_value=person),
                patch("backend.agent_runtime.SESSIONS.get", return_value=session),
                patch("backend.agent_runtime._configuration_signature", return_value=b"current"),
                patch.object(service.db, "connect", return_value=MagicMock()),
                patch.object(
                    service,
                    "_citations",
                    return_value={
                        "c1": {
                            "title": "付款制度",
                            "entity_id": "rule",
                            "quote": "应在30日内申请。",
                            "version": 1,
                        }
                    },
                ),
                patch.object(service, "_save_version") as save,
                patch.object(service, "get", return_value={"id": "saved"}),
            ):
                self.assertEqual(service.create("session", "revised"), {"id": "saved"})
                revision = save.call_args.args[3]["revision"]
                self.assertEqual(revision["before"], session["runs"]["original"]["answer"])
                self.assertEqual(revision.get("before_configuration_changed", False), changed)
                self.assertNotIn("signature", str(revision))


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in real recipe startup/MySQL contract")
class StartupMySQL(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login

    def test_tray_reads_queue_during_recipe_start_before_tenant_commit(self) -> None:
        client = self.login("fixture.builder")
        person = store.authenticate_session(client.cookies.get("ontology_session"), "r4-startup")[0]
        uploaded = client.post(
            "/api/materials/upload", files={"file": ("startup.txt", "付款前核对合同。".encode(), "text/plain")}
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        captured = []
        enqueue = jobs.enqueue

        def observe(*args: object, **kwargs: object) -> dict:
            result = enqueue(*args, **kwargs)
            captured.extend(jobs.harness_queue_summaries(person))
            return result

        with patch.object(jobs, "enqueue", side_effect=observe):
            started = client.post("/api/v2/recipes", json={"material_id": uploaded.json()["id"]})
        self.assertEqual(started.status_code, 200, started.text)
        ident = started.json()["data"]["id"]
        item = next(item for item in captured if item["id"] == ident)
        self.assertEqual((item["status"], item["stage"]), ("queued", "queued"))
        self.assertEqual(item["href"], "/build/intake?run=" + ident)
        self.assertIn("正在等待整理", item["message"])
        with use_context(person):
            committed = next(item for item in jobs.harness_queue_summaries(person) if item["id"] == ident)
        self.assertEqual(committed["status"], "queued")
        self.assertEqual(committed["href"], item["href"])

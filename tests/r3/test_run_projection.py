"""Authenticated run projection preserves removal of source-backed answers."""

import asyncio
import os
import secrets
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend import db
from backend.agent_runtime import SESSIONS
from backend.agent_service import app
from backend.security import store
from backend.security.context import use_context
from tests.r2 import test_build as construction


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in real session/MySQL HTTP contract")
class RunProjectionMySQL(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login

    def test_http_omits_answers_removed_by_scope_narrowing_or_scrubbing(self) -> None:
        builder = self.login("fixture.builder")
        principal = store.authenticate_session(builder.cookies.get("ontology_session"), "r3-projection")[0]
        client = TestClient(app)
        self.addCleanup(client.close)
        client.cookies.update(builder.cookies)
        client.headers.update(builder.headers)
        with use_context(principal):
            session = SESSIONS.create(principal, "synthetic projection")
        self.addCleanup(lambda: SESSIONS.sessions.pop(session["id"], None))
        ident = secrets.token_hex(16)
        original_scope = {"skill_ids": [], "mapping_ids": ["synthetic-source"], "entity_ids": []}
        session["last_scope"] = original_scope
        session["runs"][ident] = {
            "id": ident,
            "status": "completed",
            "created_at": db.now(),
            "partial": False,
            "tools": [],
            "provenance": [{"source_id": "synthetic-source", "title": "Sensitive fixture source"}],
            "answer": "Source-backed fixture answer",
            "partial_answer": "Source-backed partial fixture",
            "_draft_answer": "Private fixture draft",
        }
        path = f"/api/agent/sessions/{session['id']}/runs/{ident}"
        original = client.get(path)
        self.assertEqual(original.status_code, 200, original.text)
        self.assertEqual(original.json()["answer"], "Source-backed fixture answer")
        self.assertTrue(original.json()["provenance"])
        self.assertNotIn("revision_of", original.json())
        session["runs"][ident]["answer"] = ""
        explicit_empty = client.get(path)
        self.assertEqual(explicit_empty.status_code, 200, explicit_empty.text)
        self.assertIn("answer", explicit_empty.json())
        self.assertEqual(explicit_empty.json()["answer"], "")
        session["runs"][ident]["answer"] = "Source-backed fixture answer"

        with use_context(principal), patch.object(SESSIONS, "_execute", new=AsyncMock()):
            asyncio.run(SESSIONS.start(principal, session, "narrow scope", [], [], []))
        narrowed = client.get(path)
        self.assertEqual(narrowed.status_code, 200, narrowed.text)
        self.assertNotIn("answer", narrowed.json())
        self.assertNotIn("partial_answer", narrowed.json())
        self.assertEqual(narrowed.json()["provenance"], [])
        self.assertNotIn("Source-backed", narrowed.text)
        self.assertNotIn("Private fixture", narrowed.text)

        SESSIONS.scrub(session)
        scrubbed = client.get(path)
        self.assertEqual(scrubbed.status_code, 200, scrubbed.text)
        self.assertNotIn("answer", scrubbed.json())
        self.assertNotIn("partial_answer", scrubbed.json())
        self.assertEqual(scrubbed.json()["provenance"], [])
        self.assertNotIn("Source-backed", scrubbed.text)

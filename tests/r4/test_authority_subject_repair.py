"""Exact authority subject replacement, without weakening tenant or workflow rules."""

import os
import secrets
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import authority
from tests.r2 import test_build as construction


class AuthoritySubjectService(unittest.TestCase):
    def test_replacement_uses_selected_business_org_and_retains_person_validation(self) -> None:
        old = {
            "title": "Synthetic authority",
            "summary": "",
            "domain": "",
            "org_id": "old-org",
            "payload": {"authority_spec": authority.Spec(type="position", org_ids=["old-org"]).model_dump()},
        }
        entities = {
            "new-org": {"kind": "organization", "status": "draft", "payload": {}},
            "person": {"kind": "person", "status": "draft", "payload": {}, "org_id": "old-org"},
        }
        with (
            patch.object(authority, "get_entity", side_effect=lambda _c, ident, *_: entities[ident]),
            patch.object(authority, "_save_draft", return_value={}) as save,
        ):
            authority.repair_gate_fields(
                None,
                old,
                {"expected_version": 1, "reason": "Replace", "payload": {"applicable_subject_ids": ["new-org"]}},
            )
            body = save.call_args.args[2]
            self.assertEqual(body.org_id, "new-org")
            self.assertEqual(authority.validate_refs(None, body.spec.model_dump(), body.org_id)["org_ids"], ["new-org"])
            # A selected person's organization still has to be in the exact scope.
            invalid = body.spec.model_dump()
            invalid["person_ids"] = ["person"]
            with self.assertRaises(HTTPException) as failure:
                authority.validate_refs(None, invalid, body.org_id)
            self.assertEqual(failure.exception.status_code, 422)
            self.assertEqual(old["org_id"], "old-org")
            self.assertEqual(old["payload"]["authority_spec"]["org_ids"], ["old-org"])


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in authority HTTP/MySQL regression")
class AuthoritySubjectsHTTP(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke

    def request(self, client: TestClient, path: str, body: dict) -> dict:
        response = client.post(path, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_exact_selection_and_rejected_changes_preserve_storage(self) -> None:
        client = self.login("fixture.builder")
        person = self.request(
            client, "/api/authority/people", {"name": "中文经办" + secrets.token_hex(4), "org_id": "dept-01"}
        )["entity"]
        created = self.request(
            client,
            "/api/authority/assets",
            {
                "title": "主体核对" + secrets.token_hex(4),
                "spec": {"type": "position", "org_ids": ["dept-01"], "person_ids": [person["id"]]},
            },
        )["entity"]
        ident = created["id"]
        before = self.invoke(client, "element.get", {"entity_id": ident})
        self.assertEqual(before["entity"]["owner_tenant_id"], "dept-01")
        self.assertEqual(before["entity"]["owner_org_id"], "dept-01")
        # Real SQL with a small page exercises the same bound without 500 fixture writes.
        for _ in range(3):
            self.request(client, "/api/authority/people", {"name": "AAA" + secrets.token_hex(4), "org_id": "dept-01"})
        with patch("backend.capabilities.elements.APPLICABILITY_OPTION_LIMIT", 2):
            options = self.invoke(client, "element.get", {"entity_id": ident})["applicability_options"]
        self.assertEqual({item["id"] for item in options}, {"dept-01", person["id"]})
        self.assertEqual({item["title"] for item in options}, {"办公厅", person["title"]})
        for selected in ([], [person["id"]], ["sub-01"]):
            with self.subTest(selected=selected):
                rejected = client.post(
                    "/api/v2/capabilities/element.update_draft:invoke",
                    json={
                        "entity_id": ident,
                        "version": created["version"],
                        "reason": "核实主体",
                        "payload": {"applicable_subject_ids": selected},
                    },
                )
                self.assertEqual(rejected.status_code, 422, rejected.text)
                after = self.invoke(client, "element.get", {"entity_id": ident})
                for key in ("version", "payload", "org_id", "relations", "owner_tenant_id", "owner_org_id"):
                    self.assertEqual(after["entity"].get(key), before["entity"].get(key), key)
                self.assertEqual(after["versions"], before["versions"])
        saved = self.invoke(
            client,
            "element.update_draft",
            {
                "entity_id": ident,
                "version": created["version"],
                "reason": "保留已核对主体",
                "payload": {"applicable_subject_ids": ["dept-01", person["id"]]},
            },
        )["entity"]
        self.assertEqual(saved["payload"]["authority_spec"]["org_ids"], ["dept-01"])
        self.assertEqual(saved["payload"]["authority_spec"]["person_ids"], [person["id"]])
        self.assertEqual(saved["org_id"], "dept-01")
        self.assertEqual(saved["owner_tenant_id"], "dept-01")
        self.assertEqual(saved["owner_org_id"], "dept-01")
        self.assertEqual(saved["version"], created["version"] + 1)

    def test_node_subject_cannot_disappear_via_simple_repair(self) -> None:
        client = self.login("fixture.builder")
        person = self.request(
            client, "/api/authority/people", {"name": "节点经办" + secrets.token_hex(4), "org_id": "dept-01"}
        )["entity"]
        created = self.request(
            client,
            "/api/authority/assets",
            {
                "title": "节点核对" + secrets.token_hex(4),
                "spec": {
                    "type": "workflow",
                    "org_ids": ["dept-01"],
                    "nodes": [
                        {"id": "step", "org_id": "dept-01", "person_id": person["id"]},
                    ],
                },
            },
        )["entity"]
        rejected = client.post(
            "/api/v2/capabilities/element.update_draft:invoke",
            json={
                "entity_id": created["id"],
                "version": created["version"],
                "reason": "删去节点经办",
                "payload": {"applicable_subject_ids": ["dept-01"]},
            },
        )
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertIn("流程节点", rejected.text)
        after = self.invoke(client, "element.get", {"entity_id": created["id"]})["entity"]
        self.assertEqual(after["version"], created["version"])
        self.assertEqual(after["payload"]["authority_spec"], created["payload"]["authority_spec"])

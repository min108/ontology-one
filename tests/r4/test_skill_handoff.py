"""Refresh a real adopted skill before and after specialist package creation."""

import os
import secrets
import unittest
from urllib.parse import quote

from tests.r2 import test_build as construction


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL handoff workflow")
class SkillHandoffHTTP(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke
    proposal = construction.BuildMySQL.proposal

    def test_refresh_changes_legacy_conversion_into_existing_managed_detail(self) -> None:
        builder = self.login("fixture.builder")
        proposal = self.proposal(builder)
        item = proposal["items"][0]
        self.invoke(
            builder,
            "changeset.decide_items",
            {
                "changeset_id": proposal["id"],
                "item_ids": [item["id"]],
                "decision": "edited",
                "edit": {
                    "title": item["after_data"]["title"],
                    "content": item["after_data"]["content"],
                    "kind": "skill",
                },
            },
        )
        self.invoke(builder, "changeset.apply", {"changeset_id": proposal["id"]})

        def refresh() -> dict:
            return next(
                row
                for row in self.invoke(builder, "changeset.get", {"changeset_id": proposal["id"]})["items"]
                if row["id"] == item["id"]
            )

        before = refresh()
        ident = before["applied_entity_id"]
        self.assertEqual(before["handoff"]["href"], "/skill-development?legacy=" + quote(ident))
        self.assertEqual(builder.get("/api/skills/" + ident).status_code, 404)
        body = {
            "title": item["after_data"]["title"],
            "package_name": "handoff-" + secrets.token_hex(5),
            "legacy_entity_id": ident,
        }
        created = builder.post("/api/skills", json=body)
        self.assertEqual(created.status_code, 200, created.text)
        self.assertTrue(created.json()["entity"]["payload"]["managed_skill"])
        after = refresh()
        self.assertEqual(after["applied_entity_id"], ident)
        self.assertEqual(after["handoff"]["href"], "/skill-development/" + quote(ident))
        self.assertNotEqual(after["handoff"]["href"], before["handoff"]["href"])
        opened = builder.get("/api/skills/" + ident)
        self.assertEqual(opened.status_code, 200, opened.text)
        self.assertEqual(opened.json()["entity"]["id"], ident)
        # The original conversion guard stays intact; refresh avoids routing users back into it.
        self.assertEqual(builder.post("/api/skills", json=body).status_code, 409)
        other = self.login("r2.other")
        self.assertEqual(other.get("/api/skills/" + ident).status_code, 404)


if __name__ == "__main__":
    unittest.main()

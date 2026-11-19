"""A.2 verifies original spreadsheet cells and server-side adoption decisions."""

import os
import unittest
from contextlib import closing

from openpyxl import Workbook

from backend import asset_import, db
from backend.security import store
from backend.security.context import use_context
from tests.r2 import test_build as construction
from tests.r4a1 import test_build_source_review as source_review


class OriginalWorkbook(unittest.TestCase):
    def test_measurement_columns_round_trip_and_older_sheets_do_not_erase_saved_units(self) -> None:
        for headers in (("单位", "粒度"), ("计量单位", "统计粒度"), ()):
            with self.subTest(headers=headers):
                book = Workbook()
                first = ["数据项编码", "二级分类", "三级分类", "数据项名称", "业务含义"]
                book.active.append([*first, *headers])
                book.active.append(
                    [
                        "D.05.01.301",
                        "结算",
                        "复核",
                        "处理份数",
                        "复核完成的资料份数",
                        *("份", "每笔申请")[: len(headers)],
                    ]
                )
                rows, _ = asset_import.sheet_rows(book, "data")
                asset = asset_import.build_asset("data", rows[0], "mat", "原始工作簿", "chunk", "batch")
                p = asset["payload"]
                if headers:
                    self.assertEqual((p["unit"], p["grain"]), ("份", "每笔申请"))
                    self.assertIn("每笔申请", p["content"])
                else:
                    self.assertNotIn("unit", p)
                    self.assertNotIn("grain", p)
                book.close()


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in real intake MySQL contracts")
class ChangesetIntake(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke
    synthetic_proposal = source_review.SourceReviewMySQL.synthetic_proposal

    def test_only_ready_checks_gates_even_for_a_server_scored_sure_candidate(self) -> None:
        client = self.login("fixture.builder")
        for fields, eligible in (({}, False), ({"unit": "份", "grain": "每份申请"}, True)):
            with self.subTest(eligible=eligible):
                proposal = self.synthetic_proposal(client, "data", "经办人员按每份申请登记份数。", fields)
                item = proposal["items"][0]
                person = store.authenticate_session(client.cookies.get("ontology_session"), "intake-contract")[0]
                with use_context(person), closing(db.connect()) as conn:
                    tier = conn.execute("SELECT tier FROM changeset_items WHERE id=?", (item["id"],)).fetchone()["tier"]
                self.assertEqual(tier, "sure")
                self.assertEqual(item["gates"]["passed"], eligible)
                outcome = self.invoke(
                    client,
                    "changeset.decide_items",
                    {
                        "changeset_id": proposal["id"],
                        "item_ids": [item["id"]],
                        "decision": "accepted",
                        "only_ready": True,
                    },
                )
                self.assertEqual(outcome["items"][0]["decision"], "accepted" if eligible else "pending")
                saved = self.invoke(client, "changeset.get", {"changeset_id": proposal["id"]})["items"][0]
                self.assertEqual(saved["decision"], outcome["items"][0]["decision"])

    def test_reclassified_rule_inherits_file_facts_and_anchors_after_reread(self) -> None:
        client = self.login("fixture.builder")
        text = (
            "第一条 本规定适用于所属单位的合同管理。\n第二条 按合同复核履约材料。\n第三条 本规定自2026年1月1日起施行。"
        )
        proposal = self.synthetic_proposal(client, "skill", text, {}, quote="按合同复核履约材料。")
        item = proposal["items"][0]
        # A skill edited earlier can have its own empty fields. Reclassification must
        # read the document facts again, including their original source anchors.
        edit = {"title": item["after_data"]["title"], "content": item["after_data"]["content"]}
        cleared = self.invoke(
            client,
            "changeset.decide_items",
            {
                "changeset_id": proposal["id"],
                "item_ids": [item["id"]],
                "decision": "edited",
                "edit": {**edit, "kind": "skill", "valid_from": "", "scope": ""},
            },
        )["items"][0]["after_data"]
        self.assertEqual((cleared["valid_from"], cleared["scope"]), ("", ""))
        self.invoke(
            client,
            "changeset.decide_items",
            {
                "changeset_id": proposal["id"],
                "item_ids": [item["id"]],
                "decision": "edited",
                "edit": {**edit, "kind": "rule"},
            },
        )
        current = self.invoke(client, "changeset.get", {"changeset_id": proposal["id"]})["items"][0]["after_data"]
        self.assertEqual(current["kind"], "rule")
        self.assertEqual(current["valid_from"], "2026-01-01")
        self.assertEqual(current["scope"], "所属单位的合同管理")
        for key in ("valid_from", "scope"):
            self.assertEqual(
                current["field_provenance"][key]["anchor_id"], proposal["document_facts"][key]["anchor_id"]
            )
            self.assertEqual(current["field_provenance"][key]["quote"], proposal["document_facts"][key]["quote"])

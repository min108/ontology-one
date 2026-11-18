"""D12 source-only facts: ordinary documents and hostile unrelated facts."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.domains import build

MATERIALS = Path(__file__).resolve().parents[2] / "docs/blueprint/rounds/r4a1-materials"


class BuildFacts(unittest.TestCase):
    def test_current_document_date_and_scope(self) -> None:
        for name, expected in [("plain", "2026-01-01"), ("repeal", ""), ("history", "")]:
            text = (MATERIALS / f"regulation-{name}.txt").read_text()
            facts = build.document_facts(text)
            self.assertEqual(facts["valid_from"]["value"], expected)
            self.assertIn("适用于", facts["scope"]["quote"])
            if expected:
                self.assertEqual(
                    text[facts["valid_from"]["start"] : facts["valid_from"]["end"]], facts["valid_from"]["quote"]
                )
            self.assertNotIn("2019-03-01", str(facts))
            self.assertNotIn("2018-05-01", str(facts))

    def test_only_current_file_operative_dates_are_eligible(self) -> None:
        positives = [
            "本办法自2026年1月1日起施行。",
            "本制度自2026-01-01起生效。",
            "本规定自2026年1月1日起实施。",
            "本细则自2026年1月1日起执行。",
            "第十条 本规程自2026年1月1日起施行。",
            "本办法于2026年1月1日施行。",
        ]
        negatives = [
            "原办法自2019年3月1日起施行，同时废止。",
            "公司于2018年5月1日实施全面预算管理以来。",
            "生效日期：2026-01-01",
            "本办法自发布之日起施行。原办法（自2019年3月1日起施行）同时废止。",
            "本办法自2026年2月30日起施行。",
            "旧制度记载“本办法自2019年3月1日起施行”。",
        ]
        for text in positives:
            with self.subTest(text=text):
                self.assertEqual(build.document_facts(text)["valid_from"]["value"], "2026-01-01")
        for text in negatives:
            with self.subTest(text=text):
                self.assertEqual(build.document_facts(text)["valid_from"]["value"], "")

    def enrich(self, candidate: dict, text: str) -> dict:
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"id": "org", "title": "本机构"}
        conn.execute.return_value.__iter__.return_value = iter([])
        with patch.object(build, "require_context", return_value=SimpleNamespace(tenant_id="org")):
            return build.enrich_candidate(conn, candidate, text)

    def test_unrelated_conditions_exceptions_and_model_dates_are_not_copied(self) -> None:
        quote = "应核对合同。"
        candidate = {
            "kind": "rule",
            "content": quote,
            "source_quotes": [{"quote": quote}],
            "conditions": ["异地出差时"],
            "exceptions": "紧急情况除外",
            "valid_from": "2019-03-01",
            "scope": "另一部门",
        }
        result = self.enrich(candidate, quote + "\n适用条件：异地出差时；例外：紧急情况除外。")
        self.assertEqual(result.get("conditions"), [])
        self.assertEqual(result.get("exceptions"), "")
        self.assertEqual(result.get("scope"), "")
        self.assertEqual(result.get("valid_from"), "")

    def test_own_quoted_fields_are_retained_with_source(self) -> None:
        quote = "适用范围：采购付款；适用条件：验收完成后；例外：紧急付款除外。"
        candidate = {
            "kind": "rule",
            "content": quote,
            "source_quotes": [{"quote": quote}],
            "conditions": ["验收完成后"],
            "exceptions": "紧急付款除外",
            "scope": "采购付款",
        }
        result = self.enrich(candidate, quote)
        for key in ["scope", "conditions", "exceptions"]:
            self.assertTrue(result[key])
            self.assertEqual(result["field_provenance"][key]["origin"], "source")
            self.assertIn(result["field_provenance"][key]["quote"], quote)

    def test_local_definition_uses_original_content_not_placeholder(self) -> None:
        candidate = {
            "kind": "rule",
            "content": "付款前应核对合同。",
            "summary": "按原文条款保留，请核对适用范围与例外。",
            "extraction_mode": "local",
            "source_quotes": [{"quote": "付款前应核对合同。"}],
        }
        result = self.enrich(candidate, candidate["content"])
        self.assertEqual(result["definition"], "付款前应核对合同")
        self.assertNotIn("请核对", result["definition"])

    def test_multi_article_quote_never_spreads_neighbour_facts(self) -> None:
        quote = "第一条 付款应核对合同。第二条 外地出差时办理报销，紧急情况除外。"
        candidate = {
            "kind": "rule",
            "content": "付款应核对合同。",
            "source_quotes": [{"quote": quote}],
            "conditions": ["外地出差时"],
            "exceptions": "紧急情况除外",
        }
        result = self.enrich(candidate, quote)
        self.assertEqual(result["conditions"], [])
        self.assertEqual(result["exceptions"], "")

    def test_natural_conditions_and_exceptions_are_preserved_without_model_fields(self) -> None:
        for quote, field in [
            ("应在验收通过后提出付款申请。", "conditions"),
            ("单笔金额超过100万元的，应经批准。", "conditions"),
            ("预付款不得超过30%；确需超过的，应报负责人批准。", "exceptions"),
            ("原则上统一办理，但紧急事项另报批准。", "exceptions"),
            ("一般不得垫付，紧急救援除外。", "exceptions"),
        ]:
            with self.subTest(quote=quote):
                result = self.enrich({"kind": "rule", "content": quote, "source_quotes": [{"quote": quote}]}, quote)
                self.assertTrue(result[field])
                self.assertEqual(result["field_provenance"][field]["origin"], "source")

    def test_submission_plan_respects_shared_denial_and_nonwaivable_policy(self) -> None:
        check = {
            "code": "QG3",
            "status": "fail",
            "can_waive": True,
            "reason": "缺少例外",
            "application_reason": "原文未写明",
        }
        self.assertEqual(build.submission_blockers({"checks": [check]}), [])
        self.assertEqual(build.submission_blockers({"checks": [{**check, "denial_reason": "请先补齐"}]}), ["请先补齐"])
        hard = {**check, "code": "QG2", "can_waive": False}
        self.assertEqual(build.submission_blockers({"checks": [hard]}, {"QG2": "强行提交"}), ["缺少例外"])

    def test_conditional_branches_are_not_absent_when_model_omits_fields(self) -> None:
        cases = [
            ("财务人员应及时审核；资料不齐全的，应通知补正。", "exceptions", "资料不齐全的，应通知补正"),
            ("申请符合要求时办理；证明缺失的，须先补交。", "exceptions", "证明缺失的，须先补交"),
            ("发现虚假票据的不予报销。", "conditions", "发现虚假票据的"),
            ("存在安全隐患的必须暂停作业。", "conditions", "存在安全隐患的"),
        ]
        for quote, field, expected in cases:
            with self.subTest(quote=quote):
                result = self.enrich({"kind": "rule", "content": quote, "source_quotes": [{"quote": quote}]}, quote)
                self.assertIn(expected, result[field])
                self.assertEqual(result["field_provenance"][field]["origin"], "source")

    def test_model_governance_fields_require_own_locatable_evidence(self) -> None:
        quote = "登记付款办理情况。"
        for kind in ("data", "knowledge"):
            with self.subTest(kind=kind):
                result = self.enrich(
                    {
                        "kind": kind,
                        "content": quote,
                        "source_quotes": [{"quote": quote}],
                        "definition": "每笔必须收十万元",
                        "unit": "美元",
                        "grain": "每日每部门",
                        "boundary": "禁止非金融机构使用",
                    },
                    quote,
                )
                self.assertEqual(result["definition"], "登记付款办理情况")
                self.assertEqual(result["unit"], "")
                self.assertEqual(result["grain"], "")
                self.assertEqual(result["boundary"], "")
                for field in ("unit", "grain", "boundary"):
                    self.assertNotIn(field, result["field_provenance"])
                self.assertIn(result["definition"], result["field_provenance"]["definition"]["quote"])

    def test_supported_measurement_and_boundary_have_individual_sources(self) -> None:
        quote = "台账按份登记，以元计量，仅供合同付款核对使用。"
        result = self.enrich(
            {
                "kind": "data",
                "content": quote,
                "source_quotes": [{"quote": quote}],
                "definition": "台账",
                "unit": "元",
                "grain": "按份登记",
                "boundary": "仅供合同付款核对使用",
            },
            quote,
        )
        for field in ("definition", "unit", "grain", "boundary"):
            self.assertTrue(result[field])
            self.assertEqual(result["field_provenance"][field]["origin"], "source")
            self.assertIn(result[field], result["field_provenance"][field]["quote"])
        missing = self.enrich({"kind": "data", "content": quote, "source_quotes": [{"quote": quote}]}, quote)
        self.assertEqual(missing["unit"], "")
        self.assertEqual(missing["grain"], "")

    def test_measurement_cannot_borrow_another_article_or_document(self) -> None:
        quote = "第一条 登记付款办理情况。第二条 外汇结算以美元计量，每日每部门登记。"
        result = self.enrich(
            {
                "kind": "data",
                "content": "登记付款办理情况。",
                "source_quotes": [{"quote": quote}],
                "unit": "美元",
                "grain": "每日每部门",
                "boundary": "外汇结算",
            },
            quote,
        )
        self.assertEqual((result["unit"], result["grain"], result["boundary"]), ("", "", ""))
        result = self.enrich(
            {
                "kind": "data",
                "content": "登记付款办理情况。",
                "source_quotes": [{"quote": "登记付款办理情况。"}],
                "unit": "美元",
            },
            quote,
        )
        self.assertEqual(result["unit"], "")

    def test_current_operative_date_survives_same_sentence_repeal(self) -> None:
        positives = [
            "本办法自2027年2月3日起施行，原办法同时废止。",
            "第八条 本规定于2027-02-03生效；原规定（自2019年3月1日起施行）同时废止。",
        ]
        for text in positives:
            fact = build.document_facts(text)["valid_from"]
            self.assertEqual(fact["value"], "2027-02-03")
            self.assertEqual(text[fact["start"] : fact["end"]], fact["quote"])
            self.assertNotIn("2019", fact["quote"])
        for text in [
            "本办法自发布之日起施行，原办法自2019年3月1日起施行。",
            "原办法自2019年3月1日起施行，本办法自发布之日起施行。",
            "原文件说明（本办法自2019年3月1日起施行），现予废止。",
        ]:
            self.assertEqual(build.document_facts(text)["valid_from"]["value"], "")

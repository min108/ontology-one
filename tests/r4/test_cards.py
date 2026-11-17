"""Business card contracts, including executable counterexamples to result checks."""

import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

from backend.kernel.cards import build_card, cache_card


class BusinessCards(unittest.TestCase):
    def card(self, kind: str, payload: object = None, **fields: object) -> dict:
        return build_card(
            {
                "id": "E-1",
                "title": "测试内容",
                "kind": kind,
                "version": 2,
                "status": "published",
                "payload": payload or {},
                **fields,
            }
        )

    def contains(self, actual: str | list, expected: str) -> None:
        self.assertIn(expected, actual)
        # The same result assertion must reject a card with the key fact removed.
        broken = (
            actual.replace(expected, "") if isinstance(actual, str) else [item for item in actual if item != expected]
        )
        with self.assertRaises(AssertionError):
            self.assertIn(expected, broken)

    def test_definition_precedes_summary(self) -> None:
        self.contains(self.card("rule", {"definition": "明确定义"}, summary="另一个摘要。")["one_liner"], "明确定义")

    def test_summary_first_sentence_only(self) -> None:
        self.assertEqual(self.card("material", summary="第一句。第二句。")["one_liner"], "第一句。")
        with self.assertRaises(AssertionError):
            self.assertEqual("第一句。第二句。", "第一句。")

    def test_unknown_kind_has_generic_card(self) -> None:
        self.contains(self.card("future", {"scenario": "日常使用"})["points"], "适用场景：日常使用")

    def test_rule_legacy_scope(self) -> None:
        self.contains(self.card("rule", {"scope": "全部合同"})["points"], "适用范围：全部合同")

    def test_rule_judgement_sentences(self) -> None:
        card = self.card(
            "rule",
            {
                "judgement": {
                    "condition": {"oxl": "amount > 20", "sentence": "金额大于二十"},
                    "requirement": {"sentence": "应附审批材料"},
                }
            },
        )
        self.contains(card["points"], "条件：金额大于二十")
        self.contains(card["points"], "要求：应附审批材料")
        self.assertNotIn("amount >", card["text"])

    def test_rule_exceptions_and_period(self) -> None:
        card = self.card(
            "rule",
            {"judgement": {"exceptions": [{"sentence": "框架协议除外"}]}},
            _meta={"valid_from": "2026-01-01", "valid_to": "2027-01-01"},
        )
        for expected in ("例外：框架协议除外", "生效日期：2026-01-01", "失效日期：2027-01-01"):
            self.contains(card["points"], expected)

    def test_object_identity_resolves_property_names(self) -> None:
        card = self.card(
            "object_type",
            {"identity": {"properties": ["number"]}, "properties": [{"key": "number", "name": "合同编号"}]},
        )
        self.contains(card["points"], "唯一标识：合同编号")

    def test_object_grain_units_boundary(self) -> None:
        card = self.card(
            "object_type",
            {"grain": "一份合同一条", "properties": [{"name": "金额", "unit": "元"}], "boundary": "不含订单"},
        )
        for expected in ("粒度：一份合同一条", "关键数据项：金额（元）", "边界：不含订单"):
            self.contains(card["points"], expected)

    def test_metric_formula_sentence(self) -> None:
        card = self.card("metric", {"formula": {"oxl": "a - b", "sentence": "金额减已付金额"}})
        self.contains(card["points"], "计算口径：金额减已付金额")
        self.assertNotIn("a - b", card["text"])

    def test_metric_aggregation_and_caveat(self) -> None:
        card = self.card(
            "metric",
            {
                "unit": "元",
                "grain": "每份合同",
                "aggregations": ["sum", "avg"],
                "default_aggregation": "sum",
                "caveats": "不代表违约",
            },
        )
        for expected in (
            "单位：元",
            "粒度：每份合同",
            "汇总方式：求和；平均",
            "默认汇总：求和",
            "使用边界：不代表违约",
        ):
            self.contains(card["points"], expected)

    def test_term_alias_and_disambiguation(self) -> None:
        card = self.card(
            "term", {"aliases": ["供方", "采购方"], "not_to_confuse": [{"term": "客户", "note": "提供方向相反"}]}
        )
        self.contains(card["points"], "别名：供方；采购方")
        self.contains(card["points"], "不要混淆：客户：提供方向相反")

    def test_data_unit_and_scope(self) -> None:
        card = self.card("data", {"unit": "元", "constraints": "含税", "standard_basis": "会计准则"})
        for expected in ("单位：元", "取值口径：含税", "口径依据：会计准则"):
            self.contains(card["points"], expected)

    def test_knowledge_conditions_and_boundary(self) -> None:
        card = self.card(
            "knowledge", {"conditions": "凭证齐备", "boundary": "仅作核查参考", "verification_path": "先核对合同"}
        )
        for expected in ("适用条件：凭证齐备", "使用边界：仅作核查参考", "核查路径：先核对合同"):
            self.contains(card["points"], expected)

    def test_skill_inputs_steps_outputs(self) -> None:
        card = self.card("skill", {"inputs": ["合同"], "steps": [{}, {}], "outputs": "核对表"})
        for expected in ("输入：合同", "步骤：2 个步骤", "输出：核对表"):
            self.contains(card["points"], expected)

    def test_process_trigger_result_and_counts(self) -> None:
        card = self.card(
            "process",
            {
                "trigger": "收到保证金",
                "end_result": "完成退还",
                "stages": [{}],
                "steps": [{"actors": [{"note": "项目经办人"}]}],
            },
        )
        for expected in (
            "起点：收到保证金",
            "终点：完成退还",
            "办理过程：1 个环节，1 个步骤",
            "主要参与方：项目经办人",
        ):
            self.contains(card["points"], expected)

    def test_process_scope_exclusion(self) -> None:
        card = self.card("process", {"scope": {"include": "施工项目", "exclude": "质量保证金"}})
        self.contains(card["points"], "适用范围：施工项目")
        self.contains(card["points"], "不包括：质量保证金")

    def test_only_confirmed_relations_render(self) -> None:
        rows = [
            {"id": str(n), "type": "references", "source_title": "合同", "target_title": state, "status": state}
            for n, state in enumerate(("confirmed", "candidate", "rejected"))
        ]
        card = self.card("rule", relations={"outgoing": rows})
        self.contains(card["relations"], "合同 业务引用 confirmed")
        self.assertEqual(len(card["relations"]), 1)
        with self.assertRaises(AssertionError):
            self.assertEqual(len(card["relations"] + ["candidate leak"]), 1)

    def test_relation_family_limits_three(self) -> None:
        rows = [{"id": str(n), "type": "references", "target_title": str(n), "status": "confirmed"} for n in range(5)]
        card = self.card("rule", relations={"outgoing": rows})
        self.contains(card["relations"][-1], "2")
        self.assertEqual(len(card["relations"]), 4)
        with self.assertRaises(AssertionError):
            self.assertEqual(len(rows), 4)

    def test_process_method_context_preserves_legacy_reading(self) -> None:
        row = {
            "id": "r",
            "type": "uses_method",
            "source_title": "办理过程",
            "target_title": "方法",
            "status": "confirmed",
        }
        self.contains(self.card("process", relations={"outgoing": [row]})["relations"], "办理过程 运用 方法")
        self.contains(self.card("matter", relations={"outgoing": [row]})["relations"], "办理过程 采用核查方法 方法")

    def test_governing_rules_must_be_published(self) -> None:
        row = {
            "id": "r",
            "type": "governs",
            "source_kind": "rule",
            "source_status": "published",
            "source_title": "审批规定",
            "status": "confirmed",
            "source_payload": {"judgement": {"requirement": {"sentence": "应附审批材料"}}},
        }
        card = self.card("object_type", relations={"incoming": [row]})
        self.contains(card["rules"], "审批规定：应附审批材料")
        row["source_status"] = "draft"
        self.assertEqual(self.card("object_type", relations={"incoming": [row]})["rules"], [])

    def test_anchor_source_title_version_locator(self) -> None:
        card = self.card(
            "rule", {"source_refs": [{"material_title": "采购办法", "source_version": 2, "locator": "原文第 12-30 字"}]}
        )
        self.contains(card["sources"], "《采购办法》第 2 版 · 原文第 12-30 字")

    def test_dataset_source_sheet_column(self) -> None:
        card = self.card(
            "data",
            {"source_refs": [{"kind": "dataset", "dataset_title": "合同台账", "sheet": "付款", "column": "金额"}]},
        )
        self.contains(card["sources"], "合同台账 · 付款 · 金额")

    def test_other_source_shapes(self) -> None:
        card = self.card(
            "knowledge",
            {
                "source_refs": [
                    {"kind": "external", "title": "上级规定", "note": "尚未上传"},
                    {"kind": "statement", "quote": "原话"},
                    {"kind": "element", "title": "规则", "version": 3},
                ]
            },
        )
        for expected in ("上级规定 · 尚未上传", "陈述记录：原话", "规则第 3 版"):
            self.contains(card["sources"], expected)

    def test_text_is_bounded(self) -> None:
        text = self.card("rule", {"definition": "长" * 10000, "scope": "多" * 10000})["text"]
        self.assertLessEqual(len(text), 1200)
        with self.assertRaises(AssertionError):
            self.assertLessEqual(len(text + "超限"), 1200)

    def test_deterministic_without_mutating_input(self) -> None:
        entity = {"kind": "rule", "payload": {"conditions": ["验收合格"]}}
        before = copy.deepcopy(entity)
        self.assertEqual(build_card(entity), build_card(copy.deepcopy(entity)))
        self.assertEqual(entity, before)
        with self.assertRaises(AssertionError):
            self.assertEqual({**entity, "payload": {}}, before)

    def test_cache_writes_exact_pure_text(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE entity_meta(entity_id TEXT PRIMARY KEY,card_text TEXT)")
        conn.execute("INSERT INTO entity_meta(entity_id) VALUES ('E-1')")
        entity = {"id": "E-1", "kind": "data", "title": "合同金额", "payload": {"unit": "元"}, "relations": {}}
        card = cache_card(conn, entity)
        actual = conn.execute("SELECT card_text FROM entity_meta WHERE entity_id='E-1'").fetchone()[0]
        self.assertEqual(actual, card["text"])
        with self.assertRaises(AssertionError):
            self.assertEqual("stale", card["text"])

    def test_legacy_payload_json_and_null(self) -> None:
        self.contains(self.card("data", json.dumps({"unit": "元"}))["points"], "单位：元")
        self.assertTrue(build_card({"kind": "rule", "payload": None})["one_liner"])

    def test_header_renders_business_status_and_context(self) -> None:
        card = self.card("rule", _meta={"module_name": "采购公共部分", "org_name": "装备公司"})
        self.contains(card["header"]["status"], "已发布")
        self.contains(card["text"], "采购公共部分")
        self.contains(card["text"], "装备公司")

    def test_header_uses_registry(self) -> None:
        import sys
        from types import SimpleNamespace

        module = SimpleNamespace(get_kind=lambda kind: SimpleNamespace(name="登记名称"))
        with patch.dict(sys.modules, {"backend.kernel.kinds": module}):
            self.contains(self.card("object_type")["header"]["kind"], "登记名称")

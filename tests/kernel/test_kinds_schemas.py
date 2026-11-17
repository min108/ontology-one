"""Positive and adversarial contracts for all seven additive kinds."""

import unittest
from copy import deepcopy

from backend.kernel.kinds import KINDS, get_kind, list_kinds
from backend.kernel.schemas import validate_payload


class KindContracts(unittest.TestCase):
    def test_registry_contains_old_and_seven_new_kinds(self) -> None:
        self.assertEqual(len(KINDS), 23)
        self.assertEqual(
            {k for k, v in KINDS.items() if v.payload_schema},
            {
                "position",
                "party",
                "term",
                "object_type",
                "link_type",
                "metric",
                "process",
            },
        )
        self.assertEqual(get_kind("algorithm"), get_kind("skill"))
        self.assertEqual(get_kind("process").id_prefix, "PRC-")
        self.assertEqual(get_kind("process").pillar, "asset")
        with self.assertRaises(TypeError):
            KINDS["invented"] = get_kind("rule")
        with self.assertRaises(ValueError):
            get_kind("invented")

    def test_registry_returns_detached_metadata(self) -> None:
        values = list_kinds()
        values[0]["name"] = "changed"
        self.assertNotEqual(get_kind(values[0]["kind"]).name, "changed")

    def test_new_optional_fields_allow_minimal_drafts(self) -> None:
        for kind in ("position", "party", "term", "object_type", "link_type", "metric", "process"):
            with self.subTest(kind=kind):
                validate_payload(kind, {"schema": kind + "/1"})
                with self.assertRaises(ValueError):
                    validate_payload(kind, {})
                with self.assertRaises(ValueError):
                    validate_payload(kind, {"schema": kind + "/2"})

    def test_old_payloads_remain_compatible(self) -> None:
        payload = {"原文": "旧条款", "未知历史字段": [1, 2]}
        original = deepcopy(payload)
        validate_payload("rule", payload)
        self.assertEqual(payload, original)
        with self.assertRaises(ValueError):
            validate_payload("rule", [])

    def test_object_valid_properties_and_identity(self) -> None:
        payload = {
            "schema": "object_type/1",
            "properties": [{"key": "amount", "name": "金额", "datatype": "decimal", "note": "合同金额"}],
            "identity": {"properties": ["amount"]},
            "title_property": "amount",
        }
        validate_payload("object_type", payload)
        for update in (
            {"title_property": "missing"},
            {"properties": payload["properties"] * 2},
            {"properties": [{"key": "amount", "name": "金额", "datatype": "decimal"}]},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_payload("object_type", {**payload, **update})

    def test_link_cardinality_and_join(self) -> None:
        validate_payload(
            "link_type",
            {
                "schema": "link_type/1",
                "cardinality": "one_to_many",
                "join": {"source_property": "no", "target_property": "no"},
            },
        )
        for update in ({"cardinality": "unlimited"}, {"join": {"source_property": "no"}}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_payload("link_type", {"schema": "link_type/1", **update})

    def test_metric_formula_and_aggregation(self) -> None:
        validate_payload(
            "metric",
            {
                "schema": "metric/1",
                "formula": {"oxl": "amount", "sentence": "金额"},
                "aggregations": ["sum"],
                "default_aggregation": "sum",
            },
        )
        for update in (
            {"formula": {"oxl": 42}},
            {"aggregations": ["sum"], "default_aggregation": "avg"},
            {"datatype": "python"},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_payload("metric", {"schema": "metric/1", **update})

    def test_term_aliases_and_references(self) -> None:
        validate_payload("term", {"schema": "term/1", "aliases": ["供方"], "refers_to": ["OT-X"]})
        with self.assertRaises(ValueError):
            validate_payload("term", {"schema": "term/1", "aliases": "供方"})

    def test_position_and_party(self) -> None:
        validate_payload("position", {"schema": "position/1", "org_id": "ORG-X", "reports_to": None})
        validate_payload(
            "party", {"schema": "party/1", "party_type": "supplier", "roles": [{"org_id": "ORG-X", "role": "supplier"}]}
        )
        for kind, update in (
            ("position", {"reports_to": 3}),
            ("party", {"party_type": "unknown"}),
            ("party", {"roles": [{"org_id": "ORG-X"}]}),
        ):
            with self.subTest(kind=kind, update=update), self.assertRaises(ValueError):
                validate_payload(kind, {"schema": kind + "/1", **update})

    def test_process_step_integrity(self) -> None:
        payload = {
            "schema": "process/1",
            "stages": [{"key": "G1", "name": "办理"}],
            "steps": [{"key": "S1", "name": "申请", "stage": "G1", "next": ["S2"]}, {"key": "S2", "name": "完成"}],
        }
        validate_payload("process", payload)
        bad = [
            {**payload, "steps": payload["steps"] * 2},
            {**payload, "stages": []},
            {**payload, "steps": payload["steps"][:1]},
            {**payload, "exceptions": [{"name": "例外", "steps": ["missing"]}]},
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_payload("process", value)

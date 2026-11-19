"""Release version suggestions use business changes and stable snapshot bytes."""

import hashlib
import unittest
from collections.abc import Iterable
from copy import deepcopy

from backend.core.errors import ApiError
from backend.kernel import release_snapshots as snapshots


class ReleaseDiffs(unittest.TestCase):
    def member(self, kind: str = "term", ident: str = "A", **updates: object) -> dict:
        snapshot = {
            "id": ident,
            "kind": kind,
            "title": "租赁",
            "version": 1,
            "summary": "租赁说明",
            "domain": "综合",
            "org_id": "hq",
            "source_kind": "user",
            "payload": {"definition": "租赁的定义"},
            "meta": {},
            "relations": [],
            "bindings": [],
            **updates,
        }
        return snapshots.make_item(snapshot, "member")

    def level(self, before: list[dict], after: list[dict], promoted: Iterable[str] = ()) -> str:
        _, bump = snapshots.compare(before, after, promoted)
        return bump

    def test_manifest_is_hash_of_hashes_sorted_by_entity_id(self) -> None:
        a, b = self.member(), self.member(ident="B")
        expected = hashlib.sha256((a["content_hash"] + b["content_hash"]).encode()).hexdigest()
        self.assertEqual(snapshots.manifest_hash([b, a]), expected)
        self.assertNotEqual(snapshots.manifest_hash([b]), expected)

    def test_receipt_version_does_not_change_member_content(self) -> None:
        a, b = self.member(), self.member(version=42)
        self.assertEqual(a["content_hash"], b["content_hash"])
        self.assertEqual(a["entity_version"], 1)
        self.assertEqual(b["entity_version"], 42)

    def test_relation_review_and_temporary_id_do_not_change_semantics(self) -> None:
        edge = {
            "id": "rel-12",
            "type": "applies_to",
            "source": "A",
            "target": "P",
            "status": "confirmed",
            "label": "适用于",
            "payload": {"role": "经办"},
        }
        a = self.member(relations=[edge])
        b = self.member(relations=[{**edge, "id": "rel-recreated"}])
        self.assertEqual(a["content_hash"], b["content_hash"])

    def test_ref_only_drift_does_not_raise_version(self) -> None:
        a = snapshots.make_item({"id": "P", "kind": "person", "title": "经办人", "version": 2}, "ref")
        b = snapshots.make_item({**a["snapshot"], "version": 3, "title": "项目经办人"}, "ref")
        self.assertNotEqual(a["content_hash"], b["content_hash"])
        self.assertEqual(
            snapshots.compare([self.member(), a], [self.member(), b], ())[0],
            {"added": [], "changed": [], "removed": [], "promoted": []},
        )

    def test_promoted_previous_member_is_minor_even_if_only_member(self) -> None:
        a = self.member()
        ref = snapshots.make_item({k: a["snapshot"][k] for k in ("id", "kind", "title", "version")}, "ref")
        diff, bump = snapshots.compare([a], [ref], {"A"})
        self.assertEqual((diff["promoted"], diff["removed"], bump), (["A"], [], "minor"))
        self.assertEqual(self.level([a], [ref]), "major")

    def test_new_member_is_minor_and_archive_is_major(self) -> None:
        a, b = self.member(), self.member(ident="B")
        self.assertEqual(self.level([a], [a, b]), "minor")
        self.assertEqual(self.level([a, b], [a]), "major")

    def test_plain_text_alias_and_source_edits_are_patch(self) -> None:
        a = self.member()
        for field, value in (("definition", "另一种说明"), ("aliases", ["别名"]), ("source_refs", [{"quote": "原话"}])):
            payload = {**a["snapshot"]["payload"], field: value}
            with self.subTest(field=field):
                self.assertEqual(self.level([a], [self.member(payload=payload)]), "patch")

    def test_object_added_property_is_minor(self) -> None:
        prop = {"key": "tax", "name": "税额", "datatype": "decimal"}
        a = self.member("object_type", payload={"properties": []})
        b = self.member("object_type", payload={"properties": [prop]})
        self.assertEqual(self.level([a], [b]), "minor")

    def test_object_dropped_property_is_major(self) -> None:
        prop = {"key": "tax", "name": "税额", "datatype": "decimal"}
        a = self.member("object_type", payload={"properties": [prop]})
        self.assertEqual(self.level([a], [self.member("object_type", payload={"properties": []})]), "major")

    def test_object_retyped_property_is_major(self) -> None:
        prop = {"key": "tax", "datatype": "decimal"}
        a = self.member("object_type", payload={"properties": [prop]})
        b = self.member("object_type", payload={"properties": [{**prop, "datatype": "string"}]})
        self.assertEqual(self.level([a], [b]), "major")

    def test_object_identity_key_is_major_but_description_is_patch(self) -> None:
        base = {"properties": ["id"], "note": "原始键"}
        a = self.member("object_type", payload={"identity": base})
        self.assertEqual(
            self.level([a], [self.member("object_type", payload={"identity": {**base, "properties": ["name"]}})]),
            "major",
        )
        self.assertEqual(
            self.level([a], [self.member("object_type", payload={"identity": {**base, "note": "文字修订"}})]), "patch"
        )

    def test_metric_formula_but_not_its_explanation_is_major(self) -> None:
        a = self.member("metric", payload={"formula": {"oxl": "$amount", "sentence": "合同金额"}})
        self.assertEqual(
            self.level([a], [self.member("metric", payload={"formula": {"oxl": "$amount * 2", "sentence": "解释"}})]),
            "major",
        )
        self.assertEqual(
            self.level([a], [self.member("metric", payload={"formula": {"oxl": "$amount", "sentence": "字词修订"}})]),
            "patch",
        )

    def test_rule_judgement_oxl_is_major(self) -> None:
        base = {"condition": {"oxl": "true", "sentence": "如适用"}, "requirement": {"oxl": "$amount > 0"}}
        a = self.member("rule", payload={"judgement": base})
        for field in ("condition", "requirement"):
            with self.subTest(field=field):
                edited = deepcopy(base)
                edited[field]["oxl"] = "false"
                self.assertEqual(self.level([a], [self.member("rule", payload={"judgement": edited})]), "major")
        edited = deepcopy(base)
        edited["condition"]["sentence"] = "属于该范围时"
        self.assertEqual(self.level([a], [self.member("rule", payload={"judgement": edited})]), "patch")

    def test_only_new_confirmed_relation_is_minor(self) -> None:
        edge = {
            "id": "rel",
            "source": "A",
            "target": "B",
            "type": "based_on",
            "label": "依据",
            "status": "confirmed",
            "payload": {},
        }
        self.assertEqual(self.level([self.member()], [self.member(relations=[edge])]), "minor")

    def test_new_published_binding_is_minor(self) -> None:
        self.assertEqual(
            self.level([self.member()], [self.member(bindings=[{"id": "MAP-X", "fields": [{"asset_id": "A"}]}])]),
            "minor",
        )

    def test_numeric_version_order_and_requested_increase(self) -> None:
        self.assertEqual(snapshots.choose_version("1.9.10", "1.10.0"), "1.10.0")
        with self.assertRaises(ApiError):
            snapshots.choose_version("2.0.0", "1.99.99")

    def test_only_three_canonical_numeric_components(self) -> None:
        for value in (
            "1",
            "1.0",
            "01.0.0",
            "1.01.0",
            "1.0.-1",
            "1.0.0-alpha",
            "\uff11\uff12.0.0",
            "",
            "1.2.3\n",
            "12345678901234567890.1.2",
        ):
            with self.subTest(value=value), self.assertRaises(ApiError):
                snapshots.choose_version("1.0.0", value)

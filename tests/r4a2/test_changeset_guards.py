"""AC3/4/8: changeset writes share specialist ownership, per-item transactions and gate history.

Once a skill has a package, every generic content or workflow write must stop before changing the
package, entity, proposal or versions. Confirmation and submission must respect the same guard.
Filling a gap retires only the affected application with one activity, including after denial.
Submitting eligible siblings must survive one item's preparation failure without leaving partial
material, draft, relation or activity writes. Permission and database failures abort the request.
"""

import os
import unittest
from contextlib import closing
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from httpx import Response

from backend import db
from backend.core.errors import ApiError
from backend.domains import build
from backend.kernel.gates import evaluate_checks
from backend.security import store
from backend.security.context import use_context
from tests.r4a1 import test_build_workflow as workflow


class MeaningReasons(unittest.TestCase):
    def test_each_missing_data_field_is_named_without_requesting_complete_ones(self) -> None:
        fields = {"definition": "单笔付款金额", "unit": "元", "grain": "每张付款单"}
        for key, label in (("definition", "定义"), ("unit", "单位"), ("grain", "粒度")):
            with self.subTest(missing=key):
                check = evaluate_checks({"kind": "data", "payload": {**fields, key: ""}}, {}, {})["checks"][1]
                self.assertEqual(check["status"], "fail")
                self.assertFalse(check["can_waive"])
                self.assertIn(label, check["reason"])
                self.assertIn("不能豁免", check["reason"])
                for present in {"定义", "单位", "粒度"} - {label}:
                    self.assertNotIn(present, check["reason"])

    def test_nonwaivable_checks_explain_the_business_consequence(self) -> None:
        checks = evaluate_checks({"kind": "rule", "payload": {}}, {}, {})["checks"]
        for index, reason in ((1, "一致的业务口径"), (3, "无法确定适用对象或责任方"), (4, "无法追溯和还原修改")):
            with self.subTest(code=checks[index]["code"]):
                self.assertEqual(checks[index]["status"], "fail")
                self.assertFalse(checks[index]["can_waive"])
                self.assertIn(reason, checks[index]["reason"])
                self.assertIn("不能豁免", checks[index]["reason"])


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in real changeset guard contracts")
class ChangesetWriteGuards(unittest.TestCase):
    setUpClass = classmethod(workflow.BuildWorkflow.setUpClass.__func__)
    tearDownClass = classmethod(workflow.BuildWorkflow.tearDownClass.__func__)
    login = workflow.BuildWorkflow.login
    invoke = workflow.BuildWorkflow.invoke
    proposal = workflow.BuildWorkflow.proposal
    confirm = workflow.BuildWorkflow.confirm
    accept = workflow.BuildWorkflow.accept

    def edit(self, client: TestClient, proposal: dict, item: dict, **fields: object) -> Response:
        after = item["after_data"]
        response = client.post(
            "/api/v2/changesets/" + proposal["id"] + "/items:bulk-decide",
            json={
                "changeset_id": proposal["id"],
                "item_ids": [item["id"]],
                "decision": "edited",
                "edit": {
                    "title": after["title"],
                    "kind": after["kind"],
                    "content": after["content"],
                    "expected_version": item.get("applied_version"),
                    **fields,
                },
            },
        )
        return response

    def test_clean_submission_has_no_waiver_activity(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.confirm(client, self.proposal(client))
        ready = next(item for item in proposal["items"] if item["gates"]["passed"])
        self.accept(client, proposal, [ready["id"]])
        result = self.invoke(client, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})
        self.assertEqual(result["submitted_count"], 1)
        entity = client.get("/api/entities/" + result["submitted_entity_ids"][0]).json()
        self.assertEqual(entity["status"], "review")
        self.assertIn("提交审核", {row["action"] for row in entity["activities"]})
        self.assertFalse(any("豁免" in row["action"] + row["detail"] for row in entity["activities"]))

    def test_managed_skill_blocks_all_three_actions_before_version_checks(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.proposal(client)
        item = proposal["items"][0]
        self.assertEqual(self.edit(client, proposal, item, kind="skill").status_code, 200)
        request = {"changeset_id": proposal["id"]}
        ident = self.invoke(client, "changeset.apply", request)["entity_ids"][0]
        path = "/api/skills/" + ident
        package = client.post("/api/skills", json={"legacy_entity_id": ident, "title": "核对合同付款"})
        self.assertEqual(package.status_code, 200, package.text)
        before = client.get(path).json()
        proposal = self.invoke(client, "changeset.get", request)
        item = next(value for value in proposal["items"] if value["id"] == item["id"])
        versions = client.get(f"/api/entities/{ident}/versions").json()
        for operation in ("edit", "kind", "discard"):
            with self.subTest(operation=operation):
                if operation == "discard":
                    response = client.post(
                        "/api/v2/capabilities/changeset.decide_items:invoke",
                        json={**request, "item_ids": [item["id"]], "decision": "rejected"},
                    )
                else:
                    response = self.edit(
                        client,
                        proposal,
                        item,
                        **({"kind": "rule"} if operation == "kind" else {"content": "改过的技能正文"}),
                    )
                self.assertEqual(response.status_code, 409, response.text)
                error = response.json()["error"]
                self.assertIn("技能工作台", error["message"])
                self.assertEqual(
                    error["next_actions"], [{"label": "前往技能工作台", "href": "/skill-development/" + ident}]
                )
                current = client.get(path).json()
                for field in ("entity", "package_revision", "files", "manifest", "digest", "history", "gate_history"):
                    self.assertEqual(current[field], before[field])
                self.assertEqual(client.get(f"/api/entities/{ident}/versions").json(), versions)
                again = self.invoke(client, "changeset.get", request)
                self.assertEqual(next(value for value in again["items"] if value["id"] == item["id"]), item)
        batch = self.invoke(client, "changeset.apply_and_submit", request)
        self.assertEqual(batch["submitted_count"], 0)
        self.assertIn("技能工作台", " ".join(batch["remaining_items"][0]["reasons"]))

    def test_condition_repair_withdraws_actual_application_after_denial_or_before_submission(self) -> None:
        client, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        proposal = self.proposal(client)
        request = {"changeset_id": proposal["id"]}
        cases = [
            item
            for item in proposal["items"]
            if any(check["code"] == "QG3" and check["status"] == "fail" for check in item["gates"]["checks"])
        ][:2]
        self.assertEqual(len(cases), 2)
        for item in cases:
            response = self.edit(client, proposal, item, waiver_applications={"QG3": "请独立核对缺少的条件"})
            self.assertEqual(response.status_code, 200, response.text)
        self.invoke(client, "changeset.apply", request)

        def row(item: dict) -> dict:
            return next(
                value for value in self.invoke(client, "changeset.get", request)["items"] if value["id"] == item["id"]
            )

        def repair(item: dict) -> str:
            current = row(item)
            self.assertEqual(current["after_data"]["waiver_applications"], {"QG3": "请独立核对缺少的条件"})
            repaired = self.edit(
                client,
                proposal,
                current,
                scope="办理合同业务",
                conditions=["核对前"],
                exceptions="无例外",
                valid_from="2026-01-01",
                waiver_applications=current["after_data"]["waiver_applications"],
            )
            self.assertEqual(repaired.status_code, 200, repaired.text)
            fresh = row(item)
            self.assertEqual(fresh["after_data"]["waiver_applications"], {})
            checks = {c["code"]: c for c in fresh["gates"]["checks"]}
            self.assertEqual(checks["QG3"]["status"], "pass")
            self.assertFalse(checks["QG3"].get("application_reason"))
            self.assertFalse(checks["QG3"].get("denial_reason"))
            return fresh["applied_entity_id"]

        first_id = repair(cases[0])
        sent = self.invoke(client, "changeset.apply_and_submit", request)
        second = row(cases[1])
        second_id = second["applied_entity_id"]
        self.assertCountEqual(sent["submitted_entity_ids"], [first_id, second_id])
        rejected = reviewer.post(
            f"/api/v2/reviews/{second_id}:decide",
            json={
                "decision": "return",
                "version": second["applied_version"],
                "reason": "需要实际补充条件",
                "waiver_denials": {"QG3": "该条件可以查实"},
            },
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertFalse(row(cases[1])["can_submit"])
        repair(cases[1])
        sent = self.invoke(client, "changeset.apply_and_submit", request)
        self.assertEqual(sent["submitted_entity_ids"], [second_id])
        for item in cases:
            current = row(item)
            ident = current["applied_entity_id"]
            response = reviewer.post(
                f"/api/v2/reviews/{ident}:decide",
                json={"decision": "approve_publish", "version": current["applied_version"]},
            )
            self.assertEqual(response.status_code, 200, response.text)
            entity = client.get("/api/entities/" + ident).json()
            self.assertEqual(entity["status"], "published")
            history = [(e["action"], e["detail"]) for e in entity["activities"]]
            self.assertEqual(sum(action == "申请发布检查豁免" for action, _ in history), 1)
            self.assertEqual(
                sum(
                    action == "撤回发布检查豁免申请" and "条件" in detail and "已通过" in detail
                    for action, detail in history
                ),
                1,
            )
            self.assertEqual(sum(action == "拒绝发布检查豁免" for action, _ in history), int(ident == second_id))

    def test_item_preparation_failure_rolls_back_generated_records_and_retry_submits_only_that_item(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.confirm(client, self.proposal(client))
        ready = [item for item in proposal["items"] if item["gates"]["passed"]][:3]
        self.assertEqual(len(ready), 3)
        self.accept(client, proposal, [item["id"] for item in ready])
        request = {"changeset_id": proposal["id"]}
        real_record = build.record_candidate_sources
        generated_ids = []

        def fail_second_after_recording(conn: db.Connection, entity: dict, candidate: dict) -> None:
            real_record(conn, entity, candidate)
            if candidate["id"] == ready[1]["after_data"]["id"]:
                generated_ids.append(entity["id"])
                raise HTTPException(422, "本条的原文定位已失效。")

        with patch.object(build, "record_candidate_sources", side_effect=fail_second_after_recording):
            result = self.invoke(client, "changeset.apply_and_submit", request)
        self.assertEqual(len(generated_ids), 1)
        orphan = generated_ids[0]
        self.assertEqual(
            result["remaining_items"],
            [
                {
                    "item_id": ready[1]["id"],
                    "title": ready[1]["after_data"]["title"],
                    "reasons": ["本条的原文定位已失效。"],
                }
            ],
        )
        self.assertEqual(result["submitted_count"], 2)
        self.assertEqual(result["entity_ids"], result["submitted_entity_ids"])
        self.assertEqual(client.get("/api/entities/" + orphan).status_code, 404)
        self.assertEqual(client.get(f"/api/entities/{orphan}/versions").json()["items"], [])
        self.assertEqual(client.get("/api/activity", params={"entity_id": orphan}).json()["items"], [])
        self.assertEqual(client.get("/api/relations", params={"node": orphan}).json()["total"], 0)
        material = client.get("/api/entities/" + proposal["source_material_id"]).json()
        candidate = next(c for c in material["payload"]["candidates"] if c["id"] == ready[1]["after_data"]["id"])
        self.assertFalse(candidate.get("entity_id"))
        reopened = self.invoke(client, "changeset.get", request)
        failed = next(row for row in reopened["items"] if row["id"] == ready[1]["id"])
        self.assertFalse(failed["applied_entity_id"])
        self.assertEqual(failed["decision"], "accepted")
        retried = self.invoke(client, "changeset.apply_and_submit", request)
        self.assertEqual(retried["submitted_count"], 1)
        self.assertEqual(retried["remaining_count"], 0)
        self.assertTrue(set(retried["submitted_entity_ids"]).isdisjoint(result["submitted_entity_ids"]))

    def test_lost_permission_during_preparation_aborts_every_item(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.confirm(client, self.proposal(client))
        ready = [item for item in proposal["items"] if item["gates"]["passed"]][:2]
        self.assertEqual(len(ready), 2)
        self.accept(client, proposal, [item["id"] for item in ready])
        request = {"changeset_id": proposal["id"]}
        record = build.record_candidate_sources
        created = []

        def remove_access(conn: db.Connection, entity: dict, candidate: dict) -> None:
            created.append(entity["id"])
            record(conn, entity, candidate)
            if len(created) == 2:
                raise ApiError("PERMISSION_DENIED")

        with patch.object(build, "record_candidate_sources", side_effect=remove_access):
            response = client.post("/api/v2/capabilities/changeset.apply_and_submit:invoke", json=request)
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(len(created), 2)
        self.assertTrue(all(client.get("/api/entities/" + ident).status_code == 404 for ident in created))
        reopened = self.invoke(client, "changeset.get", request)
        self.assertFalse(any(row["applied_entity_id"] for row in reopened["items"]))

    def test_stale_discard_and_batch_date_cannot_write_to_historical_content(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.confirm(client, self.proposal(client))
        item = proposal["items"][1]
        ids = [item["id"]]
        self.accept(client, proposal, ids)
        request = {"changeset_id": proposal["id"]}
        ident = self.invoke(client, "changeset.apply", request)["entity_ids"][0]
        before = self.invoke(client, "changeset.get", request)
        saved = next(row for row in before["items"] if row["id"] == item["id"])
        edited = self.edit(client, proposal, saved, title=saved["after_data"]["title"] + "（已复核）")
        self.assertEqual(edited.status_code, 200, edited.text)
        discarded = client.post(
            "/api/v2/capabilities/changeset.decide_items:invoke",
            json={
                **request,
                "item_ids": ids,
                "decision": "rejected",
                "expected_versions": {item["id"]: saved["applied_version"]},
            },
        )
        self.assertEqual(discarded.status_code, 409, discarded.text)
        current = client.get("/api/entities/" + ident).json()
        self.assertEqual(current["status"], "draft")
        historical = client.patch(
            "/api/entities/" + ident,
            json={"expected_version": current["version"], "payload": {**current["payload"], "active_asset": False}},
        )
        self.assertEqual(historical.status_code, 200, historical.text)
        snapshot = self.invoke(client, "changeset.get", request)
        refused = client.post(
            "/api/v2/capabilities/changeset.confirm_facts:invoke", json={**request, "valid_from": "2026-02-23"}
        )
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn("历史条目", refused.json()["error"]["message"])
        after = self.invoke(client, "changeset.get", request)
        self.assertEqual(after, snapshot)
        self.assertEqual(client.get("/api/entities/" + ident).json(), historical.json())

    def test_protected_owners_share_generic_guard_and_keep_their_destination(self) -> None:
        client = self.login("fixture.builder")
        person = store.authenticate_session(client.cookies.get("ontology_session"), "guard-contract")[0]
        with use_context(person), closing(db.connect()) as conn:
            for kind, payload, destination in (
                ("rule", {"authority_spec": {"type": "position"}}, "权责规范工作面"),
                ("person", {}, "权责规范工作面"),
                ("matter", {}, "专项事项与依据工作面"),
                ("evidence", {}, "专项事项与依据工作面"),
                ("task", {}, "任务或专题工作档案"),
                ("project", {"project_mode": "special_review"}, "任务或专题工作档案"),
                ("rule", {"active_asset": False}, "历史条目"),
                ("organization", {}, "机构根节点"),
            ):
                with self.subTest(kind=kind), self.assertRaises(ApiError) as blocked:
                    build.guard_adopted_write(conn, {"id": "typed-candidate", "kind": kind, "payload": payload})
                self.assertIn(destination, blocked.exception.message)
                self.assertLess(blocked.exception.status_code, 500)

    def test_pending_view_cannot_discard_a_draft_created_since_it_was_shown(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.proposal(client)
        item = proposal["items"][0]
        self.assertFalse(item["applied_entity_id"])
        request = {"changeset_id": proposal["id"]}
        self.accept(client, proposal, [item["id"]])
        ident = self.invoke(client, "changeset.apply", request)["entity_ids"][0]
        response = client.post(
            "/api/v2/capabilities/changeset.decide_items:invoke",
            json={**request, "item_ids": [item["id"]], "decision": "rejected", "expected_versions": {item["id"]: 0}},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(client.get("/api/entities/" + ident).json()["status"], "draft")

    def test_bulk_submit_ignores_a_redundant_note_without_creating_an_application(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.confirm(client, self.proposal(client))
        first, second = [item for item in proposal["items"] if item["gates"]["passed"]][:2]
        self.assertEqual(
            self.edit(client, proposal, first, waiver_applications={"QG3": "核对时旧表单仍带着这句说明。"}).status_code,
            200,
        )
        self.accept(client, proposal, [second["id"]])
        request = {"changeset_id": proposal["id"]}
        sent = self.invoke(client, "changeset.apply_and_submit", request)
        self.assertEqual((sent["submitted_count"], sent["remaining_count"]), (2, 0))
        for ident in sent["submitted_entity_ids"]:
            result = self.invoke(client, "element.get", {"entity_id": ident})
            self.assertEqual(result["entity"]["status"], "review")
            self.assertTrue(result["gates"]["passed"])
            self.assertFalse(any("豁免" in event["action"] for event in result["entity"]["activities"]))

    def test_file_confirmation_cannot_reinstate_a_request_withdrawn_in_the_element(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.proposal(client)
        item = next(row for row in proposal["items"] if not row["after_data"]["exceptions"])
        self.assertEqual(self.edit(client, proposal, item, waiver_applications={"QG3": "待核例外"}).status_code, 200)
        request = {"changeset_id": proposal["id"]}
        ident = self.invoke(client, "changeset.apply", request)["entity_ids"][0]
        detail = self.invoke(client, "element.get", {"entity_id": ident})
        self.assertEqual(detail["gates"]["checks"][2]["application_reason"], "待核例外")
        cleared = self.invoke(
            client,
            "element.update_draft",
            {"entity_id": ident, "version": detail["entity"]["version"], "waiver_applications": {}},
        )
        current = self.invoke(
            client,
            "changeset.confirm_facts",
            {**request, "valid_from": "2026-02-28", "expected_versions": {item["id"]: cleared["entity"]["version"]}},
        )
        saved = next(row for row in current["items"] if row["id"] == item["id"])
        self.assertEqual(saved["after_data"]["waiver_applications"], {})
        self.assertFalse(saved["can_submit"])
        activities = client.get("/api/activity", params={"entity_id": ident}).json()["items"]
        self.assertEqual(sum(row["action"] == "申请发布检查豁免" for row in activities), 1)
        self.assertEqual(sum(row["action"] == "撤回发布检查豁免申请" for row in activities), 1)

    def test_partial_edit_keeps_the_other_persons_unresolved_request_and_cannot_override_its_denial(self) -> None:
        builder, editor, reviewer = (
            self.login("fixture.builder"),
            self.login("fixture.admin"),
            self.login("fixture.reviewer"),
        )
        proposal = self.proposal(builder)
        item = next(row for row in proposal["items"] if not row["after_data"]["exceptions"])
        request = {"changeset_id": proposal["id"]}
        reason = "缺少可查实的例外，请独立审核本项。"
        self.assertEqual(self.edit(builder, proposal, item, waiver_applications={"QG3": reason}).status_code, 200)
        ident = self.invoke(builder, "changeset.apply", request)["entity_ids"][0]

        def fresh() -> dict:
            return next(
                row for row in self.invoke(builder, "changeset.get", request)["items"] if row["id"] == item["id"]
            )

        def application_after_edit(suffix: str) -> dict:
            current = fresh()
            response = self.edit(editor, proposal, current, title=current["after_data"]["title"] + suffix)
            self.assertEqual(response.status_code, 200, response.text)
            saved = fresh()
            self.assertEqual(saved["after_data"]["waiver_applications"], {"QG3": reason})
            check = saved["gates"]["checks"][2]
            self.assertEqual(
                (check["status"], check["application_reason"], check["application_by"], check["application_at"]),
                ("fail", reason, original["application_by"], original["application_at"]),
            )
            return saved

        original = fresh()["gates"]["checks"][2]
        self.assertEqual(original["application_by"], "fixture.builder")
        self.assertTrue(application_after_edit("（核对）")["can_submit"])
        self.assertEqual(self.invoke(editor, "changeset.apply_and_submit", request)["submitted_entity_ids"], [ident])
        version = fresh()["applied_version"]
        review_path = f"/api/v2/reviews/{ident}:decide"
        blocked = reviewer.post(review_path, json={"decision": "approve_publish", "version": version})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        denied = reviewer.post(
            review_path,
            json={
                "decision": "return",
                "version": version,
                "reason": "补齐例外",
                "waiver_denials": {"QG3": "这项缺口需要补齐"},
            },
        )
        self.assertEqual(denied.status_code, 200, denied.text)
        after_denial = application_after_edit("（再核）")
        self.assertEqual(after_denial["gates"]["checks"][2]["denial_reason"], "这项缺口需要补齐")
        self.assertFalse(after_denial["can_submit"])
        retry = self.invoke(builder, "changeset.apply_and_submit", request)
        self.assertEqual((retry["submitted_count"], retry["remaining_count"]), (0, 1))
        activities = builder.get("/api/activity", params={"entity_id": ident}).json()["items"]
        self.assertEqual(sum(row["action"] == "申请发布检查豁免" for row in activities), 1)
        self.assertFalse(any(row["action"] == "撤回发布检查豁免申请" for row in activities))

    def test_an_unresolved_application_survives_a_changed_draft_but_its_prior_waiver_does_not(self) -> None:
        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        proposal = self.proposal(builder)
        item = next(row for row in proposal["items"] if not row["after_data"]["exceptions"])
        request = {"changeset_id": proposal["id"]}
        application = "请独立检查缺失的例外说明。"
        self.assertEqual(self.edit(builder, proposal, item, waiver_applications={"QG3": application}).status_code, 200)
        ident = self.invoke(builder, "changeset.apply_and_submit", request)["submitted_entity_ids"][0]
        before = reviewer.get("/api/v2/reviews/" + ident).json()["data"]["entity"]
        path = f"/api/v2/reviews/{ident}:decide"
        returned = reviewer.post(
            path,
            json={
                "decision": "return",
                "version": before["version"],
                "reason": "请修订标题说明业务对象",
                "waivers": {"QG3": "本版使用范围已人工核实"},
            },
        )
        self.assertEqual(returned.status_code, 200, returned.text)
        self.assertEqual(returned.json()["data"]["gates"]["checks"][2]["status"], "waived")
        item = next(row for row in self.invoke(builder, "changeset.get", request)["items"] if row["id"] == item["id"])
        updated = self.edit(builder, proposal, item, title=item["after_data"]["title"] + "（办理人核对）")
        self.assertEqual(updated.status_code, 200, updated.text)
        check = self.invoke(builder, "element.get", {"entity_id": ident})["gates"]["checks"][2]
        self.assertEqual(
            (check["status"], check["application_reason"], check["waiver_reason"]), ("fail", application, "")
        )
        self.assertEqual(self.invoke(builder, "changeset.apply_and_submit", request)["submitted_entity_ids"], [ident])
        submitted = reviewer.get("/api/v2/reviews/" + ident).json()["data"]["entity"]
        decision = {"decision": "approve_publish", "version": submitted["version"]}
        blocked = reviewer.post(path, json=decision)
        self.assertEqual(blocked.status_code, 409, blocked.text)
        released = reviewer.post(path, json={**decision, "waivers": {"QG3": "独立核对本版并接受例外说明缺口"}})
        self.assertEqual(released.status_code, 200, released.text)
        self.assertEqual(released.json()["data"]["entity"]["status"], "published")

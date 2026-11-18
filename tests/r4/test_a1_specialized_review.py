"""D15 cumulative editors through specialized HTTP contracts and real source anchors."""

import os
import secrets
import unittest

from fastapi.testclient import TestClient

from tests.r2 import test_build as construction
from tests.test_skills import MANIFEST


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in A.1 specialized HTTP/MySQL workflow")
class SpecializedReviewHTTP(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke

    def request(self, client: TestClient, method: str, path: str, **kwargs: object) -> dict:
        response = client.request(method, path, **kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def source(self, client: TestClient) -> dict:
        quote = "【合成制度】本机构办理合成业务时逐项核对输入并形成结果，无其他例外，自2026年1月1日起生效。"
        material = self.request(
            client,
            "POST",
            "/api/materials/upload",
            files={"file": ("职责方法依据.txt", quote.encode(), "text/plain")},
        )["id"]
        reading = f"/api/materials/{material}/reading"
        draft = self.request(client, "GET", reading)
        saved = self.request(client, "POST", reading + "/confirm", json={"revision": draft["revision"]})
        source = self.request(
            client,
            "GET",
            f"/api/materials/{material}/source",
            params={"anchor_id": saved["segments"][0]["anchor_id"]},
        )
        self.assertEqual(source["text"], quote)
        self.assertEqual(source["focus"]["quote"], quote)
        self.invoke(client, "recipe.start", {"material_id": material})
        return {
            "kind": "anchor",
            **{
                key: source["focus"][key]
                for key in ("material_id", "source_version", "anchor_id", "start", "end", "quote", "locator")
            },
        }

    def test_authority_original_and_intermediate_editors_cannot_review(self) -> None:
        admin, builder, reviewer = (
            self.login(name) for name in ("fixture.admin", "fixture.builder", "fixture.reviewer")
        )
        reference = self.source(builder)
        for admin_first in (True, False):
            with self.subTest(admin_first=admin_first):
                body = {
                    "title": "合成职责核对" + secrets.token_hex(4),
                    "spec": {
                        "type": "position",
                        "purpose": "逐项核对合成业务输入",
                        "basis": "职责方法依据",
                        "scope": "本机构合成业务",
                        "conditions": "办理所述合成业务时",
                        "exceptions": "无其他例外",
                        "effective_from": "2026-01-01",
                        "org_ids": ["dept-01"],
                        "sections": [],
                        "source_refs": [reference],
                    },
                }
                detail = self.request(admin if admin_first else builder, "POST", "/api/authority/assets", json=body)
                ident = detail["entity"]["id"]
                # Five checks cannot substitute for the specialist's required content cards.
                blocked = builder.post(
                    f"/api/entities/{ident}/action",
                    json={"action": "submit", "expected_version": 1, "reason": "缺内容卡片"},
                )
                self.assertEqual(blocked.status_code, 422, blocked.text)
                body["spec"]["sections"] = [{"id": "one", "title": "核对职责", "content": "逐项核对输入并形成结果"}]
                editors = (builder,) if admin_first else (admin, builder)
                for index, editor in enumerate(editors):
                    body.update(expected_version=detail["entity"]["version"], summary=f"第{index + 1}位编辑核对说明")
                    detail = self.request(editor, "PUT", f"/api/authority/assets/{ident}", json=body)
                entity = self.request(
                    builder,
                    "POST",
                    f"/api/entities/{ident}/action",
                    json={
                        "action": "submit",
                        "expected_version": detail["entity"]["version"],
                        "reason": "完成规范核对",
                    },
                )
                for path, request in (
                    (f"/api/entities/{ident}/action", {"action": "approve", "expected_version": entity["version"]}),
                    (f"/api/v2/reviews/{ident}:decide", {"decision": "approve_publish", "version": entity["version"]}),
                ):
                    denied = admin.post(path, json=request)
                    self.assertEqual(denied.status_code, 403, denied.text)
                released = self.request(
                    reviewer,
                    "POST",
                    f"/api/v2/reviews/{ident}:decide",
                    json={"decision": "approve_publish", "version": entity["version"]},
                )["data"]
                self.assertEqual(released["entity"]["status"], "published")
                self.assertTrue(released["gates"]["passed"])
                self.assertEqual(len(released["entity"]["payload"]["r2_review"]["editor_ids"]), 2)

    def test_skill_original_and_intermediate_editors_cannot_review_and_proof_stays_required(self) -> None:
        admin, builder, reviewer = (
            self.login(name) for name in ("fixture.admin", "fixture.builder", "fixture.reviewer")
        )
        reference = self.source(builder)
        for admin_first in (True, False):
            with self.subTest(admin_first=admin_first):
                name = "a1-editor-skill-" + secrets.token_hex(4)
                detail = self.request(
                    admin if admin_first else builder,
                    "POST",
                    "/api/skills",
                    json={"title": name, "package_name": name},
                )
                ident = detail["entity"]["id"]
                manifest = {
                    **MANIFEST,
                    "source_refs": [reference],
                    "dependency_note": "本合成技能仅使用显式输入，不依赖其他要素。",
                }
                editors = (builder,) if admin_first else (admin, builder)
                for index, editor in enumerate(editors):
                    detail = self.request(
                        editor,
                        "PUT",
                        f"/api/skills/{ident}",
                        json={
                            "expected_version": detail["entity"]["version"],
                            "title": name,
                            "files": {
                                **detail["files"],
                                "SKILL.md": detail["files"]["SKILL.md"] + f"\n第{index + 1}位编辑补充核对说明。\n",
                            },
                            "manifest": manifest,
                            "reason": "核对方法与实际依据",
                        },
                    )
                version = detail["entity"]["version"]
                blocked = builder.post(
                    f"/api/skills/{ident}/action",
                    json={"action": "submit", "expected_version": version, "reason": "尚未运行当前版本测试"},
                )
                self.assertEqual(blocked.status_code, 409, blocked.text)
                proof = self.request(builder, "POST", f"/api/skills/{ident}/test", json={"expected_version": version})
                self.assertEqual(proof["outcome"], "passed")
                detail = self.request(
                    builder,
                    "POST",
                    f"/api/skills/{ident}/action",
                    json={"action": "submit", "expected_version": version, "reason": "当前版本已核对和测试"},
                )
                version = detail["entity"]["version"]
                denied = admin.post(
                    f"/api/skills/{ident}/action",
                    json={"action": "publish", "expected_version": version, "reason": "旧编辑不能自行发布"},
                )
                self.assertEqual(denied.status_code, 403, denied.text)
                for path, body, status in (
                    (f"/api/entities/{ident}/action", {"action": "approve", "expected_version": version}, 409),
                    (f"/api/v2/reviews/{ident}:decide", {"decision": "approve_publish", "version": version}, 403),
                ):
                    generic = reviewer.post(path, json=body)
                    self.assertEqual(generic.status_code, status, generic.text)
                    self.assertIn("技能", generic.text)
                released = self.request(
                    reviewer,
                    "POST",
                    f"/api/skills/{ident}/action",
                    json={"action": "publish", "expected_version": version, "reason": "独立核对当前方法、依据与测试"},
                )
                self.assertEqual(released["entity"]["status"], "published")
                self.assertEqual(len(released["entity"]["payload"]["r2_review"]["editor_ids"]), 2)

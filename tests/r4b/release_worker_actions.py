"""Exercise a queued release against its real reviewer session and MySQL lease."""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from backend import jobs
from backend.security import store
from backend.security.context import Principal, use_context


class ReleaseWorkerActions:
    """Uses the migrated HTTP fixture from test_release_actions."""

    def _run_release_job(self, principal: Principal, job_id: str) -> str:
        deadline = time.monotonic() + 12
        with use_context(principal):
            while time.monotonic() < deadline:
                job = jobs.queue_status(job_id)
                if job and job["status"] not in {"queued", "running"}:
                    return job["status"]
                if not asyncio.run(jobs.run_one()):
                    time.sleep(0.1)
        self.fail("The submitted release job never reached a final queue state")

    def test_background_snapshot_loses_authority_on_logout_and_can_be_approved_again(self) -> None:
        admin = self.login("fixture.superadmin")
        actor = self.identity(admin)
        reviewer_name = "b1.hq.release-reviewer"
        store.create_user(
            actor,
            username=reviewer_name,
            display_name="公共部分审核人员",
            tenant_id=actor.tenant_id,
            role="reviewer",
            password="SyntheticFixture9!",
            must_change_password=False,
        )
        reviewer = self.login(reviewer_name)
        asset = self.asset(admin, reviewer, "付款复核期间", self.evidence(admin))
        draft = self.business(admin, "release.create_draft", {"module_id": "MOD-DEFAULT"})
        reference = {"release_id": draft["id"]}
        self.assertEqual(self.business(admin, "release.submit", reference)["status"], "candidate")
        pending = self.business(reviewer, "release.publish", {**reference, "background": True})
        self.assertEqual(pending["status"], "candidate")
        self.assertEqual(reviewer.post("/api/auth/logout").status_code, 200)
        self.assertEqual(self._run_release_job(actor, pending["job_id"]), "cancelled")

        def current() -> str | None:
            return self.get(admin, "/api/v2/modules/MOD-DEFAULT")["current_release_id"]

        detail_url = "/api/v2/releases/" + draft["id"]
        self.assertEqual(self.get(admin, detail_url)["status"], "candidate")
        self.assertIsNone(current())
        self.assertEqual(self.frozen(admin, draft["id"]), {})

        fresh_reviewer = self.login(reviewer_name)
        approved = self.business(fresh_reviewer, "release.publish", {**reference, "background": True})
        self.assertEqual(self._run_release_job(actor, approved["job_id"]), "completed")
        frozen = self.get(admin, detail_url)
        self.assertEqual((frozen["status"], current()), ("published", draft["id"]))
        self.assertEqual(frozen["reviewed_by"], self.identity(fresh_reviewer).user_id)
        self.assertIn(asset["id"], self.frozen(admin, draft["id"]))

    def test_two_creators_share_one_preview_and_a_later_entity_change_blocks_the_candidate(self) -> None:
        tenant = "dept-02"
        superadmin = self.identity(self.login("fixture.superadmin"))
        self.names[tenant] = store.get_tenant(tenant)["db_name"]
        for role in ("admin", "reviewer"):
            store.create_user(
                superadmin,
                username="b1.racing." + role,
                display_name="版本并行核验",
                tenant_id=tenant,
                role=role,
                password="SyntheticFixture9!",
                must_change_password=False,
            )
        first, second = self.login("b1.racing.admin"), self.login("b1.racing.admin")
        reviewer = self.login("b1.racing.reviewer")
        evidence = self.evidence(first)
        before = self.asset(first, reviewer, "交接首份登记", evidence)
        barrier = threading.Barrier(2)

        def create(client: TestClient) -> dict:
            barrier.wait(timeout=10)
            return self.business(client, "release.create_draft", {"module_id": "MOD-DEFAULT"})

        with ThreadPoolExecutor(max_workers=2) as pool:
            drafts = list(pool.map(create, [first, second]))
        self.assertEqual(drafts[0]["id"], drafts[1]["id"])
        self.assertEqual(drafts[0]["manifest_hash"], drafts[1]["manifest_hash"])
        self.assertEqual(self.get(first, "/api/v2/modules/MOD-DEFAULT/releases")["total"], 1)

        draft = drafts[0]
        reference = {"release_id": draft["id"]}
        candidate = self.business(first, "release.submit", reference)
        self.assertEqual(candidate["status"], "candidate")
        after = self.asset(first, reviewer, "交接补充登记", evidence)
        denied = reviewer.post("/api/v2/capabilities/release.publish:invoke", json=reference)
        self.rejected(denied, 409, "ITEM_CHANGED")
        self.assertEqual(self.frozen(first, draft["id"]), {})
        self.assertIsNone(self.get(first, "/api/v2/modules/MOD-DEFAULT")["current_release_id"])
        unchanged = self.detail(first, draft["id"])
        self.assertEqual((unchanged["status"], unchanged["manifest_hash"]), ("candidate", draft["manifest_hash"]))

        replacement = self.business(second, "release.create_draft", {"module_id": "MOD-DEFAULT"})
        self.assertNotEqual(replacement["id"], draft["id"])
        self.assertNotEqual(replacement["manifest_hash"], draft["manifest_hash"])
        latest = self.release(second, reviewer, replacement)
        self.assertEqual(latest["member_count"], 2)
        self.assertLessEqual({before["id"], after["id"]}, set(self.frozen(second, latest["id"])))

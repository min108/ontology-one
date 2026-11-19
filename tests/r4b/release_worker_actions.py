"""Exercise a queued release against its real reviewer session and MySQL lease."""

import asyncio
import time
from contextlib import closing

from backend import db, jobs
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

        def frozen_ids() -> list[str]:
            with use_context(actor), closing(db.connect()) as conn:
                return [
                    row[0]
                    for row in conn.execute(
                        "SELECT entity_id FROM ont_release_items WHERE release_id=? ORDER BY entity_id", (draft["id"],)
                    )
                ]

        def current() -> str | None:
            return self.get(admin, "/api/v2/modules/MOD-DEFAULT")["current_release_id"]

        detail_url = "/api/v2/releases/" + draft["id"]
        self.assertEqual(self.get(admin, detail_url)["status"], "candidate")
        self.assertIsNone(current())
        self.assertEqual(frozen_ids(), [])

        fresh_reviewer = self.login(reviewer_name)
        approved = self.business(fresh_reviewer, "release.publish", {**reference, "background": True})
        self.assertEqual(self._run_release_job(actor, approved["job_id"]), "completed")
        frozen = self.get(admin, detail_url)
        self.assertEqual((frozen["status"], current()), ("published", draft["id"]))
        self.assertEqual(frozen["reviewed_by"], self.identity(fresh_reviewer).user_id)
        self.assertIn(asset["id"], frozen_ids())

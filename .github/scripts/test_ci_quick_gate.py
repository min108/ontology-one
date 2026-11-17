"""R3.1: commit markers may add acceptance, never bypass the quick tier."""

import copy
import fnmatch
import unittest
from pathlib import Path
from typing import Any

import yaml


def check_quick_tier(workflow: dict[str, Any]) -> None:
    foundation = workflow["jobs"]["foundation"]
    assert "if" not in foundation, "The quick-tier job must run for every workflow trigger"
    assert not foundation.get("continue-on-error"), "The quick-tier job must propagate failures"
    required = {
        "Code checks": ("ruff check ", "npm --prefix frontend test -- --run", "node --test e2e/*.test.mjs"),
        "Verify frozen formatting history independently": ("python scripts/checks/check_historical_format.py",),
        "Backend and pipeline tests": (
            "python -m unittest discover -s tests -v",
            "python -m unittest discover -s .github/scripts",
        ),
        "Existing regressions and synthetic model evaluation": ("for name in restore_v020",),
        "Frontend build": ("npm --prefix frontend run build",),
        "Offline source and artifact checks": ("python scripts/checks/check_offline.py --require-dist",),
    }
    steps = {step.get("name"): step for step in foundation["steps"]}
    assert required.keys() <= steps.keys(), "A required quick-tier step was removed"
    for name, commands in required.items():
        assert "if" not in steps[name], f"Quick-tier step {name} must not be conditional"
        assert not steps[name].get("continue-on-error"), f"Quick-tier step {name} must retain failures"
        lines = [line.strip() for line in str(steps[name].get("run", "")).splitlines()]
        for command in commands:
            assert any(line.startswith(command) for line in lines), f"Quick-tier command missing: {command}"


def check_artifact_aggregation(workflow: dict[str, Any]) -> None:
    producers = {
        name: [
            step["with"]["name"] for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")
        ]
        for name, job in workflow["jobs"].items()
        if name != "summary"
    }
    names = [name for uploads in producers.values() for name in uploads]
    assert len(names) == len(set(names)), "Concurrent jobs must not upload the same artifact name"
    summary = workflow["jobs"]["summary"]
    assert set(summary["needs"]) == producers.keys(), "The summary must wait for every producer"
    merge = next(step for step in summary["steps"] if "actions/upload-artifact/merge@" in step.get("uses", ""))
    assert all(fnmatch.fnmatchcase(name, merge["with"]["pattern"]) for name in names)
    assert merge["with"]["delete-merged"] is True, "Only one final summary artifact may remain"
    assert merge["with"]["name"] not in names, "The summary must not overwrite a producer"


class QuickTierContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.safe_load((Path(__file__).parents[1] / "workflows/ci.yml").read_text())

    def test_current_workflow_preserves_the_quick_tier(self) -> None:
        check_quick_tier(self.workflow)

    def test_marker_based_job_skip_is_rejected(self) -> None:
        for marker in ("[R3-VISUAL]", "[R3-REMAINING]", "[R3-INTEGRATION]"):
            with self.subTest(marker=marker):
                changed = copy.deepcopy(self.workflow)
                changed["jobs"]["foundation"]["if"] = f"!contains(github.event.head_commit.message, '{marker}')"
                with self.assertRaisesRegex(AssertionError, "every workflow trigger"):
                    check_quick_tier(changed)

    def test_missing_conditional_and_nonblocking_checks_are_rejected(self) -> None:
        for mode in ("missing", "conditional", "nonblocking"):
            with self.subTest(mode=mode):
                changed = copy.deepcopy(self.workflow)
                steps = changed["jobs"]["foundation"]["steps"]
                step = next(item for item in steps if item.get("name") == "Backend and pipeline tests")
                if mode == "missing":
                    steps.remove(step)
                elif mode == "conditional":
                    step["if"] = "false"
                else:
                    step["continue-on-error"] = True
                with self.assertRaises(AssertionError):
                    check_quick_tier(changed)

    def test_removed_commands_and_job_failure_suppression_are_rejected(self) -> None:
        for mode in ("missing_frontend_tests", "empty_commands", "nonblocking_job"):
            with self.subTest(mode=mode):
                changed = copy.deepcopy(self.workflow)
                job = changed["jobs"]["foundation"]
                if mode == "nonblocking_job":
                    job["continue-on-error"] = True
                elif mode == "empty_commands":
                    for step in job["steps"]:
                        if "run" in step:
                            step["run"] = "true"
                else:
                    step = next(step for step in job["steps"] if step.get("name") == "Code checks")
                    step["run"] = step["run"].replace("npm --prefix frontend test -- --run", "true")
                with self.assertRaises(AssertionError):
                    check_quick_tier(changed)

    def test_current_artifacts_merge_without_collisions(self) -> None:
        check_artifact_aggregation(self.workflow)

    def test_concurrent_same_name_artifact_upload_is_rejected(self) -> None:
        changed = copy.deepcopy(self.workflow)
        for job in changed["jobs"].values():
            for step in job["steps"]:
                if step.get("uses", "").startswith("actions/upload-artifact@"):
                    step["with"]["name"] = "r3-ci-summary-${{ github.run_id }}"
        with self.assertRaisesRegex(AssertionError, "same artifact name"):
            check_artifact_aggregation(changed)

    def test_r31_followup_keeps_only_failed_scope_and_unexecuted_offline(self) -> None:
        steps = {step.get("name"): step for step in self.workflow["jobs"]["r3-remaining"]["steps"]}
        for name in (
            "Recheck only the failed SDK background-task UI case",
            "Previously unexecuted administration operations",
            "Previously unexecuted R2 mainline",
            "Previously unexecuted reviewed visual comparison",
            "Previously unexecuted R3 actions and structure",
        ):
            self.assertIn("env.R31_RECHECK_ONLY != 'true'", steps[name]["if"])
        failed = steps["R3.1 recheck the failed cross-role review case"]
        self.assertIn("env.R31_RECHECK_ONLY == 'true'", failed["if"])
        self.assertIn("--grep 'R3 A9 cross-role'", failed["run"])
        self.assertNotIn("R31_RECHECK_ONLY", steps["Prepare previously unexecuted offline acceptance"]["if"])


if __name__ == "__main__":
    unittest.main()

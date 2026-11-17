"""R3.1: commit markers may add acceptance, never bypass the quick tier."""

import copy
import unittest
from pathlib import Path
from typing import Any

import yaml


def check_quick_tier(workflow: dict[str, Any]) -> None:
    foundation = workflow["jobs"]["foundation"]
    assert "if" not in foundation, "The quick-tier job must run for every workflow trigger"
    required = {"Code checks", "Backend and pipeline tests", "Frontend build", "Offline source and artifact checks"}
    steps = {step.get("name"): step for step in foundation["steps"]}
    assert required <= steps.keys(), "A required quick-tier step was removed"
    for name in required:
        assert "if" not in steps[name], f"Quick-tier step {name} must not be conditional"
        assert not steps[name].get("continue-on-error"), f"Quick-tier step {name} must retain failures"


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


if __name__ == "__main__":
    unittest.main()

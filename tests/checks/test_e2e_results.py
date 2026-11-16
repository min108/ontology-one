"""Aggregate green counts cannot hide missing projects, retries or resource leaks."""

import copy
import unittest

from e2e.check_results import SCOPES, validate_cleanup, validate_report


def report() -> dict:
    test = {
        "projectName": "home",
        "expectedStatus": "passed",
        "status": "expected",
        "results": [{"status": "passed", "retry": 0, "errors": []}],
    }
    return {
        "suites": [{"specs": [{"id": "case-1", "ok": True, "tests": [test]}]}],
        "stats": {"expected": 1, "unexpected": 0, "flaky": 0, "skipped": 0},
    }


class AcceptanceResultsTests(unittest.TestCase):
    def test_exact_projects_are_checked_independently_of_total_count(self) -> None:
        self.assertEqual(validate_report(report(), {"home": 1}), {"home": 1})
        with self.assertRaisesRegex(ValueError, "inventory"):
            validate_report(report(), {"identity": 1})

    def test_retried_pass_and_expected_failure_are_not_acceptance(self) -> None:
        for key, value in (("expectedStatus", "failed"), ("status", "flaky")):
            data = report()
            data["suites"][0]["specs"][0]["tests"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_report(data, {"home": 1})
        data = report()
        data["suites"][0]["specs"][0]["tests"][0]["results"][0]["retry"] = 1
        with self.assertRaises(ValueError):
            validate_report(data, {"home": 1})

    def test_duplicate_id_cannot_replace_a_missing_test(self) -> None:
        data = report()
        data["suites"][0]["specs"].append(copy.deepcopy(data["suites"][0]["specs"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_report(data, {"home": 2})

    def test_zero_summary_cannot_hide_skips_or_report_errors(self) -> None:
        for change in (
            {"errors": [{"message": "setup failed"}]},
            {"stats": {"expected": 0, "unexpected": 0, "flaky": 0, "skipped": 0}},
        ):
            data = report() | change
            with self.assertRaises(ValueError):
                validate_report(data, {"home": 1})

    def test_cleanup_is_complete_exact_and_private(self) -> None:
        namespace = "e2e_r1_" + "a" * 24
        names = [namespace + "_control", *[namespace + "_t_" + value * 24 for value in "bcd"]]
        data = {
            "namespace": namespace,
            "created_databases": names,
            "cleaned_databases": names,
            "services_stopped": True,
            "auth_state_removed": True,
        }
        validate_cleanup(data)
        for change in (
            {"cleaned_databases": names[:-1]},
            {"services_stopped": False},
            {"auth_state_removed": False},
            {"created_databases": [*names, "production"], "cleaned_databases": [*names, "production"]},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_cleanup(data | change)

    def test_final_inventories_are_explicit_not_the_historical_90(self) -> None:
        self.assertEqual(
            {scope: sum(projects.values()) for scope, projects in SCOPES.items()},
            {
                "e2e-legacy-ci": 75,
                "e2e-legacy-replacements-ci": 5,
                "e2e-foundation-ci": 106,
                "e2e-admin-ci": 15,
                "e2e-visual-ci": 9,
            },
        )

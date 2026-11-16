"""A green summary cannot hide missing cases or an enlarged platform skip set."""

import unittest

from scripts.checks.check_regression_results import baseline_results, parse_results, validate_results


def report(*lines: str, count: int | None = None, ending: str = "OK") -> str:
    total = len(lines) if count is None else count
    return "\n".join([*lines, f"Ran {total} tests in 1.000s", ending, ""])


class RegressionResultsTests(unittest.TestCase):
    def test_async_diagnostic_does_not_drop_the_case(self) -> None:
        raw = report(
            "test_sdk (test_sdk.SDK.test_sdk) ... Executing <Task pending> took 0.5 seconds\nok",
            "test_native (test_native.Native.test_native) ... skipped 'Windows only'",
            ending="OK (skipped=1)",
        )
        self.assertEqual(
            parse_results(raw),
            {"test_sdk.SDK.test_sdk::test_sdk": "ok", "test_native.Native.test_native::test_native": "skipped"},
        )

    def test_multiline_method_description_is_supported(self) -> None:
        raw = report("test_one (sample.Tests.test_one)\nPreserves all old records ... ok")
        self.assertEqual(parse_results(raw), {"sample.Tests.test_one::test_one": "ok"})

    def test_duplicate_records_cannot_replace_a_missing_case(self) -> None:
        line = "test_one (sample.Tests.test_one) ... ok"
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            parse_results(report(line, line))

    def test_next_test_cannot_hide_an_unfinished_record(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            parse_results(report("test_one (sample.Tests.test_one) ...", "test_two (sample.Tests.test_two) ... ok"))

    def test_truncated_log_is_not_a_success(self) -> None:
        for raw in ("", "test_one (sample.Tests.test_one) ... ok", "test_one (sample.Tests.test_one) ..."):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_results(raw)

    def test_count_and_skip_summary_must_match_records(self) -> None:
        line = "test_one (sample.Tests.test_one) ... ok"
        for raw in (report(line, count=2), report(line, ending="OK (skipped=1)")):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_results(raw)

    def test_failures_and_expected_failures_are_not_passes(self) -> None:
        for status, ending in (("FAIL", "FAILED (failures=1)"), ("expected failure", "OK (expected failures=1)")):
            with self.subTest(status=status), self.assertRaises(ValueError):
                parse_results(report(f"test_one (sample.Tests.test_one) ... {status}", ending=ending))

    def test_combined_runs_are_not_one_complete_regression(self) -> None:
        raw = report("test_one (sample.Tests.test_one) ... ok") + report("test_two (sample.Tests.test_two) ... ok")
        with self.assertRaises(ValueError):
            parse_results(raw)

    def test_missing_baseline_or_changed_status_fails(self) -> None:
        baseline = {"old": "ok", "native": "skipped"}
        for records in ({"native": "skipped"}, {"old": "skipped", "native": "skipped"}):
            with self.subTest(records=records), self.assertRaises(ValueError):
                validate_results(records, baseline)

    def test_new_skips_are_rejected_even_if_old_cases_match(self) -> None:
        with self.assertRaisesRegex(ValueError, "skip"):
            validate_results({"old": "ok", "native": "skipped", "new": "skipped"}, {"old": "ok", "native": "skipped"})

    def test_additional_passes_are_counted_without_losing_old_cases(self) -> None:
        summary = validate_results({"old": "ok", "native": "skipped", "new": "ok"}, {"old": "ok", "native": "skipped"})
        self.assertEqual(summary, {"total": 3, "passed": 2, "skipped": 1, "baseline": 2, "added": 1})

    def test_frozen_baseline_includes_the_documented_missing_sdk_case(self) -> None:
        baseline = baseline_results()
        self.assertEqual(len(baseline), 489)
        self.assertEqual(sum(value == "skipped" for value in baseline.values()), 39)
        self.assertEqual(
            baseline[
                "test_agent_sdk.AgentOfficialSDKTests."
                "test_agent_only_exposes_fixed_selected_tool_and_calls_authenticated_bridge::"
                "test_agent_only_exposes_fixed_selected_tool_and_calls_authenticated_bridge"
            ],
            "ok",
        )

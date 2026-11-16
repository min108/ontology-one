"""Historical formatter exit success cannot substitute for actual AST equivalence."""

import unittest

from scripts.checks.check_historical_format import compare_sources


class HistoricalFormatTests(unittest.TestCase):
    def test_docstring_whitespace_only_is_explicitly_distinguished(self) -> None:
        before = {"backend/a.py": '"""first   line"""\nx=1\n', "tests/b.py": "x=2\n"}
        after = {"backend/a.py": '"""first line"""\nx = 1\n', "tests/b.py": "x = 2\n"}
        report = compare_sources(before, after, set(before), expected_files=2, expected_changed=2, expected_strict=1)
        self.assertEqual(report["normalized_equal"], 2)
        self.assertEqual(report["strict_equal"], 1)
        self.assertEqual(report["different"], 0)

    def test_actual_behavior_difference_fails_even_with_plausible_counts(self) -> None:
        with self.assertRaisesRegex(ValueError, "AST"):
            compare_sources(
                {"a.py": "x=1"}, {"a.py": "x=2"}, {"a.py"}, expected_files=1, expected_changed=1, expected_strict=0
            )

    def test_runtime_string_whitespace_is_not_normalized(self) -> None:
        with self.assertRaisesRegex(ValueError, "AST"):
            compare_sources(
                {"a.py": 'x="a  b"'},
                {"a.py": 'x="a b"'},
                {"a.py"},
                expected_files=1,
                expected_changed=1,
                expected_strict=0,
            )

    def test_added_and_removed_python_files_cannot_disappear_from_inventory(self) -> None:
        with self.assertRaisesRegex(ValueError, "inventory"):
            compare_sources({"a.py": "x=1"}, {"b.py": "x=1"}, {"a.py", "b.py"})

    def test_non_python_or_out_of_scope_diff_is_rejected(self) -> None:
        for changed in ({"a.py", "VERSION"}, {"other.py"}):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "diff"):
                compare_sources(
                    {"a.py": "x=1"},
                    {"a.py": "x = 1"},
                    changed,
                    expected_files=1,
                    expected_changed=len(changed),
                    expected_strict=1,
                )

    def test_truncated_inventory_cannot_claim_the_frozen_151(self) -> None:
        with self.assertRaisesRegex(ValueError, "count"):
            compare_sources({"a.py": "x=1"}, {"a.py": "x = 1"}, {"a.py"})

    def test_changed_paths_must_match_observed_content_changes(self) -> None:
        with self.assertRaisesRegex(ValueError, "diff"):
            compare_sources(
                {"a.py": "x=1"}, {"a.py": "x = 1"}, set(), expected_files=1, expected_changed=0, expected_strict=1
            )

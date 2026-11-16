"""Validate exact final acceptance inventories, source versions and resource cleanup."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

if __package__:
    from .harness_fixture import validate_harness_result
    from .visual_baselines import states, validate_state
else:
    from harness_fixture import validate_harness_result
    from visual_baselines import states, validate_state

ROOT = Path(__file__).resolve().parents[1]
LEGACY = "1359118dd672431dcd0cde2eb81f2b0f994eef37"
BROWSER = "159.0.8154.6"
SCOPES = {
    "e2e-legacy-ci": {"": 75},
    "e2e-legacy-replacements-ci": {"legacy-replacements": 5},
    "e2e-foundation-ci": {"foundation-pages": 89, "foundation-home": 4, "foundation-identity": 12, "harness-sdk": 1},
    "e2e-admin-ci": {"foundation-admin": 15},
    "e2e-visual-ci": {"foundation-visual": 9},
}


def validate_report(report: dict, expected: dict[str, int]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    identities = set()

    def visit(suite: dict) -> None:
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                identity = (test.get("projectName"), spec.get("id"))
                if not spec.get("id") or identity in identities:
                    raise ValueError("Duplicate or missing test identity")
                identities.add(identity)
                results = test.get("results", [])
                if (
                    spec.get("ok") is not True
                    or test.get("expectedStatus") != "passed"
                    or test.get("status") != "expected"
                    or len(results) != 1
                    or results[0].get("status") != "passed"
                    or results[0].get("retry") != 0
                    or results[0].get("errors")
                ):
                    raise ValueError("Every exact test must pass once without retry, skip or expected failure")
                counts[test["projectName"]] += 1
        for child in suite.get("suites", []):
            visit(child)

    visit(report)
    if dict(counts) != expected:
        raise ValueError(f"Project inventory differs: {dict(counts)} != {expected}")
    stats = {key: report.get("stats", {}).get(key) for key in ("expected", "unexpected", "flaky", "skipped")}
    if (
        any(type(value) is not int for value in stats.values())
        or stats != {"expected": sum(expected.values()), "unexpected": 0, "flaky": 0, "skipped": 0}
        or report.get("errors")
    ):
        raise ValueError("Browser summary differs from exact complete test results")
    return dict(counts)


def validate_cleanup(runner: dict) -> None:
    namespace = runner.get("namespace", "")
    if not re.fullmatch(r"e2e_r1_[0-9a-f]{24}", namespace):
        raise ValueError("Missing isolated random namespace")
    created, cleaned = runner.get("created_databases", []), runner.get("cleaned_databases", [])
    if (
        len(created) < 4
        or len(created) != len(set(created))
        or len(cleaned) != len(set(cleaned))
        or set(created) != set(cleaned)
        or any(not re.fullmatch(re.escape(namespace) + r"(?:_control|_t_[0-9a-f]{24})?", name) for name in created)
        or runner.get("services_stopped") is not True
        or runner.get("auth_state_removed") is not True
    ):
        raise ValueError("Incomplete database/service/private authentication cleanup")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=ROOT / "work/evidence/r1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    policy = json.loads((ROOT / "e2e/visual-policy.json").read_text())
    if policy.get("mode") not in {"capture", "compare"}:
        raise ValueError("Unknown visual policy")
    summaries = {}
    for scope, expected in SCOPES.items():
        directory = args.evidence / scope
        runner = json.loads((directory / "runner.json").read_text())
        environment = json.loads((directory / "environment.json").read_text())
        report = json.loads((directory / "results.json").read_text())
        old = scope in {"e2e-legacy-ci", "e2e-legacy-replacements-ci"}
        source = LEGACY if old else commit
        kind = (
            "baseline-capture-not-acceptance"
            if scope == "e2e-visual-ci" and policy["mode"] == "capture"
            else "acceptance"
        )
        if (
            runner.get("status") != "passed"
            or runner.get("target") != ("legacy" if old else "foundation")
            or environment.get("target") != ("legacy" if old else "foundation")
            or runner.get("source_commit") != source
            or runner.get("fixture_commit") != LEGACY
            or runner.get("result_kind") != kind
            or environment.get("sourceCommit") != source
            or environment.get("fixtureCommit") != LEGACY
            or environment.get("browserVersion") != BROWSER
        ):
            raise ValueError(f"{scope}: failed, wrong-source, noncanonical or mislabeled evidence")
        projects = validate_report(report, expected)
        validate_cleanup(runner)
        if scope == "e2e-foundation-ci":
            validate_harness_result(directory / "harness-result.json")
            if runner.get("harness", {}).get("observed_processes_reaped") is not True:
                raise ValueError("Actual SDK process cleanup is incomplete")
        if scope == "e2e-visual-ci":
            expected_paths = {f"visual/{layout}/{state}/actual.png" for layout, state in states().values()}
            actual_paths = {
                path.relative_to(directory).as_posix() for path in (directory / "visual").rglob("actual.png")
            }
            if actual_paths != expected_paths:
                raise ValueError("Visual scope must contain exactly 63 canonical screenshot states")
            for stem, (layout, state) in states().items():
                validate_state(directory / "visual" / layout / state, stem, commit, policy["mode"])
        if runner.get("browser_tests") != {
            key: report["stats"][key] for key in ("expected", "unexpected", "flaky", "skipped")
        }:
            raise ValueError(f"{scope}: runner and Playwright counts differ")
        summaries[scope] = {
            "source_commit": source,
            "projects": projects,
            "passed": sum(projects.values()),
            "cleaned_databases": len(runner["cleaned_databases"]),
            "result_kind": kind,
        }
    result = {
        "commit": commit,
        "browser": BROWSER,
        "policy": policy,
        "scopes": summaries,
        "total_tests": sum(sum(projects.values()) for projects in SCOPES.values()),
        "visual_images": 63,
        "visual_packages": 33,
        "foundation_gates_passed": policy["mode"] == "compare",
        "final_acceptance": "Requires this report plus the separate exact-SHA offline CI job success",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Validate one complete Linux unittest run against the unchanged 0.7.0 baseline.

The historical list missed one SDK result split by asyncio diagnostic output.
Keep the original file intact and explicitly add only that documented case.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "docs/blueprint/rounds/r1-baseline/baseline-tests.txt"
BASELINE_SHA256 = "5ee027797eb2e456261daea8cd19ad6a00399952307cde16c4799355faaabe55"
MISSING_SDK_CASE = (
    "test_agent_sdk.AgentOfficialSDKTests."
    "test_agent_only_exposes_fixed_selected_tool_and_calls_authenticated_bridge::"
    "test_agent_only_exposes_fixed_selected_tool_and_calls_authenticated_bridge"
)
START = re.compile(r"^((?:test\w*|runTest)) \(([^\s)]+)\)(?:\s+\.\.\.\s*(.*))?$")
STATUS = re.compile(r"^(ok|skipped)(?:\s|$)")


def baseline_results() -> dict[str, str]:
    source = BASELINE.read_bytes()
    if hashlib.sha256(source).hexdigest() != BASELINE_SHA256:
        raise ValueError("Historical baseline file changed")
    rows = dict(line.rsplit(" ", 1) for line in source.decode().splitlines())
    if len(rows) != 488 or MISSING_SDK_CASE in rows:
        raise ValueError("Unexpected historical baseline inventory")
    rows[MISSING_SDK_CASE] = "ok"
    return rows


def parse_results(source: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    pending: str | None = None
    for line in source.splitlines():
        start = START.match(line)
        if start:
            if pending is not None:
                raise ValueError(f"Unfinished result: {pending}")
            pending = start[2] + "::" + start[1]
            remainder = start[3] or ""
        else:
            # Verbose unittest may put the method's docstring on the next line.
            remainder = line.rsplit(" ... ", 1)[-1]
        status = STATUS.match(remainder)
        if pending and status:
            if pending in rows:
                raise ValueError(f"Duplicate result: {pending}")
            rows[pending] = status[1]
            pending = None
    if pending is not None:
        raise ValueError(f"Unfinished result: {pending}")
    totals = re.findall(r"^Ran (\d+) tests? in .+$", source, re.MULTILINE)
    endings = re.findall(r"^(?:OK|FAILED)(?: .*)?$", source, re.MULTILINE)
    if len(totals) != 1 or len(endings) != 1 or int(totals[0]) != len(rows) or not rows:
        raise ValueError("Expected one complete run with matching reported and parsed counts")
    success = re.fullmatch(r"OK(?: \(skipped=(\d+)\))?", endings[0])
    if not success:
        raise ValueError("Run did not finish with an unconditional success summary")
    if int(success[1] or 0) != sum(value == "skipped" for value in rows.values()):
        raise ValueError("Reported skip count does not match parsed records")
    return rows


def validate_results(records: dict[str, str], baseline: dict[str, str]) -> dict[str, int]:
    differences = sorted(key for key, status in baseline.items() if records.get(key) != status)
    if differences:
        raise ValueError("Missing or changed baseline results: " + ", ".join(differences))
    if any(value not in {"ok", "skipped"} for value in records.values()):
        raise ValueError("Non-passing result")
    original_skips = {key for key, status in baseline.items() if status == "skipped"}
    actual_skips = {key for key, status in records.items() if status == "skipped"}
    if original_skips != actual_skips:
        raise ValueError("Linux skip set differs from the frozen Windows-native skip set")
    return {
        "total": len(records),
        "passed": len(records) - len(actual_skips),
        "skipped": len(actual_skips),
        "baseline": len(baseline),
        "added": len(records) - len(baseline),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path, help="Write verified summary and every case result as JSON")
    args = parser.parse_args()
    try:
        records = parse_results(args.log.read_text())
        summary = validate_results(records, baseline_results())
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps({"summary": summary, "results": dict(sorted(records.items()))}, indent=2) + "\n"
            )
        print(json.dumps(summary, sort_keys=True))
    except (OSError, ValueError) as error:
        print(f"Regression results rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

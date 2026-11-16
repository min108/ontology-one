"""Keep the legacy inventory reviewed and new imports behind declaration aliases."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*(?:\(\s*)?)["']([^"']+)["']""")


def check_tree(root: Path, baseline: dict) -> list[str]:
    legacy = root / "legacy"
    known = set(baseline["files"])
    retired = set(baseline["retired"])
    current = {path.relative_to(legacy).as_posix() for path in legacy.rglob("*") if path.is_file()}
    errors = []
    for name in sorted(current - known):
        errors.append(f"legacy/{name}: new exempt file is not allowed")
    for name in sorted((known - retired) - current):
        errors.append(f"legacy/{name}: deletion has not been reviewed")
    for name in sorted(retired & current):
        errors.append(f"legacy/{name}: planned retirement is incomplete")
    if retired - known:
        errors.append("Retirement registry contains an unknown legacy path")
    for path in root.rglob("*"):
        if path.suffix not in {".ts", ".tsx"} or path.is_relative_to(legacy):
            continue
        for specifier in IMPORT.findall(path.read_text()):
            if specifier.startswith(".") and (path.parent / specifier).resolve().is_relative_to(legacy.resolve()):
                errors.append(f"{path.relative_to(root)}: import legacy through @legacy/* declarations")
            if specifier.startswith(("/src/legacy/", "src/legacy/")):
                errors.append(f"{path.relative_to(root)}: direct legacy source import is forbidden")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "frontend/src")
    args = parser.parse_args()
    baseline = json.loads(Path(__file__).with_name("legacy_baseline.json").read_text())
    errors = check_tree(args.root, baseline)
    for error in errors:
        print(error)
    print(f"Legacy inventory/type boundary: {len(errors)} violation(s)")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())

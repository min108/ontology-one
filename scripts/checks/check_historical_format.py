"""Prove the frozen R1-1 formatting commit from Git objects, failing on every mismatch.

The historical ast_equiv.py prints DIFF but does not fail its process. This gate
independently compares both inventories and ASTs rather than trusting that exit code.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BEFORE = "50e160bbb49cc7be25446e23de088e563cba68c3"
AFTER = "353b9b2784e84b006394c7b982a2002477676bb3"
SCOPE = ("backend", "scripts", "ops", "tests", ".github/scripts")


def normalized_ast(source: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            statement = node.body[0]
            if (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                statement.value.value = " ".join(statement.value.value.split())
    return ast.dump(tree, include_attributes=False)


def compare_sources(
    before: dict[str, str],
    after: dict[str, str],
    changed_paths: set[str],
    *,
    expected_files: int = 151,
    expected_changed: int = 150,
    expected_strict: int = 150,
) -> dict[str, object]:
    if before.keys() != after.keys():
        raise ValueError("Historical Python inventory changed; additions/removals are not formatting")
    if any(not path.endswith(".py") or path not in before for path in changed_paths):
        raise ValueError("Historical diff includes non-Python or out-of-scope files")
    actual_changes = {path for path in before if before[path] != after[path]}
    if actual_changes != changed_paths:
        raise ValueError("Historical diff inventory differs from observed source changes")
    if len(before) != expected_files or len(actual_changes) != expected_changed:
        raise ValueError("Historical file count differs from the frozen 151 files / 150 changes")
    files = []
    for path, original in sorted(before.items()):
        current = after[path]
        strict = ast.dump(ast.parse(original), include_attributes=False) == ast.dump(
            ast.parse(current), include_attributes=False
        )
        normalized = strict or normalized_ast(original) == normalized_ast(current)
        files.append(
            {
                "path": path,
                "before_sha256": hashlib.sha256(original.encode()).hexdigest(),
                "after_sha256": hashlib.sha256(current.encode()).hexdigest(),
                "changed": path in actual_changes,
                "strict_equal": strict,
                "normalized_equal": normalized,
            }
        )
    strict_equal = sum(item["strict_equal"] for item in files)
    normalized_equal = sum(item["normalized_equal"] for item in files)
    different = len(files) - normalized_equal
    if different:
        raise ValueError(
            "Historical AST difference: " + ", ".join(item["path"] for item in files if not item["normalized_equal"])
        )
    if strict_equal != expected_strict or normalized_equal != expected_files:
        raise ValueError("Historical AST counts differ from the frozen strict/normalized proof")
    return {
        "files": len(files),
        "changed_files": len(actual_changes),
        "strict_equal": strict_equal,
        "normalized_equal": normalized_equal,
        "different": different,
        "python_only_diff": True,
        "normalization": "Only module/class/function docstring whitespace; runtime string values remain exact",
        "inventory": files,
    }


def git(*arguments: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *arguments])


def sources(commit: str) -> dict[str, str]:
    with tarfile.open(fileobj=io.BytesIO(git("archive", commit, "--", *SCOPE))) as archive:
        result = {}
        for entry in archive.getmembers():
            if entry.isfile() and entry.name.endswith(".py"):
                stream = archive.extractfile(entry)
                if stream is None:
                    raise ValueError("Missing historical source blob")
                result[entry.name] = stream.read().decode("utf-8")
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if git("rev-parse", AFTER + "^").decode().strip() != BEFORE:
        raise ValueError("Frozen formatting parent differs")
    changed = set(git("diff", "--name-only", "--no-renames", "-z", BEFORE, AFTER).decode().rstrip("\0").split("\0"))
    result = compare_sources(sources(BEFORE), sources(AFTER), changed)
    result.update(before=BEFORE, after=AFTER, scope=SCOPE)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "inventory"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

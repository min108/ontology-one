"""Reject new business DDL, migration gaps and migrations without named tests."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = {
    "backend/sources.py": 6,
    "backend/db.py": 13,
    "backend/asset_import.py": 1,
    "backend/demo.py": 1,
    "backend/special_projects.py": 1,
    "backend/provenance.py": 6,
    "backend/datasets.py": 5,
    "backend/jobs.py": 1,
    "backend/skills.py": 3,
    "backend/federation.py": 1,
    "backend/security/store.py": 6,
    "backend/security/applications.py": 2,
}


def check(root: Path = ROOT) -> list[str]:
    errors = []
    for path in (root / "backend").rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("backend/infra/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        count = sum(
            len(re.findall(r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s", node.value, re.I))
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
        if count > BASELINE.get(relative, 0):
            errors.append(relative + "：业务模块新增建表语句，须使用迁移。")
    for kind in ("control", "tenant"):
        paths = sorted(p for p in (root / "migrations" / kind).glob("*.py") if p.name != "__init__.py")
        if not paths:
            errors.append("migrations/" + kind + "：迁移序列缺少基线文件。")
        for index, path in enumerate(paths, 1):
            if not re.fullmatch(rf"{index:04d}_[a-z][a-z0-9_]*\.py", path.name):
                errors.append(str(path.relative_to(root)) + "：迁移编号必须连续且唯一。")
            tree = ast.parse(path.read_text(encoding="utf-8"))
            test = next(
                (
                    node.value.value
                    for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "TEST" for target in node.targets)
                    and isinstance(node.value, ast.Constant)
                ),
                None,
            )
            if not isinstance(test, str) or ":" not in test:
                errors.append(str(path.relative_to(root)) + "：迁移未登记测试。")
                continue
            filename, method = test.split(":", 1)
            test_path = root / filename
            if (
                not filename.startswith("tests/migrations/")
                or not test_path.is_file()
                or not method.startswith("test_")
            ):
                errors.append(str(path.relative_to(root)) + "：迁移测试不存在。")
                continue
            methods = {
                node.name for node in ast.walk(ast.parse(test_path.read_text())) if isinstance(node, ast.FunctionDef)
            }
            if method not in methods:
                errors.append(str(path.relative_to(root)) + "：迁移测试用例不存在。")
    return errors


def main() -> int:
    errors = check()
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print("迁移检查通过；业务建表基线46处，序号与测试登记完整。")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())

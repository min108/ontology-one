"""The frozen legacy contract is captured before the R1 route relocation."""

import ast
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import main

BASE = Path(__file__).resolve().parents[2]
CONTRACT = json.loads(Path(__file__).with_name("legacy_contract.json").read_text())


class AssemblyTests(unittest.TestCase):
    def test_legacy_route_set(self) -> None:
        actual = sorted(
            [method.upper(), path]
            for path, methods in main.app.openapi()["paths"].items()
            for method in methods
            if path.startswith("/api/") and not path.startswith("/api/v2/")
        )
        self.assertEqual(actual, CONTRACT["routes"])

    def test_no_inline_handlers_or_sql(self) -> None:
        tree = ast.parse((BASE / "backend/main.py").read_text())
        self.assertEqual(
            [node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))],
            ["lifespan"],
        )
        self.assertFalse(
            any(
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute"
                for node in ast.walk(tree)
            )
        )

    def test_relocated_functions_are_ast_equivalent(self) -> None:
        from backend.api.v1 import core

        tree = ast.parse(Path(core.__file__).read_text())
        actual = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in CONTRACT["functions"]:
                node.decorator_list = []
                for child in ast.walk(node):
                    if isinstance(child, ast.ImportFrom) and child.level:
                        child.level -= 2
                actual[node.name] = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
                self.assertIs(getattr(main, node.name), getattr(core, node.name))
        # R2 retains the frozen R1 hashes and pins the three authorized changes exactly.
        self.assertEqual(actual, {**CONTRACT["functions"], **CONTRACT["r2_functions"]})

    def test_legacy_connection_patch_is_forwarded(self) -> None:
        from backend.api.v1 import core

        with patch.object(main, "connect", return_value="connection"):
            self.assertEqual(core.connect(), "connection")

    def test_connection_uses_original_factory(self) -> None:
        from backend import db
        from backend.api.v1 import core

        self.assertIs(main.connect, db.connect)
        with patch.object(main, "connect", return_value="normal") as factory:
            self.assertEqual(core.connect(), "normal")
            factory.assert_called_once_with()

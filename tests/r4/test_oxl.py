"""R4-A A5 specification table: 30 accepted cases and 30 rejected cases.

The tables precede implementation and are independently named unittest cases.
"""

import ast
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from backend.kernel import oxl

PROPERTIES = {
    "amount": {"name": "合同金额", "unit": "元"},
    "paid": {"name": "已付款", "unit": "元"},
    "ready": {"name": "材料齐备"},
    "kind": {"name": "合同类型"},
    "signed": {"name": "签订日期"},
}
RELATIONS = {"payments": {"amount": {"name": "付款金额"}, "ready": {"name": "已确认"}}}

# Input → exact expected value; each row is a separate discovery-visible test.
ACCEPTED = [
    ("number", "123.5", {}, 123.5),
    ("arithmetic", "amount - paid * 2 / 4 + 1", {"amount": 100, "paid": 40}, 81),
    ("negative", "-amount", {"amount": 10}, -10),
    ("comparison", "amount >= 100 and amount < 200", {"amount": 100}, True),
    ("chained", "0 < amount <= 100", {"amount": 100}, True),
    ("membership", "kind in ['a', 'b']", {"kind": "b"}, True),
    ("null_arithmetic", "amount + 1", {}, None),
    ("null_compare", "amount == null", {"amount": 1}, None),
    ("and_truth_table", "ready and null", {"ready": False}, False),
    ("or_truth_table", "ready or null", {"ready": True}, True),
    ("not_unknown", "not null", {}, None),
    ("max", "max(0, amount - paid)", {"amount": 100, "paid": 120}, 0),
    ("min", "min(amount, paid)", {"amount": 1, "paid": 2}, 1),
    ("abs", "abs(-10)", {}, 10),
    ("round", "round(12.345, 2)", {}, 12.35),
    ("coalesce", "coalesce(amount, 0)", {}, 0),
    ("is_null", "is_null(amount)", {}, True),
    ("not_null", "not_null(amount)", {"amount": 0}, True),
    ("contains", "contains('采购合同', '合同')", {}, True),
    ("starts_with", "starts_with('合同一', '合同')", {}, True),
    ("len", "len('合同')", {}, 2),
    ("if_lazy", "if(true, 2, 1 / 0)", {}, 2),
    ("if_unknown", "if(null, 2, 3)", {}, None),
    ("days", "days_between('2026-11-01', '2026-11-17')", {}, 16),
    ("today", "today()", {}, "2026-11-18"),
    ("year_month", "year(signed) * 100 + month(signed)", {"signed": "2026-11-17"}, 202611),
    ("aggregate", "sum(payments.amount) + count(payments)", {}, 32),
    ("aggregate_avg", "avg(payments.amount)", {}, 15),
    ("aggregate_filter", "count(payments, ready == true)", {}, 1),
    ("aggregate_extrema", "max(payments.amount) - min(payments.amount)", {}, 10),
]
REJECTED = [
    ("attribute", "amount.real", "syntax"),
    ("dunder", "amount.__class__", "syntax"),
    ("nested_attribute", "payments.amount.real", "syntax"),
    ("bare_projection", "payments.amount", "syntax"),
    ("subscript", "amount[0]", "syntax"),
    ("slice", "amount[1:2]", "syntax"),
    ("list_comprehension", "[x for x in amount]", "syntax"),
    ("set_comprehension", "{x for x in amount}", "syntax"),
    ("dict_comprehension", "{x:x for x in amount}", "syntax"),
    ("generator", "sum(x for x in amount)", "syntax"),
    ("lambda", "lambda: 1", "syntax"),
    ("keyword", "round(amount, n=2)", "syntax"),
    ("starargs", "max(*amount)", "syntax"),
    ("kwargs", "max(**amount)", "syntax"),
    ("import", "__import__('os')", "function"),
    ("method", "kind.upper()", "syntax"),
    ("walrus", "(amount := 1)", "syntax"),
    ("ternary", "1 if true else 2", "syntax"),
    ("power", "2 ** 10", "syntax"),
    ("floor", "2 // 1", "syntax"),
    ("modulo", "2 % 1", "syntax"),
    ("bitwise", "1 | 2", "syntax"),
    ("identity", "amount is null", "syntax"),
    ("tuple", "(1, 2)", "syntax"),
    ("fstring", "f'{amount}'", "syntax"),
    ("unknown_property", "undeclared + 1", "reference"),
    ("unknown_relation", "sum(unknown.amount)", "reference"),
    ("too_long", "1" * 501, "length"),
    ("too_deep", "not " * 12 + "true", "depth"),
    ("too_many_nodes", " or ".join(["0<0"] * 67), "nodes"),
]


class ExpressionSpecifications(unittest.TestCase):
    pass


def accepted_test(expression: str, values: dict, expected: object) -> object:
    def test(self: unittest.TestCase) -> None:
        result = oxl.evaluate(
            expression,
            values,
            properties=PROPERTIES,
            relations=RELATIONS,
            related={"payments": [{"amount": 10, "ready": True}, {"amount": 20, "ready": False}]},
            as_of="2026-11-17T16:30:00Z",
        )
        self.assertEqual(result, expected)
        if isinstance(expected, bool):
            self.assertIs(result, expected)

    return test


def rejected_test(expression: str, code: str) -> object:
    def test(self: unittest.TestCase) -> None:
        with self.assertRaises(oxl.OXLError) as caught:
            oxl.parse(expression, properties=PROPERTIES, relations=RELATIONS)
        self.assertEqual(caught.exception.code, code)

    return test


for name, expression, values, expected in ACCEPTED:
    setattr(ExpressionSpecifications, f"test_accept_{name}", accepted_test(expression, values, expected))
for name, expression, code in REJECTED:
    setattr(ExpressionSpecifications, f"test_reject_{name}", rejected_test(expression, code))


class ExpressionSecurityRegressions(unittest.TestCase):
    def test_entire_three_value_truth_tables(self) -> None:
        for left in (True, False, None):
            for right in (True, False, None):
                with self.subTest(left=left, right=right):
                    values = {"ready": left, "amount": right}
                    expected_and = False if False in (left, right) else None if None in (left, right) else True
                    expected_or = True if True in (left, right) else None if None in (left, right) else False
                    self.assertIs(oxl.evaluate("ready and amount", values, properties=PROPERTIES), expected_and)
                    self.assertIs(oxl.evaluate("ready or amount", values, properties=PROPERTIES), expected_or)
        for value, expected in ((True, False), (False, True), (None, None)):
            self.assertIs(oxl.evaluate("not ready", {"ready": value}, properties=PROPERTIES), expected)

    def test_nonwhitelisted_lexical_forms_and_references(self) -> None:
        for expression in (
            '"double"',
            "'''triple'''",
            "b'bytes'",
            "'a' 'b'",
            "True",
            "None",
            "1j",
            "1 # comment",
            "{1, 2}",
            "{'a': 1}",
            "1 << 2",
            "~1",
            "amount not in [1]",
            "sum(payments.unknown)",
            "count(payments, kind == 'x')",
            "[1,2]",
            "kind in amount",
            "_oxl_if(true,1,2)",
            "_\uff4f\uff58\uff4c_if(true,1,2)",
            "\uff4d\uff41\uff58(1,2)",
        ):
            with self.subTest(expression=expression), self.assertRaises(oxl.OXLError):
                oxl.parse(expression, properties=PROPERTIES, relations=RELATIONS)

    def test_runtime_does_not_coerce_or_execute_objects(self) -> None:
        class Hostile:
            def __bool__(self) -> bool:
                raise AssertionError("must not invoke user object")

            def __add__(self, other: object) -> object:
                raise AssertionError("must not invoke user object")

        for expression, value in (("ready and true", 1), ("amount + 1", Hostile()), ("amount * 2", "large")):
            with self.subTest(expression=expression), self.assertRaises(oxl.OXLError):
                oxl.evaluate(expression, {"ready": value, "amount": value}, properties=PROPERTIES)

    def test_no_dynamic_execution_and_custom_tree(self) -> None:
        source = Path(oxl.__file__).read_text()
        tree = ast.parse(source)
        forbidden = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"eval", "exec", "compile"}
        ]
        self.assertEqual(forbidden, [])
        expression = oxl.parse("amount > 1", properties=PROPERTIES)
        self.assertNotIsInstance(expression.root, ast.AST)
        with self.assertRaises(AssertionError):
            self.assertNotIsInstance(ast.parse("1"), ast.AST)

    def test_beijing_default_and_explicit_date(self) -> None:
        with patch.object(oxl, "_now", return_value=datetime(2026, 11, 17, 16, 30, tzinfo=UTC)):
            self.assertEqual(oxl.evaluate("today()", {}), "2026-11-18")
        self.assertEqual(oxl.evaluate("today()", {}, as_of="2026-11-17"), "2026-11-17")

    def test_missing_aggregate_values_stay_unknown(self) -> None:
        for related in ({}, {"payments": [{"amount": 10}, {}]}):
            self.assertIsNone(oxl.evaluate("sum(payments.amount)", {}, relations=RELATIONS, related=related))
        self.assertEqual(oxl.evaluate("count(payments)", {}, relations=RELATIONS, related={"payments": []}), 0)
        self.assertIsNone(oxl.evaluate("avg(payments.amount)", {}, relations=RELATIONS, related={"payments": []}))

    def test_chinese_sentence_and_unit(self) -> None:
        self.assertEqual(oxl.to_sentence("amount > 200000", properties=PROPERTIES), "合同金额 大于 200000 元")
        sentence = oxl.to_sentence("if(ready, amount, 0)", properties=PROPERTIES)
        self.assertIn("如果", sentence)
        self.assertIn("合同金额", sentence)
        self.assertNotIn("ready", sentence)

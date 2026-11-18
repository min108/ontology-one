"""Bounded expression parser, platform syntax tree and deterministic interpreter.

No Python expression is executed. Callers supply authorized property/relation
schemas explicitly; input values never define which references are permitted.
"""

import ast
import io
import math
import operator
import tokenize
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

type PropertyDeclarations = Mapping[str, dict] | Iterable[str | dict]
type RelationDeclarations = Mapping[str, PropertyDeclarations]
# The business calendar uses current Beijing time, with no host tzdata dependency.
BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")
MAX_ROUND_DIGITS = 100


def _round_digits(value: object) -> int:
    if type(value) is not int:
        raise OXLError("type", "保留小数位数需要整数。")
    if not -MAX_ROUND_DIGITS <= value <= MAX_ROUND_DIGITS:
        raise OXLError("range", "保留位数须在 -100 到 100 之间，请缩小范围。")
    return value


MAX_LENGTH = 500
MAX_NODES = 200
MAX_DEPTH = 12
_FUNCTIONS = {
    "max": (1, 200),
    "min": (1, 200),
    "abs": (1, 1),
    "round": (2, 2),
    "coalesce": (2, 2),
    "is_null": (1, 1),
    "not_null": (1, 1),
    "contains": (2, 2),
    "starts_with": (2, 2),
    "len": (1, 1),
    "if": (3, 3),
    "days_between": (2, 2),
    "today": (0, 0),
    "year": (1, 1),
    "month": (1, 1),
}
_BINARY = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}
_COMPARE = {ast.Eq: "==", ast.NotEq: "!=", ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<=", ast.In: "in"}
_WORDS = {
    "+": "加",
    "-": "减",
    "*": "乘以",
    "/": "除以",
    "==": "等于",
    "!=": "不等于",
    ">": "大于",
    ">=": "大于或等于",
    "<": "小于",
    "<=": "小于或等于",
    "in": "属于",
    "and": "并且",
    "or": "或者",
    "not": "并非",
}
_FUNCTION_WORDS = {
    "max": "最大值",
    "min": "最小值",
    "abs": "绝对值",
    "round": "四舍五入",
    "coalesce": "缺失时取",
    "is_null": "未填写",
    "not_null": "已填写",
    "contains": "包含",
    "starts_with": "开头为",
    "len": "长度",
    "days_between": "相隔天数",
    "today": "核对日期",
    "year": "年份",
    "month": "月份",
    "sum": "合计",
    "count": "数量",
    "avg": "平均值",
}


class OXLError(ValueError):
    """Safe business-facing error, with a stable machine-readable category."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class Node:
    kind: str
    value: object = None
    children: tuple["Node", ...] = ()


@dataclass(frozen=True)
class Expression:
    root: Node
    references: frozenset[str]
    relation_references: frozenset[tuple[str, str | None]]
    properties: dict[str, dict]
    relations: dict[str, dict[str, dict]]


def property_map(properties: PropertyDeclarations) -> dict[str, dict]:
    """Normalize object-type payload properties without deriving them from inputs."""
    if isinstance(properties, Mapping):
        return {key: value if isinstance(value, dict) else {} for key, value in properties.items()}
    result = {}
    for item in properties:
        if isinstance(item, str):
            result[item] = {}
        elif isinstance(item, dict) and isinstance(item.get("key"), str):
            result[item["key"]] = item
        else:
            raise OXLError("reference", "属性声明不完整，请先补齐业务对象的属性。")
    return result


def _tokens(text: str) -> str:
    """Translate only the reserved function spelling `if`, outside string tokens."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError) as error:
        raise OXLError("syntax", "判定式或计算式的写法不正确。") from error
    previous_string = False
    rewritten = []
    for token in tokens:
        if token.type == tokenize.COMMENT or (token.type == tokenize.NAME and token.string == "_oxl_if"):
            # Unknown function calls get the same whitelist error as any other function.
            if token.type == tokenize.NAME:
                raise OXLError("function", "使用了未支持的函数或名称。")
            raise OXLError("syntax", "判定式或计算式不支持注释。")
        if token.type == tokenize.NAME and not token.string.isascii():
            # Python normalizes Unicode identifiers. Do not let a lookalike name
            # become an allowlisted function (or the private if placeholder).
            raise OXLError("syntax", "属性和函数名称请使用已声明的英文名称。")
        if token.type == tokenize.STRING:
            if previous_string or not token.string.startswith("'") or token.string.startswith("'''"):
                raise OXLError("syntax", "文字请使用单引号，不能拼接文字字面量。")
            previous_string = True
        elif token.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.ENDMARKER):
            previous_string = False
        if token.type == tokenize.NAME and token.string == "if":
            token = token._replace(string="_oxl_if")
        rewritten.append(token)
    return tokenize.untokenize(rewritten)


def parse(
    text: str,
    *,
    properties: PropertyDeclarations = (),
    relations: RelationDeclarations | None = None,
) -> Expression:
    """Validate all nodes/references, then discard Python's AST before evaluation."""
    if not isinstance(text, str) or not text.strip():
        raise OXLError("syntax", "请填写判定式或计算式。")
    if len(text) > MAX_LENGTH:
        raise OXLError("length", "判定式或计算式不能超过 500 个字符。")
    try:
        tree = ast.parse(_tokens(text.strip()), mode="eval")
    except (SyntaxError, ValueError, RecursionError) as error:
        if isinstance(error, OXLError):
            raise
        raise OXLError("syntax", "判定式或计算式的写法不正确。") from error
    # Count all parser nodes, including operators and load contexts. Depth starts
    # with Expression at 1 and likewise includes every node, not just operands.
    stack = [(tree, 1)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_NODES:
            raise OXLError("nodes", "判定式或计算式过于复杂，请拆分后再试。")
        if depth > MAX_DEPTH:
            raise OXLError("depth", "判定式或计算式的嵌套不能超过 12 层。")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    declared = property_map(properties)
    links = {key: property_map(value) for key, value in (relations or {}).items()}
    references: set[str] = set()
    relation_references: set[tuple[str, str | None]] = set()

    def convert(node: ast.AST, scope: dict[str, dict], link_scope: str | None = None) -> Node:
        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, float, str) or (
                isinstance(node.value, float) and not math.isfinite(node.value)
            ):
                raise OXLError("syntax", "只支持数字、单引号文字以及 true、false、null。")
            return Node("literal", node.value)
        if isinstance(node, ast.Name):
            if node.id in ("true", "false", "null"):
                return Node("literal", {"true": True, "false": False, "null": None}[node.id])
            if node.id not in scope:
                raise OXLError("reference", f"属性“{node.id}”未在业务对象中声明。")
            if link_scope:
                relation_references.add((link_scope, node.id))
            else:
                references.add(node.id)
            return Node("property", node.id)
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return Node(
                "binary",
                _BINARY[type(node.op)],
                (convert(node.left, scope, link_scope), convert(node.right, scope, link_scope)),
            )
        if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.Not, ast.USub, ast.UAdd):
            return Node(
                "unary",
                {ast.Not: "not", ast.USub: "-", ast.UAdd: "+"}[type(node.op)],
                (convert(node.operand, scope, link_scope),),
            )
        if isinstance(node, ast.BoolOp) and type(node.op) in (ast.And, ast.Or):
            return Node(
                "boolean",
                "and" if isinstance(node.op, ast.And) else "or",
                tuple(convert(value, scope, link_scope) for value in node.values),
            )
        if isinstance(node, ast.Compare):
            if any(type(op) not in _COMPARE for op in node.ops):
                raise OXLError("syntax", "使用了未支持的比较方式。")
            children = [convert(node.left, scope, link_scope)]
            for op, right in zip(node.ops, node.comparators, strict=True):
                if isinstance(op, ast.In):
                    if not isinstance(right, ast.List):
                        raise OXLError("syntax", "属于的右侧必须是字面量清单。")
                    items = tuple(convert(value, {}, link_scope) for value in right.elts)
                    if any(value.kind != "literal" for value in items):
                        raise OXLError("syntax", "清单中只能填写字面量。")
                    children.append(Node("list", children=items))
                else:
                    children.append(convert(right, scope, link_scope))
            return Node("compare", tuple(_COMPARE[type(op)] for op in node.ops), tuple(children))
        if isinstance(node, ast.Call):
            if (
                not isinstance(node.func, ast.Name)
                or node.keywords
                or any(isinstance(arg, (ast.Starred, ast.GeneratorExp)) for arg in node.args)
            ):
                raise OXLError("syntax", "函数仅支持按顺序填写参数。")
            name = "if" if node.func.id == "_oxl_if" else node.func.id
            if name in ("sum", "count", "avg", "min", "max") and node.args:
                first = node.args[0]
                projection = isinstance(first, ast.Attribute)
                count_link = name == "count" and isinstance(first, ast.Name)
                if projection or count_link:
                    if link_scope:
                        raise OXLError("syntax", "汇总条件中不支持再次跨对象汇总。")
                    if projection:
                        if not isinstance(first.value, ast.Name) or name == "count" or len(node.args) != 1:
                            raise OXLError("syntax", "汇总只支持关系及其直接属性。")
                        link, prop = first.value.id, first.attr
                    else:
                        if len(node.args) not in (1, 2):
                            raise OXLError("arity", "计数只支持关系及一个可选条件。")
                        link, prop = first.id, None
                    if link not in links or (prop is not None and prop not in links[link]):
                        raise OXLError("reference", "汇总引用的关系或对端属性尚未声明。")
                    relation_references.add((link, prop))
                    condition = (convert(node.args[1], links[link], link),) if len(node.args) == 2 else ()
                    return Node("aggregate", (name, link, prop), condition)
            if name not in _FUNCTIONS:
                raise OXLError("function", "使用了未支持的函数。")
            minimum, maximum = _FUNCTIONS[name]
            if not minimum <= len(node.args) <= maximum:
                raise OXLError("arity", "函数的参数数量不正确。")
            if name == "round":
                digits = node.args[1]
                if isinstance(digits, ast.Constant):
                    _round_digits(digits.value)
                elif (
                    isinstance(digits, ast.UnaryOp)
                    and isinstance(digits.op, (ast.USub, ast.UAdd))
                    and isinstance(digits.operand, ast.Constant)
                    and type(digits.operand.value) in (int, float)
                ):
                    _round_digits(-digits.operand.value if isinstance(digits.op, ast.USub) else digits.operand.value)
            return Node("call", name, tuple(convert(arg, scope, link_scope) for arg in node.args))
        raise OXLError("syntax", "判定式或计算式中包含未支持的写法。")

    root = convert(tree.body, declared)
    return Expression(root, frozenset(references), frozenset(relation_references), declared, links)


def _now() -> datetime:
    return datetime.now(BEIJING)


def as_of_date(value: str | date | datetime | None = None) -> date:
    if value is None:
        return _now().astimezone(BEIJING).date()
    if isinstance(value, datetime):
        # Date-only input is preferred. Naive clock input is Beijing business time.
        return value.replace(tzinfo=BEIJING).date() if value.tzinfo is None else value.astimezone(BEIJING).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            if len(value) == 10:
                return date.fromisoformat(value)
            return as_of_date(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError as error:
            raise OXLError("date", "日期格式不正确，请填写有效日期。") from error
    raise OXLError("date", "日期格式不正确，请填写有效日期。")


def _scalar(value: object) -> object:
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise OXLError("value", "参与计算的值必须是有效的数字、文字、布尔值或空值。")


def _number(value: object) -> int | float:
    if type(value) not in (int, float):
        raise OXLError("type", "算术计算需要数字。")
    return value


def _boolean(value: object) -> bool | None:
    if value is not None and type(value) is not bool:
        raise OXLError("type", "条件需要是或否，不能使用数字或文字代替。")
    return value


def _text(value: object) -> str:
    if type(value) is not str:
        raise OXLError("type", "文字函数需要文字参数。")
    return value


def _logic(values: Iterable[object], name: str) -> bool | None:
    unknown = False
    for value in values:
        value = _boolean(value)
        if (name == "and" and value is False) or (name == "or" and value is True):
            return value
        unknown = unknown or value is None
    return None if unknown else name == "and"


def _comparison(left: object, right: object, op: str) -> bool | None:
    if left is None or right is None:
        return None
    if op == "in":
        # A null candidate leaves membership unknown unless a definite match exists.
        return _logic((_comparison(left, item, "==") for item in right), "or")
    if type(left) is not type(right) and not (type(left) in (int, float) and type(right) in (int, float)):
        raise OXLError("type", "比较的两个值需要使用相同类型。")
    return {
        "==": operator.eq,
        "!=": operator.ne,
        ">": operator.gt,
        ">=": operator.ge,
        "<": operator.lt,
        "<=": operator.le,
    }[op](left, right)


def evaluate(
    expression: str | Expression,
    values: Mapping[str, object],
    *,
    properties: PropertyDeclarations = (),
    relations: RelationDeclarations | None = None,
    related: Mapping[str, list[dict] | None] | None = None,
    as_of: str | date | datetime | None = None,
) -> object:
    parsed = (
        parse(expression, properties=properties, relations=relations) if isinstance(expression, str) else expression
    )
    if not isinstance(parsed, Expression):
        raise OXLError("syntax", "请提供已校验的判定式或计算式。")
    day = as_of_date(as_of)
    if not isinstance(values, Mapping):
        raise OXLError("value", "核对对象需要提供属性记录。")
    related = {} if related is None else related
    if not isinstance(related, Mapping):
        raise OXLError("value", "关联对象需要按关系提供记录清单。")

    def visit(node: Node, row: Mapping[str, object]) -> object:
        if node.kind == "literal":
            return node.value
        if node.kind == "property":
            return _scalar(row.get(node.value))
        if node.kind == "list":
            return [visit(child, row) for child in node.children]
        if node.kind == "boolean":
            return _logic((visit(child, row) for child in node.children), node.value)
        if node.kind == "unary":
            value = visit(node.children[0], row)
            if node.value == "not":
                value = _boolean(value)
                return None if value is None else not value
            return None if value is None else (-_number(value) if node.value == "-" else _number(value))
        if node.kind == "binary":
            left, right = (visit(child, row) for child in node.children)
            if left is None or right is None:
                return None
            return _scalar(
                {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv}[node.value](
                    _number(left), _number(right)
                )
            )
        if node.kind == "compare":
            operands = [visit(child, row) for child in node.children]
            return _logic(
                (_comparison(operands[index], operands[index + 1], op) for index, op in enumerate(node.value)), "and"
            )
        if node.kind == "aggregate":
            name, link, prop = node.value
            rows = related.get(link)
            if rows is None:
                return None
            if type(rows) is not list or any(type(item) is not dict for item in rows):
                raise OXLError("value", "关联对象需要提供记录清单。")
            if name == "count":
                if not node.children:
                    return len(rows)
                matches = [_boolean(visit(node.children[0], item)) for item in rows]
                return None if None in matches else sum(value is True for value in matches)
            numbers = [_scalar(item.get(prop)) for item in rows]
            if None in numbers:
                return None
            numbers = [_number(value) for value in numbers]
            if name == "sum":
                return _scalar(sum(numbers))
            if not numbers:
                return None
            if name == "avg":
                return _scalar(sum(numbers) / len(numbers))
            return (min if name == "min" else max)(numbers)
        if node.kind == "call":
            name = node.value
            if name == "today":
                return day.isoformat()
            if name == "if":
                condition = _boolean(visit(node.children[0], row))
                return None if condition is None else visit(node.children[1 if condition else 2], row)
            if name == "coalesce":
                value = visit(node.children[0], row)
                return visit(node.children[1], row) if value is None else value
            args = [visit(child, row) for child in node.children]
            if name in ("is_null", "not_null"):
                return (args[0] is None) if name == "is_null" else (args[0] is not None)
            if None in args:
                return None
            if name in ("max", "min"):
                return (max if name == "max" else min)(_number(arg) for arg in args)
            if name == "abs":
                return abs(_number(args[0]))
            if name == "round":
                return round(_number(args[0]), _round_digits(args[1]))
            if name == "len":
                return len(_text(args[0]))
            if name == "contains":
                return _text(args[1]) in _text(args[0])
            if name == "starts_with":
                return _text(args[0]).startswith(_text(args[1]))
            if name == "days_between":
                return (as_of_date(_text(args[1])) - as_of_date(_text(args[0]))).days
            if name in ("year", "month"):
                value = as_of_date(_text(args[0]))
                return value.year if name == "year" else value.month
        raise OXLError("syntax", "判定式或计算式的结构不正确。")

    try:
        return visit(parsed.root, values)
    except (ArithmeticError, TypeError, ValueError) as error:
        if isinstance(error, OXLError):
            raise
        raise OXLError("calculation", "计算未完成，请检查数值、日期和除数。") from error


def to_sentence(
    expression: str | Expression,
    *,
    properties: PropertyDeclarations = (),
    relations: RelationDeclarations | None = None,
) -> str:
    parsed = (
        parse(expression, properties=properties, relations=relations) if isinstance(expression, str) else expression
    )

    def render(node: Node, scope: dict[str, dict]) -> str:
        if node.kind == "literal":
            if node.value is None:
                return "未填写"
            if type(node.value) is bool:
                return "是" if node.value else "否"
            return f"“{node.value}”" if isinstance(node.value, str) else str(node.value)
        if node.kind == "property":
            return scope[node.value].get("name") or str(node.value)
        if node.kind == "list":
            return "、".join(render(child, scope) for child in node.children)
        if node.kind in ("binary", "boolean"):
            return f" {_WORDS[node.value]} ".join(
                f"（{render(child, scope)}）" if child.children else render(child, scope) for child in node.children
            )
        if node.kind == "unary":
            return f"{_WORDS[node.value]}（{render(node.children[0], scope)}）"
        if node.kind == "compare":
            parts = []
            for index, op in enumerate(node.value):
                left, right = node.children[index : index + 2]
                suffix = ""
                if left.kind == "property" and right.kind == "literal" and type(right.value) in (int, float):
                    unit = scope[left.value].get("unit")
                    suffix = f" {unit}" if unit else ""
                parts.append(f"{render(left, scope)} {_WORDS[op]} {render(right, scope)}{suffix}")
            return " 并且 ".join(parts)
        if node.kind == "aggregate":
            name, link, prop = node.value
            target = parsed.relations[link]
            label = target[prop].get("name") or prop if prop else "关联记录"
            condition = f"，其中{render(node.children[0], target)}" if node.children else ""
            return f"{label}的{_FUNCTION_WORDS[name]}{condition}"
        if node.kind == "call":
            args = [render(child, scope) for child in node.children]
            if node.value == "if":
                return f"如果{args[0]}，则取{args[1]}，否则取{args[2]}"
            return f"{_FUNCTION_WORDS[node.value]}（{'，'.join(args)}）" if args else _FUNCTION_WORDS[node.value]
        raise OXLError("syntax", "判定式或计算式的结构不正确。")

    return render(parsed.root, parsed.properties)

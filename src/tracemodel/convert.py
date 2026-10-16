"""Old saved formulas as lambdas of the cell.

Before formulas were lambdas, a definition file saved them as expression text, "revenue * 0.6"
or "(a >= 0) & ~b.is_nil()", and a check built per label as one text per label combination
(`expect_by_label`). Each becomes the source of one lambda of `x`:

    "revenue * 0.6"                 -> lambda x: x['revenue'] * 0.6
    "(a >= 0) & ~b.is_nil()"        -> lambda x: all([x['a'] >= 0, not x['b'].is_nil()])

`&` and `|` become all([...]) and any([...]), not `and` and `or`, so every part is still read:
a missing cell fails the check rather than being skipped. The text is only parsed and
rewritten, never run.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping, Sequence

from .errors import DefinitionError
from .formula import MAX_SOURCE

_CALLS = {"abs", "date", "datetime", "timedelta"}
_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.Call, ast.Attribute, ast.Subscript,
            ast.Name, ast.Constant, ast.keyword, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub,
            ast.Invert, ast.BitAnd, ast.BitOr, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


class _Rewrite(ast.NodeTransformer):
    def visit_Name(self, node: ast.Name) -> ast.AST:  # an item: x['item']
        return ast.Subscript(ast.Name("x", ast.Load()), ast.Constant(node.id), ast.Load())

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if (isinstance(node.func, ast.Name) and node.func.id in ("date", "datetime") and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            # date('2026-03-31') as the old text wrote it: date.fromisoformat('2026-03-31')
            return ast.Call(ast.Attribute(node.func, "fromisoformat", ast.Load()), node.args, [])
        if isinstance(node.func, ast.Name) and node.func.id in _CALLS:  # abs(), timedelta(days=30): kept
            node.args = [self.visit(a) for a in node.args]
            node.keywords = [self.visit(k) for k in node.keywords]
            return node
        return self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        if isinstance(node.op, (ast.BitAnd, ast.BitOr)):
            parts = [self.visit(p) for p in _flatten(node, type(node.op))]
            name = "all" if isinstance(node.op, ast.BitAnd) else "any"
            return ast.Call(ast.Name(name, ast.Load()), [ast.List(parts, ast.Load())], [])
        return self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        if isinstance(node.op, ast.Invert):
            return ast.UnaryOp(ast.Not(), self.visit(node.operand))
        return self.generic_visit(node)


def _flatten(node: ast.AST, op: type) -> list[ast.AST]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, op):
        return _flatten(node.left, op) + _flatten(node.right, op)
    return [node]


def _body(text: str, where: str) -> ast.expr:
    if not isinstance(text, str):
        raise DefinitionError(f"{where}: a saved formula must be text, got {text!r}")
    if len(text) > MAX_SOURCE:
        raise DefinitionError(f"{where}: a saved formula is longer than {MAX_SOURCE:,} characters")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise DefinitionError(f"{where}: can't read expression {text!r}: {e.msg}") from None
    except (MemoryError, RecursionError):
        raise DefinitionError(f"{where}: a saved formula is nested too deeply to read") from None
    for n in ast.walk(tree):
        if not isinstance(n, _ALLOWED) or (isinstance(n, (ast.Name, ast.Attribute))
                                           and (getattr(n, "id", "") or getattr(n, "attr", "")).startswith("_")):
            raise DefinitionError(f"{where}: can't read expression {text!r}: {ast.unparse(n)!r} "
                                  "is not part of the expression grammar")
    return _Rewrite().visit(tree).body


def _lambda(body: ast.expr) -> str:
    args = ast.arguments(posonlyargs=[], args=[ast.arg("x")], kwonlyargs=[], kw_defaults=[], defaults=[])
    return ast.unparse(ast.fix_missing_locations(ast.Lambda(args, body)))


def from_text(text: str, where: str = "formula") -> str:
    """The lambda source for an expression's saved text."""
    if isinstance(text, str) and text.lstrip().startswith("lambda"):
        return text.strip()
    return _lambda(_body(text, where))


def from_expr(expr: object) -> str:
    """The lambda source for an expression or condition built in Python (model['a'] * 0.6)."""
    return from_text(repr(expr), f"formula {expr!r}")


def from_by_label(params: Sequence[str], rows: Iterable[Mapping[str, object]], where: str = "check") -> str:
    """A check saved per label combination, as one lambda choosing its condition by x.coords:
    (key == L1 and cond1) or (key == L2 and cond2) or ..., flat however many labels there are."""
    if not isinstance(rows, (list, tuple)) or not rows:
        raise DefinitionError(f"{where}: expect_by_label must be a non-empty list")
    params = list(params)
    key: ast.expr = (ast.Subscript(_coords(), ast.Constant(params[0]), ast.Load()) if len(params) == 1
                     else ast.Tuple([ast.Subscript(_coords(), ast.Constant(p), ast.Load()) for p in params], ast.Load()))
    arms: list[ast.expr] = []
    for row in rows:
        labels = list(row.get("labels", ())) if isinstance(row, Mapping) else []  # type: ignore[call-overload]
        if not params or len(labels) != len(params) or not all(isinstance(v, str) for v in labels):
            raise DefinitionError(f"{where}: labels {labels!r} don't match params {params!r}")
        value: ast.expr = (ast.Constant(labels[0]) if len(params) == 1
                           else ast.Tuple([ast.Constant(v) for v in labels], ast.Load()))
        arms.append(ast.BoolOp(ast.And(), [ast.Compare(key, [ast.Eq()], [value]),
                                           _body(row.get("expect"), where)]))  # type: ignore[arg-type]
    return _lambda(ast.BoolOp(ast.Or(), arms) if len(arms) > 1 else arms[0])


def _coords() -> ast.expr:
    return ast.Attribute(ast.Name("x", ast.Load()), "coords", ast.Load())


__all__ = ["from_by_label", "from_expr", "from_text"]

"""Read an expression back from the text that repr() writes, e.g. "abs(a['/'] - b) <= 0.5".

The text is parsed with `ast` and interpreted node by node; nothing is passed to eval. Only
the expression grammar is accepted: item names, ["label"], .sel(), .lag(), .lead(),
.shift(), .is_nil(), abs(), numbers, + - * /, unary -, one comparison, and & | ~.
"""

from __future__ import annotations

import ast
import operator
from typing import TYPE_CHECKING, Any

from .errors import DefinitionError
from .expr import Cell, Condition, Expr

if TYPE_CHECKING:
    from .model import Model

_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.BitAnd: operator.and_, ast.BitOr: operator.or_,
}
_COMPARE = {
    ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
}
_CELL_METHODS = {"sel", "lag", "lead", "shift", "is_nil"}


def parse(text: str, model: Model) -> Expr | Condition:
    """The expression or condition that `text` (as written by repr) describes."""
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise DefinitionError(f"can't read expression {text!r}: {e.msg}") from None
    return _Reader(text, model).read(tree.body)


class _Reader:
    def __init__(self, text: str, model: Model):
        self.text, self.model = text, model

    def fail(self, node: ast.AST, why: str) -> DefinitionError:
        return DefinitionError(f"can't read expression {self.text!r}: {why} ({ast.unparse(node)!r})")

    def read(self, node: ast.AST) -> Any:
        match node:
            case ast.Constant(value=v) if isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
            case ast.Name(id=name):
                if name not in self.model.items:
                    raise self.fail(node, f"{name!r} is not a declared item")
                return self.model[name]
            case ast.BinOp(op=op) if type(op) in _BINARY:
                return _BINARY[type(op)](self.read(node.left), self.read(node.right))
            case ast.UnaryOp(op=ast.USub(), operand=operand):
                return -self.read(operand)
            case ast.UnaryOp(op=ast.Invert(), operand=operand):
                return ~self.condition(operand)
            case ast.Compare(ops=[op], comparators=[right]) if type(op) in _COMPARE:
                return _COMPARE[type(op)](self.read(node.left), self.read(right))
            case ast.Subscript(value=value, slice=ast.Constant(value=str() as label)):
                return self.cell(value)[label]
            case ast.Call(func=ast.Name(id="abs"), args=[arg], keywords=[]):
                return abs(self.read(arg))
            case ast.Call(func=ast.Attribute(value=value, attr=attr)) if attr in _CELL_METHODS:
                args = [self.constant(a) for a in node.args]
                kwargs = {k.arg: self.constant(k.value) for k in node.keywords if k.arg}
                if len(kwargs) != len(node.keywords):
                    raise self.fail(node, "** arguments aren't allowed")
                return getattr(self.cell(value), attr)(*args, **kwargs)
        raise self.fail(node, "not part of the expression grammar")

    def cell(self, node: ast.AST) -> Cell:
        value = self.read(node)
        if not isinstance(value, Cell):
            raise self.fail(node, "only an item's cells take [label] or methods")
        return value

    def condition(self, node: ast.AST) -> Condition:
        value = self.read(node)
        if not isinstance(value, Condition):
            raise self.fail(node, "~ takes a condition")
        return value

    def constant(self, node: ast.AST) -> str | int:
        match node:
            case ast.Constant(value=v) if isinstance(v, (str, int)) and not isinstance(v, bool):
                return v
        raise self.fail(node, "method arguments must be a label or a whole number")

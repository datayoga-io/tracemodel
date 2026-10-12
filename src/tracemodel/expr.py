"""Expressions over cells.

`model["net_sales"]` is a Cell with no coords fixed; `["/"]` and `.sel(...)` fix more.
Arithmetic builds a tree; dims a cell leaves open are its free dims, and an expression's
free dims are the union of its cells' (xarray-style broadcasting by dim name).

Comparisons (< <= > >= == !=) turn expressions into conditions, which & | ~ combine.
In a check the tree is evaluated once per run. Displayed in IPython, it is evaluated
against the model's current values and shown as a table.
"""

from __future__ import annotations

import html
import operator
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from .errors import DefinitionError, UnknownCoord

if TYPE_CHECKING:
    from .model import Item, Model

Lookup = Callable[["Cell", Mapping[str, str]], float]


def _union(*lists: list[str]) -> list[str]:
    out: list[str] = []
    for dims in lists:
        out += [d for d in dims if d not in out]
    return out


class _Shows:
    """Display in IPython/Jupyter by evaluating against the model's values."""

    def _model(self) -> Model | None:
        return self.cells()[0].model if self.cells() else None  # type: ignore[attr-defined]

    def evaluate_table(self):
        model = self._model()
        if model is None:
            raise DefinitionError("expression has no cells to evaluate")
        return model.evaluate(self)  # type: ignore[arg-type]

    def show(self) -> None:
        result = self.evaluate_table()
        result.show() if hasattr(result, "show") else print(result)

    # A table result carries the formula as its title; a scalar gets "formula = value".
    def _repr_pretty_(self, p, cycle) -> None:
        result = self.evaluate_table()
        p.text(str(result) if hasattr(result, "_repr_html_") else f"{self!r} = {result:,}")

    def _repr_html_(self) -> str:
        result = self.evaluate_table()
        if hasattr(result, "_repr_html_"):
            return result._repr_html_()
        return f"<pre>{html.escape(repr(self))} = {result:,}</pre>"


class Expr(_Shows):
    def cells(self) -> list[Cell]:
        raise NotImplementedError

    def free_dims(self) -> list[str]:
        return _union(*(c.free_dims() for c in self.cells()))

    def evaluate(self, lookup: Lookup, at: Mapping[str, str]) -> float:
        raise NotImplementedError

    def __bool__(self) -> bool:
        raise DefinitionError(
            f"{self!r} has no single truth value: it is a value for each coord. Compare it, e.g. "
            f"{self!r} > 0, or in a formula written as a function, branch on x[...] (a number)"
        )

    def __add__(self, other: Any) -> Expr:
        return BinOp("+", self, _wrap(other))

    def __radd__(self, other: Any) -> Expr:
        return BinOp("+", _wrap(other), self)

    def __sub__(self, other: Any) -> Expr:
        return BinOp("-", self, _wrap(other))

    def __rsub__(self, other: Any) -> Expr:
        return BinOp("-", _wrap(other), self)

    def __mul__(self, other: Any) -> Expr:
        return BinOp("*", self, _wrap(other))

    def __rmul__(self, other: Any) -> Expr:
        return BinOp("*", _wrap(other), self)

    def __truediv__(self, other: Any) -> Expr:
        return BinOp("/", self, _wrap(other))

    def __rtruediv__(self, other: Any) -> Expr:
        return BinOp("/", _wrap(other), self)

    def __neg__(self) -> Expr:
        return BinOp("-", Const(0), self)

    def __abs__(self) -> Expr:
        return Abs(self)

    def __eq__(self, other: object) -> Comparison:  # type: ignore[override]
        return Comparison("==", self, _wrap(other))

    def __ne__(self, other: object) -> Comparison:  # type: ignore[override]
        return Comparison("!=", self, _wrap(other))

    def __lt__(self, other: Any) -> Comparison:
        return Comparison("<", self, _wrap(other))

    def __le__(self, other: Any) -> Comparison:
        return Comparison("<=", self, _wrap(other))

    def __gt__(self, other: Any) -> Comparison:
        return Comparison(">", self, _wrap(other))

    def __ge__(self, other: Any) -> Comparison:
        return Comparison(">=", self, _wrap(other))

    # `a >= 0 & b >= 0` parses as `a >= (0 & b) >= 0`; point at the parentheses.
    def _not_a_condition(self, other: Any = None) -> None:
        raise DefinitionError(
            f"& | ~ combine comparisons, not numbers: put parentheses around each comparison, "
            f"e.g. ({self!r} > 0) & (...)"
        )

    __and__ = __rand__ = __or__ = __ror__ = __invert__ = _not_a_condition

    __hash__ = None  # type: ignore[assignment]


def _wrap(x: Any) -> Expr:
    if isinstance(x, Expr):
        return x
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return Const(x)
    raise DefinitionError(f"can't use {x!r} in an expression")


class Const(Expr):
    _repr_pretty_ = _Shows._repr_pretty_  # IPython prefers a subclass __repr__ otherwise

    def __init__(self, value: float):
        self.value = value

    def cells(self) -> list[Cell]:
        return []

    def evaluate(self, lookup: Lookup, at: Mapping[str, str]) -> float:
        return self.value

    def __repr__(self) -> str:
        return repr(self.value)


class Abs(Expr):
    _repr_pretty_ = _Shows._repr_pretty_  # IPython prefers a subclass __repr__ otherwise

    def __init__(self, inner: Expr):
        self.inner = inner

    def cells(self) -> list[Cell]:
        return self.inner.cells()

    def evaluate(self, lookup: Lookup, at: Mapping[str, str]) -> float:
        return abs(self.inner.evaluate(lookup, at))

    def __repr__(self) -> str:
        return f"abs({self.inner!r})"


_OPS = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv}
_PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2}


class BinOp(Expr):
    _repr_pretty_ = _Shows._repr_pretty_  # IPython prefers a subclass __repr__ otherwise

    def __init__(self, op: str, left: Expr, right: Expr):
        self.op, self.left, self.right = op, left, right

    def cells(self) -> list[Cell]:
        return self.left.cells() + self.right.cells()

    def evaluate(self, lookup: Lookup, at: Mapping[str, str]) -> float:
        return _OPS[self.op](self.left.evaluate(lookup, at), self.right.evaluate(lookup, at))

    def __repr__(self) -> str:
        def side(e: Expr, right: bool) -> str:
            if isinstance(e, BinOp) and (
                _PRECEDENCE[e.op] < _PRECEDENCE[self.op]
                or (right and _PRECEDENCE[e.op] == _PRECEDENCE[self.op] and self.op in "-/")
            ):
                return f"({e!r})"
            return repr(e)

        # sum() starts from 0 and unary minus is 0 - x; show them as `a + b` and `-x`.
        if isinstance(self.left, Const) and self.left.value == 0 and self.op in "+-":
            if self.op == "+":
                return repr(self.right)
            return f"-({self.right!r})" if isinstance(self.right, BinOp) else f"-{self.right!r}"
        return f"{side(self.left, False)} {self.op} {side(self.right, True)}"


class OutOfRange(DefinitionError):
    """A lag or lead points before the first label or after the last."""

    def __init__(self, message: str, dim: str, steps: int):
        super().__init__(message)
        self.dim, self.steps = dim, steps


class Cell(Expr):
    """An item with some coords fixed. `model["net_sales"]` fixes none.

    `shifts` moves a dim's label by a number of steps along the dim's order:
    1 is the previous label (lag), -1 the next (lead).
    """
    _repr_pretty_ = _Shows._repr_pretty_  # IPython prefers a subclass __repr__ otherwise

    def __init__(self, model: Model, item: Item, coords: Mapping[str, str] | None = None,
                 shifts: Mapping[str, int] | None = None):
        self.model = model
        self.item = item
        self.coords = dict(coords or {})
        self.shifts = {d: n for d, n in (shifts or {}).items() if n}

    def __getitem__(self, label: str) -> Cell:
        """Fix one more coord by its label; the label must belong to exactly one free dim."""
        dims = [d for d in self.free_dims() if label in self.model.dimensions[d]]
        if not dims:
            raise UnknownCoord(
                f"{label!r} is not a coord of any free dim of {self!r} "
                f"({', '.join(self.free_dims()) or 'none'})"
            )
        if len(dims) > 1:
            raise DefinitionError(
                f"{label!r} is a coord of several dims of {self.item.name!r} "
                f"({', '.join(dims)}); use .sel({dims[0]}=...)"
            )
        return Cell(self.model, self.item, self.coords | {dims[0]: label}, self.shifts)

    def sel(self, **coords: str) -> Cell:
        for dim, label in coords.items():
            if dim not in self.item.dims:
                raise UnknownCoord(f"{self.item.name!r} has no dim {dim!r}")
            self.model.dimensions[dim].require(label)
        return Cell(self.model, self.item, self.coords | coords, self.shifts)

    def shift(self, **steps: int) -> Cell:
        """The value `n` labels earlier along a dim, as in xarray: shift(period=1) is the
        previous period, shift(period=-1) the next."""
        shifts = dict(self.shifts)
        for dim, n in steps.items():
            if dim not in self.item.dims:
                raise UnknownCoord(f"{self.item.name!r} has no dim {dim!r}")
            if self.model.dimensions[dim].hierarchy:
                raise DefinitionError(f"can't shift along {dim!r}: a hierarchy has no previous or next label")
            if isinstance(n, bool) or not isinstance(n, int):
                raise DefinitionError(f"shift({dim}=...) takes a whole number of steps, got {n!r}")
            shifts[dim] = shifts.get(dim, 0) + n
        return Cell(self.model, self.item, self.coords, shifts)

    def lag(self, dim: str, n: int = 1) -> Cell:
        """The value `n` labels before, as in SQL LAG: lag("period") is the prior period."""
        return self.shift(**{dim: n})

    def lead(self, dim: str, n: int = 1) -> Cell:
        """The value `n` labels after, as in SQL LEAD: lead("period") is the next period."""
        return self.shift(**{dim: -n})

    def is_nil(self) -> IsNil:
        """A condition: the cell was entered as tm.NIL ("—"), not as a number such as 0."""
        return IsNil(self)

    def cells(self) -> list[Cell]:
        return [self]

    def free_dims(self) -> list[str]:
        return [d for d in self.item.dims if d not in self.coords]

    def resolve(self, at: Mapping[str, str]) -> dict[str, str]:
        """Full coords: the cell's own, plus `at` for its free dims, moved by any shift."""
        full = {d: self.coords[d] if d in self.coords else at[d] for d in self.item.dims}
        for dim, n in self.shifts.items():
            labels = self.model.dimensions[dim].coords
            i = labels.index(full[dim]) - n
            if not 0 <= i < len(labels):
                where = "before" if n > 0 else "after"
                raise OutOfRange(f"{self!r} has no {dim} {where} {full[dim]!r}", dim, n)
            full[dim] = labels[i]
        return full

    def evaluate(self, lookup: Lookup, at: Mapping[str, str]) -> float:
        return lookup(self, at)

    def _unambiguous(self, label: str) -> bool:
        """`item[label]` reads back as this cell: the label belongs to only one of its dims."""
        return sum(label in self.model.dimensions[d] for d in self.item.dims) == 1

    def __repr__(self) -> str:
        if not self.coords:
            base = self.item.name
        elif len(self.coords) == 1 and self._unambiguous(*self.coords.values()):
            (label,) = self.coords.values()
            base = f"{self.item.name}[{label!r}]"
        else:
            inner = ", ".join(f"{d}={v!r}" for d, v in self.coords.items())
            base = f"{self.item.name}.sel({inner})"
        for dim, n in self.shifts.items():
            verb, k = ("lag", n) if n > 0 else ("lead", -n)
            base += f".{verb}({dim!r})" if k == 1 else f".{verb}({dim!r}, {k})"
        return base


class Condition(_Shows):
    """True or false at each coord: a comparison, or comparisons joined with & | ~."""

    def cells(self) -> list[Cell]:
        raise NotImplementedError

    def free_dims(self) -> list[str]:
        return _union(*(c.free_dims() for c in self.cells()))

    def test(self, lookup: Lookup, at: Mapping[str, str]) -> bool:
        raise NotImplementedError

    @property
    def rule(self) -> str:
        """Short label for reports: the operator."""
        raise NotImplementedError

    def __and__(self, other: Any) -> Logic:
        return Logic("&", self, _condition(other))

    def __or__(self, other: Any) -> Logic:
        return Logic("|", self, _condition(other))

    def __invert__(self) -> Not:
        return Not(self)

    def __bool__(self) -> bool:
        raise DefinitionError(
            "a condition has no single truth value. Use & | ~ instead of and / or / not, with "
            "parentheses around each comparison; write 0 <= x <= 1 as (x >= 0) & (x <= 1). "
            "Display it to see the result for each coord, or use it in model.add_check(..., expect=...)"
        )


def _condition(x: Any) -> Condition:
    if isinstance(x, Condition):
        return x
    raise DefinitionError(
        f"& | ~ combine comparisons, got {x!r}; put parentheses around each comparison, e.g. (a > 0) & (b > 0)"
    )


def _slack(lhs: float, rhs: float) -> float:
    # Float slack, so bounds hold as written: abs(391_035.2 - 391_035) is 0.2000000000116.
    return 1e-9 * max(1.0, abs(lhs), abs(rhs))


_COMPARE = {
    "<": lambda a, b, e: a < b - e,
    "<=": lambda a, b, e: a <= b + e,
    ">": lambda a, b, e: a > b + e,
    ">=": lambda a, b, e: a >= b - e,
    "==": lambda a, b, e: abs(a - b) <= e,
    "!=": lambda a, b, e: abs(a - b) > e,
}


class Comparison(Condition):
    """left <op> right."""
    _repr_pretty_ = _Shows._repr_pretty_  # IPython prefers a subclass __repr__ otherwise

    def __init__(self, op: str, left: Expr, right: Expr):
        self.op, self.left, self.right = op, left, right

    def cells(self) -> list[Cell]:
        return self.left.cells() + self.right.cells()

    @property
    def rule(self) -> str:
        return self.op

    def allows(self, lhs: float, rhs: float) -> bool:
        return _COMPARE[self.op](lhs, rhs, _slack(lhs, rhs))

    def test(self, lookup: Lookup, at: Mapping[str, str]) -> bool:
        return self.allows(self.left.evaluate(lookup, at), self.right.evaluate(lookup, at))

    def __repr__(self) -> str:
        return f"{self.left!r} {self.op} {self.right!r}"


class Logic(Condition):
    """Conditions joined with & (all hold) or | (at least one holds)."""
    _repr_pretty_ = _Shows._repr_pretty_  # IPython prefers a subclass __repr__ otherwise

    def __init__(self, op: str, *parts: Condition):
        self.op = op
        # (a & b) & c is shown and evaluated as a & b & c
        self.parts = [q for p in parts for q in (p.parts if isinstance(p, Logic) and p.op == op else [p])]

    def cells(self) -> list[Cell]:
        return [c for p in self.parts for c in p.cells()]

    def test(self, lookup: Lookup, at: Mapping[str, str]) -> bool:
        results = [p.test(lookup, at) for p in self.parts]
        return all(results) if self.op == "&" else any(results)

    @property
    def rule(self) -> str:
        return "and" if self.op == "&" else "or"

    def __repr__(self) -> str:
        return f" {self.op} ".join(f"({p!r})" for p in self.parts)


class IsNil(Condition):
    _repr_pretty_ = _Shows._repr_pretty_  # IPython prefers a subclass __repr__ otherwise

    def __init__(self, cell: Cell):
        self.cell = cell
        self.parts: list[Condition] = []

    def cells(self) -> list[Cell]:
        return [self.cell]

    def test(self, lookup: Lookup, at: Mapping[str, str]) -> bool:
        lookup(self.cell, at)  # raises when the cell isn't entered: not entered isn't nil
        return self.cell.model._is_nil(self.cell, at)

    rule = "is nil"

    def __repr__(self) -> str:
        return f"{self.cell!r}.is_nil()"


class Not(Condition):
    _repr_pretty_ = _Shows._repr_pretty_  # IPython prefers a subclass __repr__ otherwise

    def __init__(self, part: Condition):
        self.parts = [part]

    def cells(self) -> list[Cell]:
        return self.parts[0].cells()

    def test(self, lookup: Lookup, at: Mapping[str, str]) -> bool:
        return not self.parts[0].test(lookup, at)

    rule = "not"

    def __repr__(self) -> str:
        return f"~({self.parts[0]!r})"

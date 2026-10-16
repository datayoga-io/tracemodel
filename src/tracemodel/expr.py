"""Cells: `model["net_sales"]` is the item with no coords fixed; `["/"]` and `.sel(...)` fix more.

Displayed in IPython it is evaluated against the model's current values and shown as a table.
Formulas and checks are lambdas of `x` (see formula.py); arithmetic on a Cell raises, showing
the lambda to write instead.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .errors import DefinitionError, UnknownCoord

if TYPE_CHECKING:
    from .model import Item, Model


class _Shows:
    """Display in IPython/Jupyter by evaluating against the model's values."""

    def evaluate_table(self):
        return self.model.evaluate(self)  # type: ignore[attr-defined]

    def show(self) -> None:
        result = self.evaluate_table()
        result.show() if hasattr(result, "show") else print(result)

    # A table result carries the cell as its title; a scalar gets "cell = value".
    def _repr_pretty_(self, p, cycle) -> None:
        result = self.evaluate_table()
        p.text(str(result) if hasattr(result, "_repr_html_") else f"{self!r} = {_fmt(result)}")

    def _repr_html_(self) -> str:
        result = self.evaluate_table()
        if hasattr(result, "_repr_html_"):
            return result._repr_html_()
        return f"<pre>{html.escape(repr(self))} = {html.escape(_fmt(result))}</pre>"


def _fmt(value: Any) -> str:
    from .table import _fmt

    return _fmt(value)


def _formula_only(text: str) -> None:
    from .convert import from_text

    try:
        hint = f": write lambda x: {from_text(text).split(': ', 1)[1]}"
    except DefinitionError:
        hint = ", e.g. lambda x: x['revenue'] * 0.6"
    raise DefinitionError(f"{text}: formulas and checks are lambdas of the cell, x{hint}")


def _op(symbol: str, reflected: bool = False):
    def method(self: Cell, other: Any = None) -> Any:
        if symbol in ("-u", "abs"):
            _formula_only(f"-{self!r}" if symbol == "-u" else f"abs({self!r})")
        left, right = (other, self) if reflected else (self, other)
        _formula_only(f"{left!r} {symbol} {right!r}")

    return method


class OutOfRange(DefinitionError):
    """A lag or lead points before the first label or after the last."""

    def __init__(self, message: str, dim: str, steps: int):
        super().__init__(message)
        self.dim, self.steps = dim, steps


class Cell(_Shows):
    """An item with some coords fixed. `model["net_sales"]` fixes none.

    `shifts` moves a dim's label by a number of steps along the dim's order:
    1 is the previous label (lag), -1 the next (lead).

    A formula reads cells through its `x`; arithmetic on a Cell raises, with the lambda to
    write instead.
    """

    def __init__(self, model: Model, item: Item, coords: Mapping[str, str] | None = None,
                 shifts: Mapping[str, int] | None = None):
        self.model = model
        self.item = item
        self.coords = dict(coords or {})
        self.shifts = {d: n for d, n in (shifts or {}).items() if n}

    __add__, __radd__ = _op("+"), _op("+", True)
    __sub__, __rsub__ = _op("-"), _op("-", True)
    __mul__, __rmul__ = _op("*"), _op("*", True)
    __truediv__, __rtruediv__ = _op("/"), _op("/", True)
    __pow__, __rpow__ = _op("**"), _op("**", True)
    __lt__, __le__, __gt__, __ge__ = _op("<"), _op("<="), _op(">"), _op(">=")
    __eq__, __ne__ = _op("=="), _op("!=")  # type: ignore[assignment]
    __neg__, __abs__ = _op("-u"), _op("abs")
    __and__ = __rand__ = __or__ = __ror__ = __invert__ = _op("&")
    __hash__ = None  # type: ignore[assignment]

    def __bool__(self) -> bool:
        raise DefinitionError(f"{self!r} has no single truth value: it is a value for each coord; "
                              "in a formula, branch on x[...] (a number)")

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

    def is_nil(self) -> None:
        _formula_only(f"{self!r}.is_nil()")

    def cells(self) -> list[Cell]:
        return [self]

    def free_dims(self) -> list[str]:
        return [d for d in self.item.dims if d not in self.coords]

    def resolve(self, at: Mapping[str, str]) -> dict[str, str]:
        """Full coords: the cell's own, plus `at` for its free dims, moved by any shift."""
        full = {d: self.coords[d] if d in self.coords else at[d] for d in self.item.dims}
        for dim, n in self.shifts.items():
            dimension = self.model.dimensions[dim]
            labels = dimension.coords
            i = dimension.index[full[dim]] - n
            if not 0 <= i < len(labels):
                where = "before" if n > 0 else "after"
                raise OutOfRange(f"{self!r} has no {dim} {where} {full[dim]!r}", dim, n)
            full[dim] = labels[i]
        return full

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


def diff(lhs: Any, rhs: Any) -> Any:
    """left - right for a report; None when missing or when the two can't be subtracted."""
    if lhs is None or rhs is None:
        return None
    try:
        return lhs - rhs
    except TypeError:
        return None

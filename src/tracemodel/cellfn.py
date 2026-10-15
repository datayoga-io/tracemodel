"""`x`, the cell a formula or check runs with: lambda x: max(0, 25_000 - x["cash"]).

`x` behaves as the cell's value (a number, or a date for a date item), read on demand and
traced; x["item"], .sel(), .lag() and .lead() read other cells, and x.coords, x.attrs(),
x.period, x.children() and x.labels() give the cell's labels and what the model knows about
them. In a check, `x` is the run: it has the run's labels but no value of its own.
"""

from __future__ import annotations

import ast
import inspect
import linecache
import types
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from . import dates
from .errors import DefinitionError, UnknownItem
from .expr import Cell
from .formula import CellValue, Formula, allow_attrs
from .periods import Period

if TYPE_CHECKING:
    from .model import Item, Model


class Missing(Exception):
    """A cell a formula reads has no value: the formula's cell is missing too."""


def _item_or_label(model: Model, cell: Cell | None, key: Any, root: bool = True) -> Cell:
    """x["cash"]: the item `cash` at the same coords; otherwise a label of x's own item. On a
    cell reached from x (x["results"]["net_sales"]) a key is always a label."""
    if not isinstance(key, str):
        raise DefinitionError(f"x[{key!r}]: x takes an item name or a label, e.g. x['revenue']")
    if not root and cell is not None:
        return cell[key]
    if key in model.items:
        return model[key]
    if cell is None:
        raise UnknownItem(f"x[{key!r}]: no item {key!r}; in a check, x reads items, e.g. x[\"revenue\"]")
    if not any(key in model.dimensions[d] for d in cell.free_dims()):
        raise UnknownItem(f"x[{key!r}]: {key!r} is neither an item nor a label of {cell!r}'s dims "
                          f"({', '.join(cell.free_dims()) or 'none'})")
    return cell[key]


class Bound(CellValue):
    """`x` for one cell: behaves as that cell's value (a number, or a date for a date item);
    x["item"], .lag() etc. read others. With no cell, a check run's `x`."""

    __slots__ = ("_at", "_cell", "_model", "_reads", "_root")

    def __init__(self, model: Model, cell: Cell | None, at: Mapping[str, str], reads: list, root: bool = True):
        # `at` is shared by every x reached from this one; nothing changes it
        self._model, self._cell, self._at, self._reads, self._root = model, cell, at, reads, root

    def _wrap(self, cell: Cell) -> Bound:
        return Bound(self._model, cell, self._at, self._reads, False)

    def _get(self) -> Any:
        """The cell's value, read (and traced) now."""
        if self._cell is None:
            raise DefinitionError("in a check, x is the run, not a cell: read an item, e.g. x['revenue']")
        cell, at = self._cell, self._at
        if len(cell.coords) + len(at) < len(cell.item.dims) or any(
                d not in at and d not in cell.coords for d in cell.item.dims):
            open_dims = [d for d in cell.free_dims() if d not in at]
            raise DefinitionError(
                f"x: {self._cell!r} leaves {', '.join(open_dims)} open, which this cell doesn't have; "
                f"pick a label, e.g. x[{self._cell.item.name!r}].sel({open_dims[0]}=...)"
            )
        self._reads.append((cell, at))
        return self._model._lookup(cell, at)

    def __float__(self) -> float:
        value = self._get()
        if dates.kind(value) != "number":
            raise TypeError(f"{self._cell!r} is a {dates.kind(value)}, not a number")
        return float(value)

    def __int__(self) -> int:
        value = self._get()
        if dates.kind(value) != "number":
            raise TypeError(f"{self._cell!r} is a {dates.kind(value)}, not a number")
        return int(value)

    @property
    def dt(self) -> dates.DatetimeAccessor:
        """Fields of a date cell, as pandas' Series.dt: x["start"].dt.month, .dt.to_period("M")."""
        value = self._get()
        if not isinstance(value, date):
            raise DefinitionError(f"{self._cell!r}.dt: .dt is for date and datetime items; it is a {dates.kind(value)}")
        return dates.DatetimeAccessor(value)

    # reading other cells
    def __getitem__(self, key: str) -> Bound:
        model = self._model
        if self._root and type(key) is str and key in model.items:  # x["revenue"], the common case
            return Bound(model, model[key], self._at, self._reads, False)
        return self._wrap(_item_or_label(model, self._cell, key, self._root))

    def _own(self, method: str) -> Cell:
        if self._cell is None:
            raise DefinitionError(f"in a check, x is the run, not a cell: use x['item'].{method}(...)")
        return self._cell

    def sel(self, **coords: str) -> Bound:
        return self._wrap(self._own("sel").sel(**coords))

    def shift(self, **steps: int) -> Bound:
        return self._wrap(self._own("shift").shift(**steps))

    def lag(self, dim: str, n: int = 1) -> Bound:
        return self._wrap(self._own("lag").lag(dim, n))

    def lead(self, dim: str, n: int = 1) -> Bound:
        return self._wrap(self._own("lead").lead(dim, n))

    @property
    def coords(self) -> dict[str, str]:
        """This cell's labels, e.g. {"period": "FY2027"}."""
        return dict(self._at)

    def _label(self, dim: str) -> str:
        if dim not in self._at:
            raise DefinitionError(f"x has no {dim!r} label; this cell's dims are {', '.join(self._at) or 'none'}")
        return self._at[dim]

    def attrs(self, dim: str) -> dict:
        """The attrs of this cell's label along `dim`, e.g. x.attrs("period") -> {"kind": "forecast"}."""
        label = self._label(dim)
        return dict(self._model.dimensions[dim].attrs(label))

    def _hierarchy(self, dim: str, method: str):
        label = self._label(dim)
        hierarchy = self._model.dimensions[dim].hierarchy
        if hierarchy is None:
            raise DefinitionError(f"x.{method}({dim!r}): {dim!r} is not a hierarchy; "
                                  f"x.labels({dim!r}) gives all its labels")
        return hierarchy, label

    def children(self, dim: str) -> list[str]:
        """The labels under this cell's label in a hierarchy, e.g. ["/products/iphone", ...]; [] on a leaf.
        A rollup check: abs(x["sales"] - sum(x["sales"].sel(segment=c) for c in x.children("segment"))) <= 0.5"""
        hierarchy, label = self._hierarchy(dim, "children")
        return hierarchy.children(label)

    def parent(self, dim: str) -> str | None:
        """The label above this cell's label in a hierarchy; None at the top."""
        hierarchy, label = self._hierarchy(dim, "parent")
        return hierarchy.parent(label)

    def labels(self, dim: str) -> list[str]:
        """All the labels of a dimension, in order."""
        if dim not in self._model.dimensions:
            raise DefinitionError(f"x.labels({dim!r}): no dimension {dim!r}")
        return list(self._model.dimensions[dim].coords)

    @property
    def period(self) -> Period:
        """This cell's period, with its dates: x.period.start_time, x["start"] in x.period."""
        return _the_period(self._model, self._at)

    @property
    def periods(self) -> dict[str, Period]:
        """This cell's period on each of its time dims, e.g. x.periods["month"]."""
        return _periods(self._model, self._at)

    def is_nil(self) -> bool:
        """The cell was entered as tm.NIL ("—"), not as a number such as 0; a cell with no value
        is missing, not nil."""
        cell = self._own("is_nil")
        self._reads.append((cell, self._at))
        value = self._model._value(cell, self._at)
        if value is None:
            raise Missing
        from .values import NIL

        return value is NIL

    # behaving as its value: a number, or a date (date - date is a timedelta, as in pandas)
    def __add__(self, o): return self._get() + _num(o)
    def __radd__(self, o): return _num(o) + self._get()
    def __sub__(self, o): return self._get() - _num(o)
    def __rsub__(self, o): return _num(o) - self._get()
    def __mul__(self, o): return self._get() * _num(o)
    def __rmul__(self, o): return _num(o) * self._get()
    def __truediv__(self, o): return self._get() / _num(o)
    def __rtruediv__(self, o): return _num(o) / self._get()
    def __pow__(self, o): return self._get() ** _num(o)
    def __rpow__(self, o): return _num(o) ** self._get()
    def __neg__(self): return -self._get()
    def __pos__(self): return +self._get()
    def __abs__(self): return abs(self._get())
    def __round__(self, n=None): return round(self._get(), n)
    def __lt__(self, o): v = self._get(); return v < _other(v, o)
    def __le__(self, o): v = self._get(); return v <= _other(v, o)
    def __gt__(self, o): v = self._get(); return v > _other(v, o)
    def __ge__(self, o): v = self._get(); return v >= _other(v, o)
    def __eq__(self, o): v = self._get(); return v == _other(v, o)  # type: ignore[override]
    def __ne__(self, o): v = self._get(); return v != _other(v, o)  # type: ignore[override]
    def __bool__(self): return bool(self._get())
    def __format__(self, spec): return format(self._get(), spec)
    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"<{self._cell!r} at {self._at}>" if self._cell is not None else f"<x at {self._at}>"


def _num(x: Any) -> Any:
    return x._get() if isinstance(x, Bound) else x


def _other(value: Any, o: Any) -> Any:
    """The right side of a comparison; a date compares with an ISO string, as in pandas."""
    o = _num(o)
    if isinstance(value, date) and isinstance(o, str):
        return dates.coerce(datetime if isinstance(value, datetime) else date, o, "comparison")
    return o


def _function_source(fn: Callable[..., Any]) -> str | None:
    """The text of a lambda (the lambda expression alone); None when unavailable or not a lambda."""
    code = getattr(fn, "__code__", None)
    if code is None:
        return None
    try:
        filename = inspect.getsourcefile(fn) or code.co_filename
    except TypeError:
        return None
    lines = linecache.getlines(filename)
    if not lines:
        return None
    text = "".join(lines)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    if fn.__name__ != "<lambda>":
        return None  # a def: a formula is one lambda
    found = [n for n in ast.walk(tree) if isinstance(n, ast.Lambda) and n.lineno == code.co_firstlineno]
    if len(found) > 1:  # several lambdas on one line: the one whose code matches
        def same(node: ast.Lambda) -> bool:
            try:
                compiled = compile(ast.Expression(node), filename, "eval")
            except (SyntaxError, ValueError):
                return False
            inner = next((c for c in compiled.co_consts if isinstance(c, types.CodeType)), None)
            return inner is not None and inner.co_code == code.co_code
        found = [n for n in found if same(n)]
    if len(found) != 1:
        return None
    return ast.get_source_segment(text, found[0])


def _periods(model: Model, at: Mapping[str, str]) -> dict[str, Period]:
    return {d: model.dimensions[d].period(label) for d, label in at.items() if model.dimensions[d].time is not None}


def _the_period(model: Model, at: Mapping[str, str]) -> Period:
    found = _periods(model, at)
    if len(found) == 1:
        return next(iter(found.values()))
    if not found:
        raise DefinitionError(f"x.period: none of this cell's dims ({', '.join(at) or 'none'}) is a time "
                              "dimension; make one with tm.period_range(...)")
    raise DefinitionError(f"x.period: this cell has several time dims ({', '.join(found)}); "
                          f"use x.periods[{next(iter(found))!r}]")


_BOUND_ATTRS = {"attrs", "children", "coords", "dt", "is_nil", "labels", "lag", "lead", "parent", "period",
                "periods", "sel", "shift"}
allow_attrs(Bound, _BOUND_ATTRS)


def run(formula: Formula, model: Model, item: Item, full: Mapping[str, str], reads: list) -> Any:
    """A formula's value for one cell: a number, a date, tm.NIL or None (missing)."""
    result = formula.call(Bound(model, model[item.name], full, reads))
    if isinstance(result, Bound):
        return result._get()
    if result is None or isinstance(result, (int, float, date, timedelta)) and not isinstance(result, bool):
        return result  # the model checks it against the item's dtype
    from .values import NIL

    if result is NIL:
        return result
    raise DefinitionError(f"formula {formula!r} returned {result!r} for {item.name} {dict(full)}; "
                          "it must return a number, or a date for a date item")


def run_check(formula: Formula, model: Model, at: Mapping[str, str], reads: list) -> Any:
    """A check's result for one run: True, False, or something else (an error)."""
    return formula.call(Bound(model, None, dict(at), reads))


__all__ = ["Bound", "Missing", "run", "run_check"]

"""Formulas written as Python functions of the cell: formula=lambda x: max(0, 25_000 - x["cash"]).

A function formula is first called once with symbolic cells (`Here`). If it only uses the
expression grammar it returns an expression, and the formula is stored as that expression.
Otherwise it is kept as a `CellFunction` and called for each cell with `x`, a `Bound`: numbers
on demand, so plain Python (max, min, if/else, math) works.
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
from .errors import DefinitionError
from .expr import Cell, Condition, Expr

if TYPE_CHECKING:
    from .model import Item, Model


def _item_or_label(cell: Cell, key: str) -> Cell:
    """x["cash"]: the item `cash` at the same coords; otherwise a label of x's own item."""
    model = cell.model
    if key in model.items:
        return model[key]
    return cell[key]


class Here(Cell):
    """The symbolic `x` a function formula is first called with."""

    def __getitem__(self, key: str) -> Cell:  # type: ignore[override]
        return _item_or_label(self, key)


class Bound:
    """`x` for one cell: behaves as that cell's value (a number, or a date for a date item);
    x["item"], .lag() etc. read others."""

    __slots__ = ("_at", "_cell", "_model", "_reads")

    def __init__(self, model: Model, cell: Cell, at: Mapping[str, str], reads: list):
        self._model, self._cell, self._at, self._reads = model, cell, dict(at), reads

    def _wrap(self, cell: Cell) -> Bound:
        return Bound(self._model, cell, self._at, self._reads)

    def _get(self) -> Any:
        """The cell's value, read (and traced) now."""
        open_dims = [d for d in self._cell.free_dims() if d not in self._at]
        if open_dims:
            raise DefinitionError(
                f"x: {self._cell!r} leaves {', '.join(open_dims)} open, which this cell doesn't have; "
                f"pick a label, e.g. x[{self._cell.item.name!r}].sel({open_dims[0]}=...)"
            )
        self._reads.append((self._cell, self._at))
        return self._model._lookup(self._cell, self._at)

    def __float__(self) -> float:
        value = self._get()
        if dates.kind(value) != "number":
            raise TypeError(f"{self._cell!r} is a {dates.kind(value)}, not a number")
        return float(value)

    @property
    def dt(self) -> dates.DatetimeAccessor:
        """Fields of a date cell, as pandas' Series.dt: x["start"].dt.month, .dt.to_period("M")."""
        value = self._get()
        if not isinstance(value, date):
            raise DefinitionError(f"{self._cell!r}.dt: .dt is for date and datetime items; it is a {dates.kind(value)}")
        return dates.DatetimeAccessor(value)

    # reading other cells
    def __getitem__(self, key: str) -> Bound:
        return self._wrap(_item_or_label(self._cell, key))

    def sel(self, **coords: str) -> Bound:
        return self._wrap(self._cell.sel(**coords))

    def shift(self, **steps: int) -> Bound:
        return self._wrap(self._cell.shift(**steps))

    def lag(self, dim: str, n: int = 1) -> Bound:
        return self._wrap(self._cell.lag(dim, n))

    def lead(self, dim: str, n: int = 1) -> Bound:
        return self._wrap(self._cell.lead(dim, n))

    @property
    def coords(self) -> dict[str, str]:
        """This cell's labels, e.g. {"period": "FY2027"}."""
        return dict(self._at)

    def attrs(self, dim: str) -> dict:
        """The attrs of this cell's label along `dim`, e.g. x.attrs("period") -> {"kind": "forecast"}."""
        if dim not in self._at:
            raise DefinitionError(f"x has no {dim!r} label; this cell's dims are {', '.join(self._at)}")
        return dict(self._model.dimensions[dim].attrs(self._at[dim]))

    def is_nil(self) -> bool:
        self._reads.append((self._cell, self._at))
        return self._model._is_nil(self._cell, self._at)

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
        return f"<{self._cell!r} at {self._at}>"


def _num(x: Any) -> Any:
    return x._get() if isinstance(x, Bound) else x


def _other(value: Any, o: Any) -> Any:
    """The right side of a comparison; a date compares with an ISO string, as in pandas."""
    o = _num(o)
    if isinstance(value, date) and isinstance(o, str):
        return dates.coerce(datetime if isinstance(value, datetime) else date, o, "comparison")
    return o


class CellFunction:
    """A formula kept as a Python function, called once per cell with `x` (a Bound)."""

    def __init__(self, fn: Callable[..., Any], source: str | None = None, env: Mapping[str, Any] | None = None):
        self.fn = fn
        self._source, self._env = source, dict(env) if env is not None else None

    @property
    def source(self) -> str | None:
        if self._source is None:
            self._source = _function_source(self.fn)
        return self._source

    def __call__(self, x: Bound) -> Any:
        return self.fn(x)

    def __repr__(self) -> str:
        return self.source or f"<function {self.fn.__name__}>"

    # == saving ==
    def to_data(self, model: Model) -> dict[str, Any]:
        """{"python": source, "env": {name: value}}: the source plus the names it uses."""
        if self.source is None:
            raise DefinitionError(
                f"can't save formula {self.fn!r}: its source isn't available "
                "(was it typed at a plain python prompt or built with exec?)"
            )
        if self._env is not None:
            env = self._env
        else:
            env = {}
            cv = inspect.getclosurevars(self.fn)
            for name, value in {**cv.globals, **cv.nonlocals}.items():
                env[name] = _env_value(name, value, model, self.source)
            env |= _default_names(self.fn, self.source, model)
        return {"python": self.source, "env": env}

    @classmethod
    def from_data(cls, data: Mapping[str, Any], model: Model) -> CellFunction:
        namespace: dict[str, Any] = {}
        for name, value in data.get("env", {}).items():
            namespace[name] = _env_restore(value, model)
        exec(compile(f"__formula__ = ({data['python']})", "<tracemodel formula>", "exec"), namespace)  # noqa: S102
        return cls(namespace["__formula__"], data["python"], data.get("env", {}))


def _default_names(fn: Callable[..., Any], source: str, model: Model) -> dict[str, Any]:
    """lambda x, k=k: ...: the default is evaluated where the lambda is written, so save k's value."""
    args = ast.parse(source, mode="eval").body.args  # type: ignore[attr-defined]
    kwdefaults = getattr(fn, "__kwdefaults__", None) or {}
    pairs = [*zip(args.defaults, getattr(fn, "__defaults__", None) or ()),
             *((d, kwdefaults[a.arg]) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None)]
    env = {}
    for node, value in pairs:
        if isinstance(node, ast.Constant):
            continue
        if not isinstance(node, ast.Name):
            raise DefinitionError(
                f"can't save formula {source!r}: a default value must be a constant or a name (k=k), "
                f"not {ast.unparse(node)!r}"
            )
        env[node.id] = _env_value(node.id, value, model, source)
    return env


def _env_value(name: str, value: Any, model: Model, source: str) -> Any:
    if value is model:
        return {"$model": True}
    if isinstance(value, types.ModuleType):
        return {"$module": value.__name__}
    if isinstance(value, Expr):
        from .model import _text

        return {"$expr": _text(value)}
    if (saved := dates.env_value(value)) is not None:
        return saved
    if _is_json(value):
        return value
    raise DefinitionError(
        f"can't save formula {source!r}: it uses {name!r} = {value!r}; a saved formula may use "
        "numbers, strings, lists and dicts of them, dates, expressions, modules and the model"
    )


def _env_restore(value: Any, model: Model) -> Any:
    if isinstance(value, dict) and len(value) == 1:
        (key, v), = value.items()
        if key == "$model":
            return model
        if key == "$module":
            return __import__(v)
        if key == "$expr":
            from .text import parse

            return parse(v, model)
        if key in dates.ENV_KEYS:
            return dates.env_restore(key, v)
    return value


def _is_json(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json(v) for k, v in value.items())
    return False


def _function_source(fn: Callable[..., Any]) -> str | None:
    """The text of a lambda (the lambda expression alone) or a def; None when unavailable."""
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
        return None  # a def: kept as code, but not savable as one expression
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


def _reads_labels(code: types.CodeType) -> bool:
    """Does the function (or a comprehension in it) use x.coords or x.attrs(...)?"""
    return bool({"coords", "attrs"} & set(code.co_names)) or any(
        _reads_labels(c) for c in code.co_consts if isinstance(c, types.CodeType))


def compile_formula(fn: Callable[..., Any], model: Model, item: Item) -> Expr | CellFunction:
    """An expression when `fn` only uses the expression grammar, else a CellFunction."""
    code = getattr(fn, "__code__", None)
    if code is not None and _reads_labels(code):  # the symbolic x has no labels to branch on
        return CellFunction(fn)
    try:
        result = fn(Here(model, item))
    except Exception:  # noqa: BLE001 - max(), if/else, math, ...: not an expression; run it per cell
        return CellFunction(fn)
    return result if isinstance(result, Expr) else CellFunction(fn)  # an Expr, or x itself (a Here)


def run(fn: CellFunction, model: Model, item: Item, full: Mapping[str, str], reads: list) -> Any:
    """Call a function formula for one cell; numbers, expressions or None (missing) come back."""
    result = fn(Bound(model, Cell(model, item), full, reads))
    if isinstance(result, Bound):
        return result._get()
    if isinstance(result, Expr):
        return result.evaluate(lambda c, a: _track(model, reads, c, a), full)
    if result is None or isinstance(result, (int, float, date, timedelta)) and not isinstance(result, bool):
        return result  # the model checks it against the item's dtype
    from .values import NIL

    if result is NIL:
        return result
    raise DefinitionError(f"formula {fn!r} returned {result!r} for {item.name} {dict(full)}; "
                          "it must return a number, or a date for a date item")


def _track(model: Model, reads: list, cell: Cell, at: Mapping[str, str]) -> float:
    reads.append((cell, dict(at)))
    return model._lookup(cell, at)


# == value checks: expect=lambda x: 0 <= x["margin"] <= 1 =====================

def _check_item(model: Model, key: str) -> Cell:
    if key not in model.items:
        raise DefinitionError(f"x[{key!r}]: no item {key!r}; in a check, x reads items, e.g. x[\"revenue\"]")
    return model[key]


class _CheckHere:
    """The symbolic `x` a value check is first called with."""

    def __init__(self, model: Model):
        self._model = model

    def __getitem__(self, key: str) -> Cell:
        return _check_item(self._model, key)


class CheckX:
    """`x` in a value check: x["item"] reads an item at the run's coords, as a number."""

    __slots__ = ("_at", "_model", "_reads")

    def __init__(self, model: Model, at: Mapping[str, str], reads: list):
        self._model, self._at, self._reads = model, dict(at), reads

    def __getitem__(self, key: str) -> Bound:
        return Bound(self._model, _check_item(self._model, key), self._at, self._reads)

    @property
    def coords(self) -> dict[str, str]:
        """This run's labels, e.g. {"period": "FY2027"}."""
        return dict(self._at)

    def attrs(self, dim: str) -> dict:
        """The attrs of this run's label along `dim`."""
        if dim not in self._at:
            raise DefinitionError(f"x has no {dim!r} label; this run's dims are {', '.join(self._at)}")
        return dict(self._model.dimensions[dim].attrs(self._at[dim]))


def compile_check(fn: Callable[..., Any], model: Model) -> Condition | CellFunction:
    """A condition when `fn` only uses the expression grammar, else a CellFunction run per check run."""
    try:
        result = fn(_CheckHere(model))
    except Exception:  # noqa: BLE001 - and, or, chained comparisons, max, if/else, x.coords: run with numbers
        return CellFunction(fn)
    return result if isinstance(result, Condition) else CellFunction(fn)


__all__ = ["Bound", "CellFunction", "CheckX", "Here", "compile_check", "compile_formula", "run"]

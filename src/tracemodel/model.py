"""The model: dimensions, items scaffold, data quality checks, and the values.

Build in order: add_dimension -> add_item -> add_check -> set_value.
"""

from __future__ import annotations

import inspect
import itertools
import json
import keyword
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Self

from . import dates
from .cellfn import CellFunction, CheckX, compile_check, compile_formula, run
from .dimensions import Dimension, Hierarchy, _parse_entry
from .errors import (
    DefinitionError,
    DimMismatch,
    InvalidValue,
    MissingValue,
    UnknownCoord,
    UnknownItem,
)
from .expr import Cell, Comparison, Condition, Expr, OutOfRange, diff
from .periods import PeriodRange
from .table import Table
from .values import (
    NIL,
    SOURCE_OPTIONAL,
    SOURCE_REQUIRED,
    CellRead,
    CheckResult,
    Entry,
    Report,
    _Nil,
)

DTYPES = {"int": int, "float": float, **dates.DTYPE_NAMES}
SEVERITIES = ("error", "warning")


class _Any:
    """Sentinel for check coords: run once for every declared coord of the dim."""

    def __repr__(self) -> str:
        return "tm.ANY"


ANY = _Any()


class AttrFilter:
    """Check coords: the labels of a dim whose attrs match, e.g. filter_by_attrs(kind="forecast")."""

    def __init__(self, attrs: Mapping[str, Any]):
        self.attrs = dict(attrs)

    def labels(self, dim: Dimension) -> tuple[str, ...]:
        return tuple(c for c in dim.coords
                     if all(k in dim.attrs(c) and dim.attrs(c)[k] == v for k, v in self.attrs.items()))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, AttrFilter) and other.attrs == self.attrs

    def __repr__(self) -> str:
        return f"tm.filter_by_attrs({', '.join(f'{k}={v!r}' for k, v in self.attrs.items())})"


def filter_by_attrs(**attrs: Any) -> AttrFilter:
    """The labels whose attrs match all of `attrs` (named after xarray's Dataset.filter_by_attrs).

    In check coords and report.filter it is resolved against the dim's labels at that time, so
    labels added later with add_coords are included when they match.
    """
    if not attrs:
        raise DefinitionError("filter_by_attrs needs at least one attr, e.g. filter_by_attrs(kind='forecast')")
    return AttrFilter(attrs)


def _labels(dim: Dimension, spec: Any) -> tuple[str, ...]:
    """Check or filter coords: tm.ANY, filter_by_attrs(...), a label or a list of labels."""
    if spec is ANY:
        return dim.coords
    if isinstance(spec, AttrFilter):
        return spec.labels(dim)
    labels = (spec,) if isinstance(spec, str) else tuple(spec)
    for label in labels:
        dim.require(label)
    return labels


@dataclass(frozen=True)
class Item:
    name: str
    dims: tuple[str, ...]
    dtype: type
    attrs: dict[str, Any] = field(default_factory=dict)
    formula: Expr | None = field(default=None, compare=False)  # calculates cells with no entered value
    description: str | None = None  # what the item is, as the source titles it: "Lease-related assets and liabilities"

    @property
    def unit_label(self) -> str:
        return " ".join(str(self.attrs[k]) for k in ("units", "scale") if k in self.attrs)


@dataclass(frozen=True)
class Check:
    id: str
    coords: dict[str, tuple[str, ...]]  # dim -> labels the check runs over
    expect: Condition | Callable[..., Condition]  # a CellFunction for a value check
    description: str | None = None
    params: tuple[str, ...] = ()  # the dims a function `expect` takes, in order
    conditions: dict[tuple[str, ...], Condition] = field(default_factory=dict)  # labels -> its condition
    severity: str = "error"  # "error" or "warning"
    dynamic: dict[str, Any] = field(default_factory=dict)  # dim -> tm.ANY or filter_by_attrs, re-resolved on add_coords

    def runs(self) -> list[dict[str, str]]:
        """One coords mapping per run: the cross product of the check's coords."""
        dims = list(self.coords)
        return [dict(zip(dims, combo)) for combo in itertools.product(*self.coords.values())]

    def condition(self, at: Mapping[str, str]) -> Condition:
        """The condition a run tests: `expect` itself, or what the function built for these labels."""
        if not self.params:
            return self.expect  # type: ignore[return-value]
        return self.conditions[tuple(at[p] for p in self.params)]


class _Missing(Exception):
    pass


class _Defer(BaseException):  # BaseException: a formula's own `except Exception` must not swallow it
    """A calculation got deep: calculate `key` first, then retry."""

    def __init__(self, key: tuple[str, tuple[str, ...]]):
        self.key = key


_MAX_DEPTH = 40  # calculated cells nested in one calculation before it defers


class Model:
    def __init__(self) -> None:
        self.dimensions: dict[str, Dimension] = {}
        self.items: dict[str, Item] = {}
        self.checks: dict[str, Check] = {}
        self._entries: dict[tuple[str, tuple[str, ...]], Entry] = {}
        self._cache: dict[tuple[str, tuple[str, ...]], Any] = {}  # calculated cells, until an input changes
        self._evaluating: set[tuple[str, tuple[str, ...]]] = set()  # cells being calculated, to catch loops
        self._calculating: list[tuple[str, tuple[str, ...]]] = []  # the same, innermost last: who is reading
        self._deps: dict[tuple[str, tuple[str, ...]], list] = {}  # what a function formula read, per cell
        # cell -> its dependents: the cached cells and check runs that read it, so a new value
        # recalculates only those (Excel's dependency tree)
        self._dependents: dict[tuple[str, tuple[str, ...]], set[tuple[str, tuple[str, ...]]]] = {}
        self._runs: dict[tuple[str, tuple[tuple[str, str], ...]], CheckResult] = {}  # check runs, until a read changes
        self._run_dependents: dict[tuple[str, tuple[str, ...]], set[tuple[str, tuple[tuple[str, str], ...]]]] = {}
        self._tx: Transaction | None = None

    # == transactions =======================================================
    def transaction(self) -> Transaction:
        """Group changes: checks that need the whole picture (circular formulas) run once, at
        commit, and any failure undoes every change made in the transaction.

            with model.transaction():
                for fy in FORECAST:
                    model.set_value(...)
            # committed here; an exception inside the block rolls everything back

        Reads (get, table, check) inside the transaction see the changes made so far.
        """
        if self._tx is not None:
            raise DefinitionError("a transaction is already open; commit or roll it back first")
        self._tx = Transaction(self)
        return self._tx

    def _changed(self) -> None:
        """The definition changed: forget everything calculated."""
        self._cache.clear()
        self._deps.clear()
        self._dependents.clear()
        self._runs.clear()
        self._run_dependents.clear()

    def _value_changed(self, key: tuple[str, tuple[str, ...]]) -> None:
        """A cell's entry changed: forget the cells and check runs that read it, and theirs."""
        stack, seen = [key], {key}
        while stack:
            k = stack.pop()
            self._cache.pop(k, None)
            self._deps.pop(k, None)
            for run_key in self._run_dependents.pop(k, ()):
                self._runs.pop(run_key, None)
            for d in self._dependents.pop(k, ()):
                if d not in seen:
                    seen.add(d)
                    stack.append(d)

    def _check_cycles_now_or_at_commit(self, name: str, starts: list[tuple[str, ...]] | None) -> None:
        """`starts` None: every cell of the item."""
        if self._tx is not None:
            self._tx._pending.append((name, starts))
            return
        if starts is None:
            starts = list(itertools.product(*(self.dimensions[d].coords for d in self.items[name].dims)))
        self._check_cycles(name, starts)

    # == 1. dimensions ======================================================
    def add_dimension(self, name: str, coords: Iterable[str] | Hierarchy) -> Dimension:
        """Add a dimension. Re-adding the same definition (e.g. a re-run cell) is a no-op."""
        dim = Dimension(name, coords)
        if name in self.dimensions:
            old = self.dimensions[name]
            if _definition(old) == _definition(dim):
                return old
            raise DefinitionError(f"dimension {name!r} already exists with different coords, descriptions or attrs")
        self.dimensions[name] = dim
        return dim

    def add_coords(self, name: str, entries: Iterable[Any] | PeriodRange) -> Dimension:
        """Append labels to a dimension, e.g. forecast periods. A time dimension takes the
        periods that continue it: add_coords("month", tm.period_range("2027-01", periods=12)).

        Items get the new cells (not entered). Checks over the dimension with tm.ANY are
        re-expanded to cover the new labels; one that can't be (a function check loaded from a
        file, whose function isn't available) raises, and nothing changes. Re-adding labels
        that are already there with the same entries is a no-op.
        """
        if name not in self.dimensions:
            raise UnknownCoord(f"no dimension {name!r}; declared: {', '.join(self.dimensions)}")
        old = self.dimensions[name]
        if old.time is not None:
            if not isinstance(entries, PeriodRange):
                raise DefinitionError(f"{name!r} is a time dimension: add periods with tm.period_range(...), "
                                      f"e.g. tm.period_range({old.time.periods[-1].shift(1).label!r}, periods=12, "
                                      f"freq={old.time.freq!r})")
            entries = old.time.extended(entries)
            if len(entries) == len(old.coords):
                return old
        current = [_entry_tuple(old, c) for c in old.coords]
        new = [_parse_entry(e) for e in entries]
        repeated = [e for e in new if e[0] in old.coords]
        changed = [e[0] for e in repeated if e != _entry_tuple(old, e[0])]
        if changed:
            raise DefinitionError(f"dimension {name!r} already has {', '.join(map(repr, changed))} with a different description or attrs")
        added = [e for e in new if e[0] not in old.coords]
        if not added:
            return old
        all_entries = current + added
        dim = Dimension(name, entries if old.time is not None else Hierarchy(all_entries) if old.hierarchy
                        else all_entries)
        checks = dict(self.checks)
        self.dimensions[name] = dim
        self._changed()
        try:
            for check in checks.values():
                if name in check.dynamic:
                    self._reexpand(check, name)
        except BaseException:
            self.dimensions[name], self.checks = old, checks
            self._changed()
            raise
        return dim

    def _reexpand(self, check: Check, dim: str) -> None:
        """Re-add a check whose tm.ANY coords now cover more labels."""
        coords = {d: check.dynamic.get(d, labels) for d, labels in check.coords.items()}
        if check.params and isinstance(check.expect, _ByLabel):
            if dim in check.params:
                raise DefinitionError(
                    f"check {check.id!r} was loaded from a file and builds its condition per "
                    f"{dim} label, so it can't cover new labels by itself; "
                    f"add it again with its function: model.add_check({check.id!r}, expect=lambda ...)"
                )
            run_over = {d: _labels(self.dimensions[d], check.dynamic[d]) if d in check.dynamic else labels
                        for d, labels in check.coords.items()}
            self._install_check(Check(check.id, run_over, check.expect, check.description,
                                      check.params, check.conditions, check.severity, check.dynamic))
            return
        self.add_check(check.id, check.expect, coords, check.description, check.severity)

    def remove_dimension(self, name: str) -> None:
        """Remove a dimension that no item uses (so it has no cells, values or checks)."""
        if name not in self.dimensions:
            raise UnknownCoord(f"no dimension {name!r}; declared: {', '.join(self.dimensions)}")
        users = [i.name for i in self.items.values() if name in i.dims]
        if users:
            raise DefinitionError(
                f"can't remove dimension {name!r}: items {', '.join(users)} use it"
            )
        del self.dimensions[name]
        self._changed()

    # == 2. items scaffold ==================================================
    def add_item(
        self,
        name: str,
        dims: Iterable[str],
        dtype: type | str = float,
        attrs: Mapping[str, Any] | None = None,
        formula: Expr | Callable[..., Any] | None = None,
        description: str | None = None,
    ) -> Item:
        """Declare a line item: its dims, data type (int, float, date or datetime), attrs
        (units, scale, ...) and a description, the item's title as people read it
        ("Lease-related assets and liabilities"), as a dimension's labels have.

        With `formula`, the item's cells are calculated; a value entered with set_value
        overrides the formula for its cell. `formula` may be a function of `x`, the cell
        being calculated: lambda x: max(0, 25_000 - x["cash"]); x["item"] reads another item at
        the same coords, x.lag("period") this item's previous period.
        """
        if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name) or name == "abs":
            raise DefinitionError(f"item name {name!r} must be a Python identifier, e.g. {_identifier(name)!r}")
        dims = tuple(dims)
        if len(set(dims)) != len(dims):
            raise DefinitionError(f"item {name!r} repeats a dim")
        for d in dims:
            if d not in self.dimensions:
                raise DefinitionError(
                    f"item {name!r} uses dim {d!r}, which is not declared; call add_dimension first"
                )
        if isinstance(dtype, str):
            dtype = DTYPES.get(dtype) or dates.dtype_from_name(dtype)  # type: ignore[assignment]
        if not any(dtype is t for t in (int, float, date, datetime)):
            raise DefinitionError(f"item {name!r}: dtype must be int, float, date or datetime, got {dtype!r}")
        if attrs is not None and not isinstance(attrs, Mapping):
            raise DefinitionError(f"item {name!r}: attrs must be a dict")
        if description is not None and not isinstance(description, str):
            raise DefinitionError(f"item {name!r}: description must be a string")
        item = Item(name, dims, dtype, dict(attrs or {}), description=description)
        old = self.items.get(name)
        if old is not None and old != item:
            raise DefinitionError(f"item {name!r} already exists with a different definition")
        self.items[name] = old or item  # declared first, so a formula can read its own cells
        try:
            if callable(formula) and not isinstance(formula, (Expr, Condition, CellFunction)):
                formula = compile_formula(formula, self, self.items[name])
            if formula is not None and not isinstance(formula, CellFunction):
                self._check_formula(f"item {name!r}: formula", dims, formula)
            if old is not None:
                if _same_formula(old.formula, formula):
                    return old
                raise DefinitionError(f"item {name!r} already exists with a different formula")
            self.items[name] = item = Item(name, dims, dtype, dict(attrs or {}), formula, description)
            self._changed()
            if formula is not None:
                self._check_cycles_now_or_at_commit(name, None)
        except BaseException:
            if old is None:
                del self.items[name]
            self._changed()
            raise
        return item

    def _check_formula(self, what: str, dims: tuple[str, ...], formula: object) -> None:
        if isinstance(formula, Condition) or not isinstance(formula, Expr):
            raise DefinitionError(
                f"{what} must be an expression such as model['a'] - model['b'], got {formula!r}"
            )
        if any(c.model is not self for c in formula.cells()):
            raise DefinitionError(f"{what} reads items of another model")
        extra = [d for d in formula.free_dims() if d not in dims]
        if extra:
            raise DefinitionError(
                f"{what} {formula!r} is open in {', '.join(extra)}, which the item doesn't have; "
                f"add the dim to the item or fix it in the formula, e.g. .sel({extra[0]}=...)"
            )

    def _formula_of(self, item: Item, labels: tuple[str, ...]) -> Expr | CellFunction | None:
        """The formula that calculates a cell: its own (set_value), else the item's if not entered."""
        e = self._entries.get((item.name, labels))
        if e is not None:
            return e.value if _is_formula(e.value) else None
        return self.items[item.name].formula  # by name: a self-reference holds the item as first declared

    def _check_cycles(self, name: str, starts: Iterable[tuple[str, ...]]) -> None:
        """Raise if a formula cell depends on itself, e.g. revenue = revenue.lag + revenue.lead."""
        done: set[tuple[str, tuple[str, ...]]] = set()

        def visit(key: tuple[str, tuple[str, ...]], path: list[tuple[str, tuple[str, ...]]]) -> None:
            if key in done:
                return
            if key in path:
                loop = path[path.index(key):] + [key]
                raise DefinitionError("circular formula: " + " -> ".join(f"{n}{_fmt(dict(zip(self.items[n].dims, l)))}" for n, l in loop))
            item = self.items[key[0]]
            formula = self._formula_of(item, key[1])
            if isinstance(formula, Expr):  # a function formula's reads are known only when it runs
                at = dict(zip(item.dims, key[1]))
                for c in formula.cells():
                    try:
                        full = c.resolve(at)
                    except OutOfRange:
                        continue
                    visit((c.item.name, tuple(full[d] for d in c.item.dims)), path + [key])
            done.add(key)

        for labels in starts:
            visit((name, tuple(labels)), [])

    def item(self, name: str) -> Item:
        if name not in self.items:
            raise UnknownItem(f"{name!r} is not a declared item; declared: {', '.join(self.items)}")
        return self.items[name]

    def __getitem__(self, name: str) -> Cell:
        """A line item as an expression: index it (["/"]) or .sel(...) to fix coords."""
        return Cell(self, self.item(name))

    # == 3. data quality ====================================================
    def add_check(
        self,
        id: str,
        expect: Condition | Callable[..., Condition],
        coords: Mapping[str, Any] | None = None,
        description: str | None = None,
        severity: str = "error",
    ) -> Check:
        """Add a check, or replace the check with the same id.

        `expect` is a condition, or a function whose parameters are dims in `coords`: it is called
        once per combination of their labels, and returns the condition for those runs. A
        function of `x` alone is a value check: it is called once per run with the run's
        numbers (x["item"]) and returns True or False.
        `severity` is "error" (the default) or "warning": a failing warning doesn't make the
        report fail.
        """
        if severity not in SEVERITIES:
            raise DefinitionError(f"check {id!r}: severity must be 'error' or 'warning', got {severity!r}")
        if not isinstance(expect, Condition) and not callable(expect):
            raise DefinitionError(
                f"check {id!r}: `expect` must be a condition, e.g. model['x'] >= 0 "
                "or abs(a - b) <= 0.2, or a function of dims in coords returning one"
            )
        run_over: dict[str, tuple[str, ...]] = {}
        dynamic = {d: spec for d, spec in (coords or {}).items() if spec is ANY or isinstance(spec, AttrFilter)}
        for dim, spec in (coords or {}).items():
            if dim not in self.dimensions:
                raise UnknownCoord(f"check {id!r}: no dimension {dim!r}")
            run_over[dim] = _labels(self.dimensions[dim], spec)
        params: tuple[str, ...] = ()
        conditions: dict[tuple[str, ...], Condition] = {}
        if not isinstance(expect, (Condition, CellFunction)) and _is_value_check(id, expect, run_over):
            expect = compile_check(expect, self)  # a condition when it fits the expression grammar
        if isinstance(expect, CellFunction):
            return self._install_check(Check(id, run_over, expect, description, (), {}, severity, dynamic))
        if not isinstance(expect, Condition):
            params = _params(id, expect, run_over)
            for labels in itertools.product(*(run_over[p] for p in params)):
                cond = expect(**dict(zip(params, labels)))
                if not isinstance(cond, Condition):
                    where = ", ".join(f"{p}={v!r}" for p, v in zip(params, labels))
                    raise DefinitionError(
                        f"check {id!r}: `expect` returned {cond!r} for {where}; it must return a "
                        "condition, e.g. x >= 0"
                    )
                conditions[labels] = cond
        return self._install_check(
            Check(id, run_over, expect, description, params, conditions, severity, dynamic))

    def _install_check(self, check: Check) -> Check:
        """Validate a check against the model's dims and labels, then add it."""
        id, run_over, params = check.id, check.coords, check.params
        if isinstance(check.expect, CellFunction):  # a value check: its reads are known only as it runs
            self.checks[id] = check
            self._forget_runs(id)
            return check
        built = list(check.conditions.values()) if params else [check.expect]
        for cond in built:
            for cell in cond.cells():
                open_dims = [d for d in cell.free_dims() if d not in run_over]
                if open_dims:
                    raise DefinitionError(
                        f"check {id!r}: {cell!r} leaves {', '.join(open_dims)} unresolved; "
                        f"fix it in the cell or add it to coords, e.g. coords={{{open_dims[0]!r}: tm.ANY}}"
                    )
        used = set(params).union(*(c.free_dims() for c in built))
        unused = [d for d in run_over if d not in used]
        if unused:
            raise DefinitionError(
                f"check {id!r}: coords give {', '.join(unused)}, but every cell already fixes it; "
                "remove it from coords"
            )
        for at in check.runs():  # a lag or lead must land on a declared label in every run
            for cell in check.condition(at).cells():
                try:
                    cell.resolve(at)
                except OutOfRange as e:
                    keep = f"[{e.steps}:]" if e.steps > 0 else f"[:{e.steps}]"
                    run = ", ".join(f"{d}={v}" for d, v in at.items())
                    raise DefinitionError(
                        f"check {id!r}: {e} (run {run}); leave that label out of coords, e.g. "
                        f"coords={{{e.dim!r}: model.dimensions[{e.dim!r}].coords{keep}}}"
                    ) from None
        self.checks[id] = check
        self._forget_runs(id)
        return check

    def _forget_runs(self, id: str) -> None:
        for run_key in [r for r in self._runs if r[0] == id]:
            del self._runs[run_key]

    def check(self) -> Report:
        """Run every check against the current values."""
        return Report([self._cached_run(check, at) for check in self.checks.values() for at in check.runs()], self)

    def _cached_run(self, check: Check, at: dict[str, str]) -> CheckResult:
        """A run's result, kept until a cell it read changes."""
        run_key = (check.id, tuple(at.items()))
        result = self._runs.get(run_key)
        if result is None:
            result = self._run(check, at)
            if result.error is None:  # a run that raised may not have read everything it needs
                self._runs[run_key] = result
                for r in result.cells:
                    self._run_dependents.setdefault((r.item, tuple(r.coords.values())), set()).add(run_key)
        return result

    def _run(self, check: Check, at: dict[str, str]) -> CheckResult:
        if isinstance(check.expect, CellFunction):
            return self._run_value_check(check, at)
        cond = check.condition(at)
        reads: list[CellRead] = []
        try:
            self._reads(cond.cells(), at, reads)
            if any(r.value is None for r in reads):
                return CheckResult(check, at, None, None, False, reads, cond)  # missing never counts as 0
            lhs, rhs = self._sides(cond, at)
            return CheckResult(check, at, lhs, rhs, self._test(cond, at) is True, reads, cond)
        except Exception as e:  # noqa: BLE001 - a cell this run reads can't be calculated: fail the run
            return CheckResult(check, at, None, None, False, reads, cond, f"{type(e).__name__}: {e}")

    def _run_value_check(self, check: Check, at: dict[str, str]) -> CheckResult:
        """Call the function with this run's numbers; a missing cell, an error or a non-bool fails."""
        raw: list = []
        result: Any = None
        error = None
        try:
            result = check.expect(CheckX(self, at, raw))
        except _Missing:
            pass
        except Exception as e:  # noqa: BLE001 - any error in the check's own code fails the run
            error = f"{type(e).__name__}: {e}"
        reads: list[CellRead] = []
        for cell, cell_at in raw:
            self._reads([cell], cell_at, reads)
        if error is None and result is not None and not isinstance(result, bool):
            error = f"returned {result!r}; a value check must return True or False"
        passed = result is True and error is None and not any(r.value is None for r in reads)
        return CheckResult(check, at, None, None, passed, reads, check.expect, error)

    def _reads(self, cells: list[Cell], at: Mapping[str, str], reads: list[CellRead]) -> None:
        """The cells read, each followed by the inputs of a formula that calculated it."""
        for c in cells:
            full = c.resolve(at)
            read = CellRead(c.item.name, full, self._value(c, at))
            if read in reads:  # a condition may read the same cell twice
                continue
            reads.append(read)
            labels = tuple(full[d] for d in c.item.dims)
            formula = self._formula_of(c.item, labels)
            if isinstance(formula, CellFunction):
                for dep, dep_at in self._deps.get((c.item.name, labels), []):
                    self._reads([dep], dep_at, reads)
            elif formula is not None:
                self._reads(formula.cells(), full, reads)

    # == 4. values ==========================================================
    def set_value(
        self, line_item: str, coords: Mapping[str, str], value: float, source: Mapping[str, Any]
    ) -> None:
        """Enter a value with its source. The last write wins; the value it replaces, and that
        value's source, are kept in the entry's history. On a formula item it overrides the
        formula for this cell.

        `value` may be an expression (or a function of the item's own cells): a formula for
        this one cell, e.g. a contract's escalator, sourced like any value.
        """
        key, full = self._key(line_item, coords)
        item = self.items[line_item]
        if isinstance(value, (Expr, Condition)) or callable(value):
            if callable(value) and not isinstance(value, (Expr, Condition, CellFunction)):
                value = compile_formula(value, self, item)
            if not isinstance(value, CellFunction):
                self._check_formula(f"{line_item}{_fmt(full)}: formula", item.dims, value)
        if item.dtype in dates.TEMPORAL and value is not NIL and not _is_formula(value):
            value = dates.coerce(item.dtype, value, f"{line_item}{_fmt(full)}")
        self._validate(item, value, source)
        previous = self._entries.get(key)
        if previous is not None:
            self._entries[key] = Entry(line_item, full, value, dict(source),
                                       [*previous.history, (previous.value, previous.source)],
                                       previous.overrides_formula)
        else:
            self._entries[key] = Entry(line_item, full, value, dict(source),
                                       overrides_formula=item.formula is not None)
        self._value_changed(key)
        if _is_formula(value):
            try:
                self._check_cycles_now_or_at_commit(line_item, [key[1]])
            except DefinitionError:
                if previous is None:
                    del self._entries[key]
                else:
                    self._entries[key] = previous
                self._value_changed(key)
                raise

    def get(self, line_item: str, **coords: str) -> float:
        """The cell's value; a nil cell gives 0 (its entry keeps tm.NIL), a nil date tm.NIL."""
        item = self.item(line_item)
        key, full = self._key(line_item, coords)
        formula = self._formula_of(item, key[1])
        if formula is None:
            value = self._entry(line_item, coords).value
        else:
            value = self._value(Cell(self, item, full), {})
            if value is None:
                raise MissingValue(f"{line_item}{_fmt(full)}: its formula {formula!r} reads a cell with no value")
        return 0 if value is NIL and item.dtype not in dates.TEMPORAL else value

    def entry(self, line_item: str, **coords: str) -> Entry | Table:
        """One entry when coords are complete; otherwise a table of the matching entries."""
        item = self.item(line_item)
        if set(coords) == set(item.dims):
            return self._entry(line_item, coords)
        for d, label in coords.items():
            if d not in item.dims:
                raise DimMismatch(f"{line_item} has no dim {d!r}")
            self.dimensions[d].require(label)
        rows = [
            [*(e.coords[d] for d in item.dims), f"= {e.value!r}" if _is_formula(e.value) else e.value,
             _source_text(e.source),
             len(e.history)]
            for e in self._entries.values()
            if e.item == line_item and all(e.coords[d] == v for d, v in coords.items())
        ]
        return Table([*item.dims, "value", "source", "replaced"], rows, title=f"{line_item} entries")

    def table(self, line_item: str) -> Table:
        """The item as a grid: periods across, other dims down."""
        item = self.item(line_item)
        result = self.evaluate(self[line_item])
        if isinstance(result, Table):
            result.title = f"{line_item} ({item.unit_label})" if item.unit_label else line_item
        return result  # type: ignore[return-value]

    def evaluate(self, expr: Expr | Condition) -> Table | float:
        """Evaluate an expression for every combination of its free dims."""
        free = expr.free_dims()
        combos = [dict(zip(free, c)) for c in itertools.product(*(self.dimensions[d].coords for d in free))] or [{}]

        def result(cond: Condition, at: dict[str, str]) -> bool | str:
            ok = self._test(cond, at)
            return "missing" if ok is None else ok

        if isinstance(expr, Comparison):
            rows = []
            for at in combos:
                lhs, rhs = self._sides(expr, at)
                rows.append([*at.values(), lhs, rhs, diff(lhs, rhs), result(expr, at)])
            return Table([*free, "left", "right", "diff", "result"], rows, title=repr(expr))
        if isinstance(expr, Condition):  # & | ~: each part, then the whole
            rows = [[*at.values(), *(result(p, at) for p in expr.parts), result(expr, at)] for at in combos]
            return Table([*free, *(repr(p) for p in expr.parts), "result"], rows, title=repr(expr))

        if not free:
            value = self._try(expr, {})
            if value is None:
                raise MissingValue(f"{expr!r} reads a cell with no value")
            return value
        if "period" in free:
            others = [d for d in free if d != "period"]
            periods = self.dimensions["period"].coords
            row_keys = list(itertools.product(*(self.dimensions[d].coords for d in others))) or [()]
            rows = [
                [" / ".join(key) or repr(expr),
                 *(self._try(expr, dict(zip(others, key)) | {"period": p}, show_nil=True) for p in periods)]
                for key in row_keys
            ]
            return Table([" / ".join(others) or "", *periods], rows, title=repr(expr))
        rows = [[*at.values(), self._try(expr, at, show_nil=True)] for at in combos]
        return Table([*free, "value"], rows, title=repr(expr))

    def __iter__(self):
        return iter(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    # == persistence ========================================================
    def to_dict(self) -> dict[str, Any]:
        """The definition (dimensions, items, checks) as plain data; values are saved apart."""
        return {
            "format": FORMAT,
            "dimensions": [_dimension_data(d) for d in self.dimensions.values()],
            "items": [_item_data(i, self) for i in self.items.values()],
            "checks": [_check_data(c, self) for c in self.checks.values()],
        }

    def save(self, path: str | Path) -> None:
        """Write the definition to a JSON file; `tm.load(path)` reads it back.

        Expressions are saved as the text repr() shows. Values go in a separate file
        (`to_jsonl`), so the definition and the values can be owned by different people.
        """
        try:
            text = _dumps(self.to_dict())
        except TypeError as e:
            raise DefinitionError(f"can't save: attrs must hold JSON values (str, number, bool, list, dict): {e}") from None
        Path(path).write_text(text + "\n")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], allow_code: bool = False) -> Model:
        """A model from `to_dict()` data. Expressions are parsed, never evaluated as code.

        Formulas saved as Python functions are code: loading them runs it, so it needs
        allow_code=True (run untrusted files in a sandbox).
        """
        from .text import parse

        if data.get("format") != FORMAT:
            raise DefinitionError(f"not a tracemodel definition (format {data.get('format')!r}, expected {FORMAT!r})")
        model = cls()
        for d in data["dimensions"]:
            entries = [e if isinstance(e, str) else tuple(e) for e in d["coords"]]
            if "time" in d:
                spine = PeriodRange.from_data(d["time"], len(entries))
                if list(spine.labels) != entries:
                    raise DefinitionError(f"dimension {d['name']!r}: its coords don't match its time spine {d['time']}")
                model.add_dimension(d["name"], spine)
            else:
                model.add_dimension(d["name"], Hierarchy(entries) if d.get("hierarchy") else entries)
        for i in data["items"]:
            text = i.get("formula")
            if isinstance(text, dict):
                formula: Any = _load_function(text, model, allow_code, f"item {i['name']!r}")
            else:
                formula = (lambda own, text=text: parse(text, model)) if text else None  # after the item exists
            model.add_item(i["name"], i["dims"], i["dtype"], i.get("attrs"), formula=formula,
                           description=i.get("description"))
            if isinstance(text, str) and isinstance(model.items[i["name"]].formula, CellFunction):
                parse(text, model)  # it didn't parse: raise its error
                raise DefinitionError(f"item {i['name']!r}: formula {text!r} is not an expression")
        for c in data["checks"]:
            specs = {dim: _spec_from_data(spec) for dim, spec in c.get("coords", {}).items()}
            for dim in specs:
                if dim not in model.dimensions:
                    raise UnknownCoord(f"check {c['id']!r}: no dimension {dim!r}")
            run_over = {dim: _labels(model.dimensions[dim], spec) for dim, spec in specs.items()}
            dynamic = {d: spec for d, spec in specs.items() if spec is ANY or isinstance(spec, AttrFilter)}
            params = tuple(c.get("params", ()))
            if isinstance(c.get("expect"), dict):  # a value check: Python code
                conditions, expect = {}, _load_function(c["expect"], model, allow_code, f"check {c['id']!r}")
            elif params:
                conditions = {tuple(r["labels"]): _condition_text(parse(r["expect"], model), c["id"])
                              for r in c["expect_by_label"]}
                expect: Any = _ByLabel(params, conditions)
            else:
                conditions, expect = {}, _condition_text(parse(c["expect"], model), c["id"])
            model._install_check(Check(c["id"], run_over, expect, c.get("description"), params,
                                       conditions, c.get("severity", "error"), dynamic))
        return model

    def to_excel(
        self,
        path: str | Path,
        *,
        title: str = "Model",
        sections: Mapping[str, Sequence[str]] | None = None,
        layout: str = "sheet",
        columns: str | None = None,
        split_after: tuple[str, str] | None = None,
        page_breaks: bool = True,
    ) -> None:
        """Write the model to an Excel workbook, for people who read models in Excel.

        Items are blocks one under the other, sharing the columns (`columns`, default: period,
        else each item's last dim that isn't a hierarchy); their other dims read down. `sections`
        groups items, e.g. {"Income statement": ["net_sales", ...], ...}; other items group
        by their dims. layout="sheet" puts the sections that share the columns on one sheet,
        each under a banner and, with `page_breaks`, on its own printed page; layout="sheets"
        gives each its own sheet. `split_after` = (dim, label) draws a line after a column,
        e.g. ("month", "2026-03") between actuals and forecast. Then the model as tables:
        dimensions, items, checks, check results, and every value with its source.
        Needs openpyxl: pip install pytracemodel[excel].
        """
        from .excel import to_excel

        to_excel(self, path, title=title, sections=sections, layout=layout, columns=columns,
                 split_after=split_after, page_breaks=page_breaks)

    def to_jsonl(self, path: str | Path) -> None:
        """Write one JSON object per value."""
        lines = []
        for e in self._entries.values():
            row = e.to_json()
            if _is_formula(e.value):  # a cell formula: text, or a function's source
                del row["value"]
                row = {"line_item": row["line_item"], "coords": row["coords"],
                       "formula": _formula_data(e.value, self)} | row
            lines.append(json.dumps(row) + "\n")
        Path(path).write_text("".join(lines))  # written only once every row could be saved

    def load_jsonl(self, path: str | Path, allow_code: bool = False) -> None:
        """Add the values in a JSON lines file, each validated like set_value.

        The file loads in one transaction (unless one is already open): all of it, or nothing.
        Cell formulas saved as Python functions need allow_code=True, as in tm.load.
        """
        if self._tx is None:
            with self.transaction():
                self.load_jsonl(path, allow_code)
            return
        with open(path) as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    if isinstance(row.get("formula"), dict):
                        where = f"{row['line_item']} {row['coords']}"
                        value: Any = _load_function(row["formula"], self, allow_code, where)
                    elif "formula" in row:
                        from .text import parse

                        value = parse(row["formula"], self)
                    else:
                        value = NIL if row["value"] is None else row["value"]
                    self.set_value(row["line_item"], row["coords"], value, row["source"])

    # == display ============================================================
    def __repr__(self) -> str:
        lines = ["<tracemodel.Model>", "Dimensions:"]
        for d in self.dimensions.values():
            lines.append(f"  {d.name}{' (hierarchy)' if d.hierarchy else ''}")
            width = max(len(c) for c in d.coords)
            for c in d.coords:
                extra = "   ".join(x for x in (d.descriptions.get(c, ""), _kv(d.attrs(c))) if x)
                lines.append(f"    {c:<{width}}   {extra}".rstrip())
        lines.append("Items:")
        width = max((len(i.name) for i in self.items.values()), default=0)
        for i in self.items.values():
            formula = f"  = {i.formula!r}" if i.formula is not None else ""
            described = f"  {i.description}" if i.description else ""
            lines.append(f"  {i.name:<{width}}   ({', '.join(i.dims)})  {i.dtype.__name__}{described}  {_kv(i.attrs)}".rstrip()
                         + formula)
        lines.append("Checks:")
        for c in self.checks.values():
            if c.params:
                labels, first = next(iter(c.conditions.items()))
                shown = (f"expect({', '.join(c.params)}), e.g. "
                         f"{', '.join(f'{p}={v!r}' for p, v in zip(c.params, labels))}: {first!r}")
            else:
                shown = repr(c.expect)
            warning = "  (warning)" if c.severity == "warning" else ""
            per = ", ".join(
                f"{d} ({', '.join(f'{k}={v}' for k, v in c.dynamic[d].attrs.items())})"
                if isinstance(c.dynamic.get(d), AttrFilter) else d for d in c.coords)
            lines.append(f"  {c.id:<6} {shown}  per {per or '-'}{warning}")
        lines.append(f"Values: {len(self)}")
        return "\n".join(lines)

    def _repr_pretty_(self, p, cycle) -> None:
        p.text(repr(self))

    # == helpers ============================================================
    def _key(self, line_item: str, coords: Mapping[str, str]) -> tuple[tuple[str, tuple[str, ...]], dict[str, str]]:
        item = self.item(line_item)
        given, expected = set(coords), set(item.dims)
        if given != expected:
            parts = []
            if expected - given:
                parts.append(f"missing {', '.join(sorted(expected - given))}")
            if given - expected:
                parts.append(f"extra {', '.join(sorted(given - expected))}")
            raise DimMismatch(f"{line_item} takes coords for ({', '.join(item.dims)}): {'; '.join(parts)}")
        for d in item.dims:
            self.dimensions[d].require(coords[d])
        full = {d: coords[d] for d in item.dims}
        return (line_item, tuple(full.values())), full

    def _entry(self, line_item: str, coords: Mapping[str, str]) -> Entry:
        key, full = self._key(line_item, coords)
        if key not in self._entries:
            raise MissingValue(f"{line_item}{_fmt(full)} has no value")
        return self._entries[key]

    def _value(self, cell: Cell, at: Mapping[str, str]) -> float | _Nil | None:
        try:
            full = cell.resolve(at)
        except OutOfRange:  # e.g. the first period's lag, when displaying
            return None
        labels = tuple(full[d] for d in cell.item.dims)
        if self._calculating:  # a formula reads this cell: a change to it recalculates that formula's cell
            self._dependents.setdefault((cell.item.name, labels), set()).add(self._calculating[-1])
        e = self._entries.get((cell.item.name, labels))
        if e is not None and not _is_formula(e.value):
            return e.value
        formula = self._formula_of(cell.item, labels)
        if formula is None:
            return None
        key = (cell.item.name, labels)
        if key in self._cache:
            return self._cache[key]
        if self._evaluating:
            return self._calculate(key, formula, full)
        self._warm(key)  # calculate what it reads first, so long lag chains don't recurse deeply
        # A function formula's reads are known only as it runs: when a calculation gets deep,
        # it stops at the cell it needs (_Defer); that cell is calculated first, then it resumes.
        pending = [key]
        while pending:
            k = pending[-1]
            item = self.items[k[0]]
            try:
                self._calculate(k, self._formula_of(item, k[1]), dict(zip(item.dims, k[1])))
            except _Defer as d:
                if d.key in pending:
                    raise DefinitionError(f"circular formula at {d.key[0]}{_fmt(dict(zip(self.items[d.key[0]].dims, d.key[1])))}") from None
                pending.append(d.key)
                continue
            pending.pop()
        return self._cache[key]

    def _calculate(self, key: tuple[str, tuple[str, ...]], formula: Expr | CellFunction,
                   full: Mapping[str, str]) -> Any:
        if key in self._cache:
            return self._cache[key]
        if key in self._evaluating:  # only possible inside a transaction, before commit checks loops
            raise DefinitionError(f"circular formula at {key[0]}{_fmt(full)}")
        if len(self._evaluating) >= _MAX_DEPTH:
            raise _Defer(key)
        self._evaluating.add(key)
        self._calculating.append(key)
        try:
            if isinstance(formula, CellFunction):
                reads: list = []
                try:
                    value = run(formula, self, self.items[key[0]], full, reads)
                except (_Missing, ZeroDivisionError):
                    value = None
                self._deps[key] = reads
            else:
                value = self._try(formula, full)
        finally:
            self._evaluating.discard(key)
            self._calculating.pop()
        value = _typed(value, self.items[key[0]], formula, full)
        self._cache[key] = value
        return value

    def _warm(self, root: tuple[str, tuple[str, ...]]) -> None:
        """Calculate the cells `root` depends on, deepest first, without recursion."""
        stack, seen = [(root, False)], set()
        while stack:
            key, ready = stack.pop()
            if key in self._cache:
                continue
            item = self.items[key[0]]
            formula = self._formula_of(item, key[1])
            if formula is None:
                continue
            full = dict(zip(item.dims, key[1]))
            if isinstance(formula, CellFunction):  # its reads are known only by running it
                if key != root:
                    self._calculate(key, formula, full)
                continue
            if ready:
                if key != root:
                    self._calculate(key, formula, full)
                continue
            if key in seen:  # a loop: _calculate reports it when it gets there
                continue
            seen.add(key)
            stack.append((key, True))
            for c in formula.cells():
                try:
                    dep = c.resolve(full)
                except OutOfRange:
                    continue
                dep_key = (c.item.name, tuple(dep[d] for d in c.item.dims))
                if dep_key not in self._cache and dep_key not in seen:
                    stack.append((dep_key, False))

    def _is_nil(self, cell: Cell, at: Mapping[str, str]) -> bool:
        return self._value(cell, at) is NIL

    def _lookup(self, cell: Cell, at: Mapping[str, str]) -> float:
        value = self._value(cell, at)
        if value is None:
            raise _Missing
        if value is NIL:
            if cell.item.dtype in dates.TEMPORAL:  # nil counts as 0, but there is no date 0
                raise DefinitionError(
                    f"{cell!r}{_fmt(cell.resolve(at))} is nil: it has no date to compute with; "
                    f"test {cell!r}.is_nil() first"
                )
            return 0
        return value

    def _try(self, expr: Expr, at: Mapping[str, str], show_nil: bool = False) -> float | _Nil | None:
        if show_nil and isinstance(expr, Cell) and self._value(expr, at) is NIL:
            return NIL  # an item's own cell shows "—"; in arithmetic it counts as 0
        try:
            return expr.evaluate(self._lookup, at)
        except (_Missing, ZeroDivisionError):
            return None

    def _test(self, cond: Condition, at: Mapping[str, str]) -> bool | None:
        """True or False; None when a value is missing or a division by zero."""
        try:
            return cond.test(self._lookup, at)
        except (_Missing, ZeroDivisionError):
            return None

    def _sides(self, cond: Condition, at: Mapping[str, str]) -> tuple[float | None, float | None]:
        if isinstance(cond, Comparison):
            return self._try(cond.left, at), self._try(cond.right, at)
        return None, None

    @staticmethod
    def _validate(item: Item, value: object, source: object) -> None:
        if value is NIL or _is_formula(value) or item.dtype in dates.TEMPORAL:  # dates: set_value coerced it
            pass
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidValue(f"{item.name}: value must be a number, got {value!r}")
        elif item.dtype is int and not isinstance(value, int):
            raise InvalidValue(f"{item.name} is int; got {value!r}")
        if not isinstance(source, Mapping):
            raise InvalidValue(f"source must be a dict with {', '.join(SOURCE_REQUIRED)}")
        missing = [k for k in SOURCE_REQUIRED if not str(source.get(k) or "").strip()]
        extra = [k for k in source if k not in SOURCE_REQUIRED + SOURCE_OPTIONAL]
        if missing or extra:
            problems = ([f"missing {', '.join(missing)}"] if missing else []) + (
                [f"unknown {', '.join(extra)}"] if extra else []
            )
            raise InvalidValue(f"source: {'; '.join(problems)}")


def _definition(dim: Dimension) -> tuple:
    time = dim.time.to_data() if dim.time is not None else None
    return dim.coords, dim.hierarchy is not None, dim.descriptions, dim.label_attrs, time


FORMAT = "tracemodel/1"


def _whole(value: float, item: Item, formula: object, full: Mapping[str, str]) -> int:
    """An int item's calculated value: a whole number, allowing for float rounding."""
    nearest = round(value)
    if abs(value - nearest) <= 1e-9 * max(1.0, abs(value)):
        return int(nearest)
    raise DefinitionError(
        f"{item.name}{_fmt(dict(full))} is int, but its formula {formula!r} gave {value!r}; "
        f"round it in the formula, e.g. formula=lambda x: round(...)"
    )


def _typed(value: Any, item: Item, formula: object, full: Mapping[str, str]) -> Any:
    """A calculated value as the item's dtype; a value of another kind is an error in the formula."""
    if value is None or value is NIL:
        return value
    kind = dates.kind(value)
    if item.dtype is int and isinstance(value, float):
        return _whole(value, item, formula, full)
    if (item.dtype in (int, float) and kind == "number") or (item.dtype is date and kind == "date") \
            or (item.dtype is datetime and kind == "datetime"):
        return value
    if item.dtype is datetime and kind == "date":
        return datetime(value.year, value.month, value.day)
    hint = ""
    if kind == "timedelta":
        hint = "; a date minus a date is a timedelta: take its days, e.g. lambda x: (x['end'] - x['start']).days"
    elif item.dtype is date and kind == "datetime":
        hint = "; take its date, e.g. lambda x: x['at'].dt.date"
    raise DefinitionError(
        f"{item.name}{_fmt(dict(full))} is {item.dtype.__name__}, but its formula {formula!r} "
        f"gave a {kind}, {value!r}{hint}"
    )


def _same_formula(a: object, b: object) -> bool:
    if isinstance(a, CellFunction) and isinstance(b, CellFunction):
        return a.fn is b.fn or (a.fn.__code__ == b.fn.__code__ and a.source == b.source)
    return repr(a) == repr(b)


def _is_formula(value: object) -> bool:
    return isinstance(value, (Expr, CellFunction))


class Transaction:
    """Changes to a model that commit together; see Model.transaction()."""

    def __init__(self, model: Model):
        self._model = model
        self._saved = (dict(model.dimensions), dict(model.items), dict(model.checks), dict(model._entries))
        self._pending: list[tuple[str, list[tuple[str, ...]] | None]] = []
        self.open = True

    def commit(self) -> None:
        """Run the deferred checks; on a failure, roll back and raise."""
        self._require_open()
        model, pending, self._pending = self._model, self._pending, []
        model._tx = None  # checks below run as they would outside a transaction
        try:
            for name, starts in pending:
                if name in model.items:
                    model._check_cycles_now_or_at_commit(name, starts)
        except BaseException:
            self._restore()
            raise
        self.open = False

    def rollback(self) -> None:
        """Undo every change made since the transaction began."""
        self._require_open()
        self._restore()

    def _restore(self) -> None:
        m = self._model
        m.dimensions, m.items, m.checks, m._entries = (dict(x) for x in self._saved)
        m._tx = None
        m._changed()
        self.open = False

    def _require_open(self) -> None:
        if not self.open:
            raise DefinitionError("this transaction is already committed or rolled back")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self.open:
            return
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()


class _ByLabel:
    """A loaded function check's `expect`: the condition saved for each label combination."""

    def __init__(self, params: tuple[str, ...], conditions: Mapping[tuple[str, ...], Condition]):
        self.params, self.conditions = params, dict(conditions)

    def __call__(self, **labels: str) -> Condition:
        return self.conditions[tuple(labels[p] for p in self.params)]

    def __repr__(self) -> str:
        return f"expect({', '.join(self.params)})"


def _dumps(data: Any, indent: str = "") -> str:
    """JSON with each short value on one line, so a definition file reads like a listing."""
    flat = json.dumps(data, ensure_ascii=False)
    if len(flat) + len(indent) <= 100 or not isinstance(data, (dict, list)) or not data:
        return flat
    inner = indent + "  "
    if isinstance(data, dict):
        rows = [f"{inner}{json.dumps(k, ensure_ascii=False)}: {_dumps(v, inner)}" for k, v in data.items()]
        return "{\n" + ",\n".join(rows) + f"\n{indent}}}"
    return "[\n" + ",\n".join(f"{inner}{_dumps(v, inner)}" for v in data) + f"\n{indent}]"


def _condition_text(cond: object, check_id: str) -> Condition:
    if not isinstance(cond, Condition):
        raise DefinitionError(f"check {check_id!r}: saved `expect` {cond!r} is not a condition")
    return cond


def _entry_tuple(dim: Dimension, label: str) -> tuple[Any, ...]:
    """A label as a coord entry: (code, description, attrs), as add_dimension takes it."""
    return (label, dim.descriptions.get(label), dict(dim.label_attrs.get(label, {})))


def _entry_data(dim: Dimension, label: str) -> str | list[Any]:
    description, attrs = dim.descriptions.get(label), dim.label_attrs.get(label)
    if attrs:
        return [label, description, attrs]
    return [label, description] if description is not None else label


def _dimension_data(dim: Dimension) -> dict[str, Any]:
    data: dict[str, Any] = {"name": dim.name}
    if dim.hierarchy is not None:
        data["hierarchy"] = True
    if dim.time is not None:
        data["time"] = dim.time.to_data()
    data["coords"] = [_entry_data(dim, c) for c in dim.coords]
    return data


def _item_data(item: Item, model: Model) -> dict[str, Any]:
    data: dict[str, Any] = {"name": item.name, "dims": list(item.dims), "dtype": item.dtype.__name__}
    if item.description is not None:
        data["description"] = item.description
    if item.attrs:
        data["attrs"] = item.attrs
    if item.formula is not None:
        data["formula"] = _formula_data(item.formula, model)
    return data


def _formula_data(formula: Expr | CellFunction, model: Model) -> str | dict[str, Any]:
    """An expression as text; a function formula as {"python": source, "env": {...}}."""
    return formula.to_data(model) if isinstance(formula, CellFunction) else _text(formula)


def _load_function(data: Mapping[str, Any], model: Model, allow_code: bool, where: str) -> CellFunction:
    if not allow_code:
        raise DefinitionError(
            f"{where}: {data.get('python')!r} is Python code; loading it runs it. "
            "Pass allow_code=True to load it (run untrusted files in a sandbox)"
        )
    return CellFunction.from_data(data, model)


def _spec_data(spec: Any) -> Any:
    return "ANY" if spec is ANY else {"filter_by_attrs": spec.attrs}


def _spec_from_data(spec: Any) -> Any:
    if spec == "ANY":
        return ANY
    if isinstance(spec, dict) and set(spec) == {"filter_by_attrs"}:
        return AttrFilter(spec["filter_by_attrs"])
    return tuple(spec)


def _check_data(check: Check, model: Model) -> dict[str, Any]:
    data: dict[str, Any] = {"id": check.id}
    if check.description:
        data["description"] = check.description
    if check.severity != "error":
        data["severity"] = check.severity
    data["coords"] = {d: _spec_data(check.dynamic[d]) if d in check.dynamic else list(labels)
                      for d, labels in check.coords.items()}
    if isinstance(check.expect, CellFunction):
        data["expect"] = check.expect.to_data(model)
    elif check.params:
        data["params"] = list(check.params)
        data["expect_by_label"] = [{"labels": list(k), "expect": _text(c)} for k, c in check.conditions.items()]
    else:
        data["expect"] = _text(check.expect)  # type: ignore[arg-type]
    return data


def _identifier(name: object) -> str:
    text = re.sub(r"\W+", "_", str(name)).strip("_") or "item"
    if not text.isidentifier():  # starts with a digit
        text = f"item_{text}"
    return f"{text}_" if keyword.iskeyword(text) or text == "abs" else text


def _text(expr: Expr | Condition) -> str:
    """repr(expr), which text.parse reads back; item names must be plain identifiers."""
    for cell in expr.cells():
        name = cell.item.name
        if not name.isidentifier() or keyword.iskeyword(name) or name == "abs":
            raise DefinitionError(f"can't save {expr!r}: item name {name!r} must be a Python identifier to be written in an expression")
    return repr(expr)


def _is_value_check(check_id: str, fn: Callable[..., Any], run_over: Mapping[str, Any]) -> bool:
    """A function whose only parameter without a default is `x` is a value check."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    names = [p.name for p in sig.parameters.values()
             if p.default is p.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
    if names != ["x"]:
        return False
    if "x" in run_over:
        raise DefinitionError(
            f"check {check_id!r}: `expect` takes x, but x is also a dim in coords; a parameter named x "
            "makes a value check (x is the run's cells), so a dim named x can't be a function check "
            "parameter. Rename the dim"
        )
    return True


def _params(check_id: str, fn: Callable[..., Any], run_over: Mapping[str, Any]) -> tuple[str, ...]:
    """The dims a function `expect` takes: its parameter names, each a dim in coords."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        raise DefinitionError(f"check {check_id!r}: can't read the parameters of `expect`") from None
    names = []
    for prm in sig.parameters.values():
        if prm.kind in (prm.VAR_POSITIONAL, prm.VAR_KEYWORD):
            raise DefinitionError(f"check {check_id!r}: `expect` can't take *args or **kwargs; name each dim")
        if prm.default is not prm.empty:
            continue  # e.g. lambda line, h=h: ... binds a value, not a dim
        if prm.name not in run_over:
            raise DefinitionError(
                f"check {check_id!r}: `expect` takes {prm.name!r}, which is not a dim in coords "
                f"({', '.join(run_over) or 'none'}); name each parameter after a dim in coords"
            )
        names.append(prm.name)
    if not names:
        raise DefinitionError(f"check {check_id!r}: a function `expect` must take at least one dim from coords")
    return tuple(names)


def _kv(attrs: Mapping[str, Any]) -> str:
    return " ".join(f"{k}={v}" for k, v in attrs.items())


def _source_text(source: Mapping[str, Any]) -> str:
    text = f"{source['document']}, {source['section']}"
    return text + (f", p. {source['page']}" if source.get("page") else "")


def _fmt(coords: Mapping[str, str]) -> str:
    return "[" + ", ".join(f"{d}={v}" for d, v in coords.items()) + "]"

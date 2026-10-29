"""The model: dimensions, items scaffold, data quality checks, and the values.

Build in order: add_dimension -> add_item -> add_check -> set_value.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import keyword
import re
import warnings
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, NoReturn, Self

from . import dates
from .cellfn import Bound, Missing, run, run_check
from .convert import from_by_label, from_expr, from_text
from .deps import NeedsLabels, precedents
from .dimensions import Dimension, Hierarchy, _parse_entry
from .errors import (
    DefinitionError,
    DimMismatch,
    InvalidValue,
    MissingValue,
    TraceModelError,
    UnknownCoord,
    UnknownItem,
)
from .expr import Cell, OutOfRange, diff
from .formula import Formula, FormulaBase, Uncompiled, compare, env_from_data
from .ops import Op
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
    Trace,
    _Nil,
)

DTYPES = {"int": int, "float": float, "str": str, "string": str, **dates.DTYPE_NAMES}
_NOT_NUMBERS = (*dates.TEMPORAL, str)  # nil is not 0 for these: there is no date 0 or empty text
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
    formula: Formula | None = field(default=None, compare=False)  # calculates cells with no entered value
    description: str | None = None  # what the item is, as the source titles it: "Lease-related assets and liabilities"

    @property
    def unit_label(self) -> str:
        return " ".join(str(self.attrs[k]) for k in ("units", "scale") if k in self.attrs)


@dataclass(frozen=True)
class Check:
    id: str
    coords: dict[str, tuple[str, ...]]  # dim -> labels the check runs over
    expect: Formula  # lambda x: ..., True when the run passes
    description: str | None = None
    severity: str = "error"  # "error" or "warning"
    dynamic: dict[str, Any] = field(default_factory=dict)  # dim -> tm.ANY or filter_by_attrs, re-resolved on add_coords

    def runs(self) -> list[dict[str, str]]:
        """One coords mapping per run: the cross product of the check's coords."""
        dims = list(self.coords)
        return [dict(zip(dims, combo)) for combo in itertools.product(*self.coords.values())]


_NO_VALUE = object()  # a side of a check that read a missing cell (None is a value a side may have)
_EVERYTHING = "*"  # a transaction's pending check: every formula and check, at commit


class _Unset:
    """An argument not given (None is a value: no description)."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()


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
        self._deps: dict[tuple[str, tuple[str, ...]], list] = {}  # what a formula read, per cell
        # what a formula can read, from its source: per formula when it doesn't use the cell's
        # labels (a template, resolved per cell), else per cell
        self._templates: dict[tuple[int, str | None], tuple[Formula, Any]] = {}
        # per formula and item: {dims whose labels the walk used: {those labels: cells}}
        self._precedents: dict[tuple[int, str | None], tuple[Formula, dict[frozenset, dict[tuple, list[Cell]]]]] = {}
        self._root_cells: dict[str, Cell] = {}  # model["item"], reused by formulas
        self._dependents_index: dict[tuple, list[tuple]] | None = None  # cell -> what can read it, for dependents()
        self._acyclic: set[tuple[str, tuple[str, ...]]] = set()  # cells whose reads, all the way down, loop nowhere
        self._orders: dict[int, tuple] = {}  # a check's reads in source order, per formula
        # cell -> its dependents: the cached cells and check runs that read it, so a new value
        # recalculates only those (Excel's dependency tree)
        self._dependents: dict[tuple[str, tuple[str, ...]], set[tuple[str, tuple[str, ...]]]] = {}
        self._runs: dict[tuple[str, tuple[tuple[str, str], ...]], CheckResult] = {}  # check runs, until a read changes
        self._run_dependents: dict[tuple[str, tuple[str, ...]], set[tuple[str, tuple[tuple[str, str], ...]]]] = {}
        self._tx: Transaction | None = None
        self._journal: list[Op] | None = None  # the ops recorded, while recording (model.recording())
        self._quiet = 0  # > 0 while a verb runs another verb as its own detail (a check re-expanded)

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

    @contextlib.contextmanager
    def recording(self) -> Iterator[list[Op]]:
        """Record every change as an Op, into the list this yields:

            with model.recording() as ops:
                model.set_value(...)
            tm.ops.replay(other_model, ops)   # the same changes, checked the same way

        A transaction that rolls back takes its ops back with it.
        """
        if self._journal is not None:
            raise DefinitionError("this model is already recording")
        self._journal = ops = []
        try:
            yield ops
        finally:
            self._journal = None

    def _emit(self, kind: str, key: Any, old: Any, new: Any) -> None:
        if self._journal is not None and not self._quiet:
            self._journal.append(Op(kind, key, old, new))

    @contextlib.contextmanager
    def _detail(self) -> Iterator[None]:
        """Verbs run as a detail of another verb record nothing of their own."""
        self._quiet += 1
        try:
            yield
        finally:
            self._quiet -= 1

    def _changed(self, reads_changed: bool = True) -> None:
        """The definition changed: forget everything calculated. `reads_changed` False when no
        existing cell reads anything new (an item was added), so loop checks done still hold."""
        if reads_changed:
            self._acyclic.clear()
        self._orders.clear()
        self._cache.clear()
        self._deps.clear()
        self._templates.clear()
        self._precedents.clear()
        self._root_cells.clear()
        self._dependents_index = None
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
        """Check the formula cells' reads (items, open dims) and loops; inside a transaction at
        commit, so formulas can read items added later in it. `starts` None: every cell."""
        if self._tx is not None:
            self._tx._pending.append((name, starts))
            return
        if starts is None:
            starts = list(itertools.product(*(self.dimensions[d].coords for d in self.items[name].dims)))
        self._check_reads(name, starts)
        self._check_cycles(name, starts)

    def _check_reads(self, name: str, starts: Iterable[tuple[str, ...]]) -> None:
        """Each formula cell reads declared items, with every dim fixed by the cell or the formula."""
        item = self.items[name]
        seen: set[int] = set()
        for labels in starts:
            formula = self._formula_of(item, tuple(labels))
            if not isinstance(formula, Formula):
                continue
            def where(formula=formula, labels=labels) -> str:
                return (f"item {name!r}: formula" if formula is item.formula
                        else f"{name}{_fmt(dict(zip(item.dims, labels)))}: formula")

            cells = self._precedent_cells(formula, item, dict(zip(item.dims, labels)), where)
            if id(cells) in seen:  # the same reads as a cell already checked
                continue
            seen.add(id(cells))
            for c in cells:
                extra = [d for d in c.free_dims() if d not in item.dims]
                if extra:
                    raise DefinitionError(
                        f"{where()} {formula!r}: {c!r} is open in {', '.join(extra)}, which the item doesn't "
                        f"have; add the dim to the item or fix it in the formula, e.g. .sel({extra[0]}=...)"
                    )

    def _template(self, formula: Formula, item: Item | None) -> Any:
        """The formula's reads for any cell (Precedents), or None when it uses the cell's labels."""
        key = (id(formula), item.name if item is not None else None)
        hit = self._templates.get(key)
        if hit is None or hit[0] is not formula:
            try:
                found = precedents(formula, self, item, None)
            except NeedsLabels:
                found = None
            hit = self._templates[key] = (formula, found)
        return hit[1]

    def _precedent_cells(self, formula: FormulaBase, item: Item | None, at: Mapping[str, str],
                         where: str | Callable[[], str] = "formula") -> list[Cell]:
        """The cells a formula can read at `at`, unresolved (resolve each with `at`). A formula
        that uses the cell's labels is walked once per distinct value of the labels it uses."""
        if not isinstance(formula, Formula):
            return []
        try:
            template = self._template(formula, item)
            if template is not None:
                return template.cells
            key = (id(formula), item.name if item is not None else None)
            hit = self._precedents.get(key)
            if hit is None or hit[0] is not formula:
                hit = self._precedents[key] = (formula, {})
            by_labels = hit[1]
            for dims, found in by_labels.items():
                cells = found.get(tuple(at.get(d) for d in sorted(dims)))
                if cells is not None:
                    return cells
            walked = precedents(formula, self, item, at)
            dims = walked.labels
            by_labels.setdefault(dims, {})[tuple(at.get(d) for d in sorted(dims))] = walked.cells
            return walked.cells
        except DefinitionError as e:
            _reraise_with(e, where() if callable(where) else where)

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
        self._emit("dimension.add", name, None, _dimension_data(dim))
        return dim

    def add_coords(self, name: str, entries: Iterable[Any] | PeriodRange) -> Dimension:
        """Append labels to a dimension, e.g. forecast periods. A time dimension takes the
        periods that continue it: add_coords("month", tm.period_range("2027-01", periods=12)).

        Items get the new cells (not entered). Checks over the dimension with tm.ANY or
        filter_by_attrs are re-expanded to cover the new labels. Re-adding labels that are
        already there with the same entries is a no-op.
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
            with self._detail():
                for check in checks.values():
                    if name in check.dynamic:
                        self._reexpand(check, name)
        except BaseException:
            self.dimensions[name], self.checks = old, checks
            self._changed()
            raise
        self._emit("coords.add", name, _dimension_data(old), _dimension_data(dim))
        return dim

    def _reexpand(self, check: Check, dim: str) -> None:
        """Re-add a check whose tm.ANY coords now cover more labels."""
        coords = {d: check.dynamic.get(d, labels) for d, labels in check.coords.items()}
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
        old = self.dimensions.pop(name)
        self._changed()
        self._emit("dimension.remove", name, _dimension_data(old), None)

    def remove_coords(self, name: str, labels: Iterable[str]) -> Dimension:
        """Remove labels from a dimension, with the values entered at them. A time dimension
        loses periods from its end; a hierarchy path goes with its children or not at all.

        Refused, with nothing removed, while a formula or a check still reads a removed label
        by name; a check over tm.ANY or filter_by_attrs shrinks with the dimension.
        """
        if name not in self.dimensions:
            raise UnknownCoord(f"no dimension {name!r}; declared: {', '.join(self.dimensions)}")
        old = self.dimensions[name]
        gone = list(dict.fromkeys([labels] if isinstance(labels, str) else labels))
        for label in gone:
            old.require(label)
        if not gone:
            return old
        keep = [c for c in old.coords if c not in gone]
        if not keep:
            raise DefinitionError(f"can't remove every label of {name!r}; remove the dimension instead")
        if old.time is not None and list(old.coords[:len(keep)]) != keep:
            raise DefinitionError(f"{name!r} is a time dimension: remove periods from its end, "
                                  f"e.g. {list(old.coords[len(keep):])!r}")
        if old.hierarchy is not None:
            orphans = [c for c in keep if any(c.startswith(g + "/") for g in gone)]
            if orphans:
                raise DefinitionError(f"can't remove {', '.join(gone)} from {name!r}: "
                                      f"{', '.join(orphans)} would lose a parent; remove them too")
        listed = [c.id for c in self.checks.values()
                  if name in c.coords and name not in c.dynamic and set(c.coords[name]) & set(gone)]
        if listed:
            raise DefinitionError(f"can't remove {', '.join(gone)} from {name!r}: checks {', '.join(listed)} "
                                  "list them in coords; change or remove those checks first")
        if old.time is not None:
            dim = Dimension(name, PeriodRange(old.time.periods[0], len(keep)))
        else:
            entries = [_entry_tuple(old, c) for c in keep]
            dim = Dimension(name, Hierarchy(entries) if old.hierarchy is not None else entries)
        with self._atomic():
            for key in [k for k, e in self._entries.items()
                        if name in self.items[k[0]].dims and e.coords[name] in gone]:
                self.clear_value(key[0], self._entries[key].coords)
            self.dimensions[name] = dim
            self._changed()
            with self._detail():
                for check in list(self.checks.values()):
                    if name in check.dynamic:
                        self._reexpand(check, name)
            self._recheck_all(f"can't remove {', '.join(gone)} from {name!r}")
            self._emit("coords.remove", name, _dimension_data(old), _dimension_data(dim))
        return dim

    # == 2. items scaffold ==================================================
    def add_item(
        self,
        name: str,
        dims: Iterable[str],
        dtype: type | str = float,
        attrs: Mapping[str, Any] | None = None,
        formula: Formula | Callable[..., Any] | str | None = None,
        description: str | None = None,
    ) -> Item:
        """Declare a line item: its dims, data type (int, float, str, date or datetime), attrs
        (units, scale, ...) and a description, the item's title as people read it
        ("Lease-related assets and liabilities"), as a dimension's labels have.

        With `formula`, the item's cells are calculated; a value entered with set_value
        overrides the formula for its cell. `formula` is a lambda of `x`, the cell being
        calculated, or its source text: lambda x: max(0, 25_000 - x["cash"]); x["item"] reads
        another item at the same coords, x.lag("period") this item's previous period.
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
        if not any(dtype is t for t in (int, float, str, date, datetime)):
            raise DefinitionError(f"item {name!r}: dtype must be int, float, str, date or datetime, got {dtype!r}")
        if attrs is not None and not isinstance(attrs, Mapping):
            raise DefinitionError(f"item {name!r}: attrs must be a dict")
        if description is not None and not isinstance(description, str):
            raise DefinitionError(f"item {name!r}: description must be a string")
        item = Item(name, dims, dtype, dict(attrs or {}), description=description)
        old = self.items.get(name)
        if old is not None and old != item:
            raise DefinitionError(f"item {name!r} already exists with a different definition; change its attrs or "
                                  f"description with update_item, or remove_item and add it again")
        self.items[name] = old or item  # declared first, so a formula can read its own cells
        try:
            if formula is not None:
                formula = self._as_formula(formula, f"item {name!r}: formula")
            if old is not None:
                if old.formula == formula:
                    return old
                raise DefinitionError(f"item {name!r} already exists with a different formula; "
                                      f"change it with set_formula({name!r}, ...)")
            self.items[name] = item = Item(name, dims, dtype, dict(attrs or {}), formula, description)
            # a new item changes no other cell's reads, unless its name is also a label (x["/a"])
            self._changed(reads_changed=any(name in d for d in self.dimensions.values()))
            if formula is not None:
                self._check_cycles_now_or_at_commit(name, None)
        except BaseException:
            if old is None:
                del self.items[name]
            self._changed()
            raise
        self._emit("item.add", name, None, _item_data(item, self))
        return item

    def set_formula(self, name: str, formula: Formula | Callable[..., Any] | str | None) -> Item:
        """Change an item's formula, or remove it with None (its cells are then entered only).
        Values entered over the formula stay. Refused, with nothing changed, when the new
        formula reads an unknown item or label, leaves a dim open, or makes a cell depend on
        itself."""
        old = self.item(name)
        if formula is not None:
            formula = self._as_formula(formula, f"item {name!r}: formula")
        if formula == old.formula:
            return old
        item = Item(old.name, old.dims, old.dtype, dict(old.attrs), formula, old.description)  # type: ignore[arg-type]
        self.items[name] = item
        self._changed()
        if formula is not None:
            try:
                self._check_cycles_now_or_at_commit(name, None)
            except BaseException:
                self.items[name] = old
                self._changed()
                raise
        self._emit("item.update", name, _item_data(old, self), _item_data(item, self))
        return item

    def update_item(self, name: str, *, attrs: Mapping[str, Any] | None | _Unset = UNSET,
                    description: str | None | _Unset = UNSET) -> Item:
        """Change an item's attrs or description. Its dims and dtype don't change: remove_item
        and add it again."""
        old = self.item(name)
        if not isinstance(attrs, _Unset) and attrs is not None and not isinstance(attrs, Mapping):
            raise DefinitionError(f"item {name!r}: attrs must be a dict")
        if not isinstance(description, _Unset) and description is not None and not isinstance(description, str):
            raise DefinitionError(f"item {name!r}: description must be a string")
        item = Item(old.name, old.dims, old.dtype,
                    dict(old.attrs) if isinstance(attrs, _Unset) else dict(attrs or {}), old.formula,
                    old.description if isinstance(description, _Unset) else description)
        if item == old:
            return old
        self.items[name] = item
        self._root_cells.pop(name, None)  # a cell shows the item's units
        self._emit("item.update", name, _item_data(old, self), _item_data(item, self))
        return item

    def remove_item(self, name: str) -> None:
        """Remove an item, with its values. Refused, with nothing removed, while a formula or
        a check reads it."""
        self.item(name)
        with self._atomic():
            for key in [k for k in self._entries if k[0] == name]:
                self.clear_value(name, self._entries[key].coords)
            old = self.items.pop(name)
            self._changed()
            self._recheck_all(f"can't remove item {name!r}")
            self._emit("item.remove", name, _item_data(old, self), None)

    def _atomic(self) -> Any:
        """A change made of several: in the open transaction, else in one of its own."""
        return contextlib.nullcontext() if self._tx is not None else self.transaction()

    def _recheck_all(self, why: str) -> None:
        """Check every formula and check again, after something they may read was removed;
        inside a transaction, at commit."""
        if self._tx is not None:
            self._tx._pending.append((_EVERYTHING, why))
            return
        try:
            for name, item in self.items.items():
                if item.formula is not None:
                    starts: list[tuple[str, ...]] = list(
                        itertools.product(*(self.dimensions[d].coords for d in item.dims)))
                else:
                    starts = [k[1] for k, e in self._entries.items() if k[0] == name and _is_formula(e.value)]
                if starts:
                    self._check_reads(name, starts)
                    self._check_cycles(name, starts)
            for check in list(self.checks.values()):
                self._install_check(check)
        except TraceModelError as e:
            raise DefinitionError(f"{why}: {e}") from None

    def _as_formula(self, value: Any, what: str) -> FormulaBase:
        """A lambda, its source text or a Formula as a Formula; an expression built from
        model["a"] becomes the lambda it stands for."""
        if isinstance(value, FormulaBase):
            return value
        if isinstance(value, Cell):  # formula=model["revenue"]: lambda x: x['revenue']
            if value.model is not self:
                raise DefinitionError(f"{what} reads items of another model")
            return Formula(from_expr(value), None, what)
        return Formula.of(value, what)

    def _formula_of(self, item: Item, labels: tuple[str, ...]) -> FormulaBase | None:
        """The formula that calculates a cell: its own (set_value), else the item's if not entered."""
        e = self._entries.get((item.name, labels))
        if e is not None:
            return e.value if _is_formula(e.value) else None
        return self.items[item.name].formula  # by name: a self-reference holds the item as first declared

    def _check_cycles(self, name: str, starts: Iterable[tuple[str, ...]]) -> None:
        """Raise if a formula cell depends on itself, e.g. revenue = revenue.lag + revenue.lead."""
        done = self._acyclic  # kept between checks until a cell's reads change
        for labels in starts:
            root = (name, tuple(labels))
            if root in done:
                continue
            path, on_path = [root], {root}
            stack = [iter(self._cell_reads(root))]  # depth first, without recursion
            while stack:
                key = next(stack[-1], None)
                if key is None:
                    stack.pop()
                    finished = path.pop()
                    on_path.discard(finished)
                    done.add(finished)
                    continue
                if key in done:
                    continue
                reads = self._cell_reads(key) if key not in on_path else None
                if reads == []:  # an entered cell, or one whose formula reads nothing
                    done.add(key)
                    continue
                if key in on_path:
                    loop = path[path.index(key):] + [key]
                    raise DefinitionError("circular formula: " + " -> ".join(
                        f"{n}{_fmt(dict(zip(self.items[n].dims, l)))}" for n, l in loop))
                path.append(key)
                on_path.add(key)
                stack.append(iter(reads))

    def _cell_reads(self, key: tuple[str, tuple[str, ...]]) -> list[tuple[str, tuple[str, ...]]]:
        """The formula cells and entered cells a cell's formula can read, as keys."""
        item = self.items[key[0]]
        formula = self._formula_of(item, key[1])
        if formula is None:
            return []
        at = dict(zip(item.dims, key[1]))
        out = []
        for c in self._precedent_cells(formula, item, at, lambda: f"{key[0]}{_fmt(at)}: formula"):
            try:
                full = c.resolve(at)
            except OutOfRange:
                continue
            out.append((c.item.name, tuple(full[d] for d in c.item.dims)))
        return out

    def item(self, name: str) -> Item:
        if name not in self.items:
            raise UnknownItem(f"{name!r} is not a declared item; declared: {', '.join(self.items)}")
        return self.items[name]

    def __getitem__(self, name: str) -> Cell:
        """A line item's cells, to display as a table: index it (["/"]) or .sel(...) to fix coords."""
        cell = self._root_cells.get(name)
        if cell is None:
            cell = self._root_cells[name] = Cell(self, self.item(name))
        return cell

    # == 3. data quality ====================================================
    def add_check(
        self,
        id: str,
        expect: Formula | Callable[..., Any] | str,
        coords: Mapping[str, Any] | None = None,
        description: str | None = None,
        severity: str = "error",
    ) -> Check:
        """Add a check, or replace the check with the same id.

        `expect` is a lambda of `x`, or its source text, run once per combination of the
        labels in `coords` and returning True or False: lambda x: 0 <= x["margin"] <= 1. `x` is
        the run: x["item"] reads an item at the run's labels, x.coords gives them, and
        x.children(dim) a hierarchy label's children, for rollups:
        lambda x: abs(x["sales"] - sum(x["sales"].sel(segment=c) for c in x.children("segment"))) <= 0.5.
        `severity` is "error" (the default) or "warning": a failing warning doesn't make the
        report fail.
        """
        if severity not in SEVERITIES:
            raise DefinitionError(f"check {id!r}: severity must be 'error' or 'warning', got {severity!r}")
        run_over: dict[str, tuple[str, ...]] = {}
        dynamic = {d: spec for d, spec in (coords or {}).items() if spec is ANY or isinstance(spec, AttrFilter)}
        for dim, spec in (coords or {}).items():
            if dim not in self.dimensions:
                raise UnknownCoord(f"check {id!r}: no dimension {dim!r}")
            run_over[dim] = _labels(self.dimensions[dim], spec)
        if isinstance(expect, Cell):
            raise DefinitionError(f"check {id!r}: `expect` must be a lambda of x returning True or False, "
                                  f"e.g. lambda x: x[{expect.item.name!r}] >= 0; got {expect!r}")
        if expect is None or not (callable(expect) or isinstance(expect, (str, FormulaBase))):
            raise DefinitionError(
                f"check {id!r}: `expect` must be a lambda of x, e.g. lambda x: x['revenue'] >= 0, got {expect!r}")
        formula = self._as_formula(expect, f"check {id!r}: expect")
        if isinstance(formula, Formula) and formula.param in run_over and formula.param != "x":
            raise DefinitionError(
                f"check {id!r}: `expect` takes {formula.param!r}, a dim in coords; a check is a lambda of the "
                f"run, x: read the label as x.coords[{formula.param!r}] and items as x['item'], e.g. "
                f"lambda x: x['revenue'].sel({formula.param}=...) or a rollup with x.children({formula.param!r})"
            )
        old = self.checks.get(id)
        before = _check_data(old, self) if old is not None else None
        check = self._install_check(Check(id, run_over, formula, description, severity, dynamic))
        self._emit("check.set", id, before, _check_data(check, self))
        return check

    def _install_check(self, check: Check) -> Check:
        """Validate a check against the model's dims and labels, then add it."""
        id, run_over = check.id, check.coords
        where = f"check {id!r}: expect"
        if isinstance(check.expect, Formula):
            template = self._template_or_raise(check.expect, where)
            used: set[str] = set()
            for at in check.runs():
                cells = self._precedent_cells(check.expect, None, at, where)
                for cell in cells:
                    open_dims = [d for d in cell.free_dims() if d not in run_over]
                    if open_dims:
                        raise DefinitionError(
                            f"check {id!r}: {cell!r} leaves {', '.join(open_dims)} unresolved; "
                            f"fix it in the cell or add it to coords, e.g. coords={{{open_dims[0]!r}: tm.ANY}}"
                        )
                    used |= set(cell.free_dims())
                    try:  # a lag or lead must land on a declared label in every run
                        cell.resolve(at)
                    except OutOfRange as e:
                        keep = f"[{e.steps}:]" if e.steps > 0 else f"[:{e.steps}]"
                        run = ", ".join(f"{d}={v}" for d, v in at.items())
                        raise DefinitionError(
                            f"check {id!r}: {e} (run {run}); leave that label out of coords, e.g. "
                            f"coords={{{e.dim!r}: model.dimensions[{e.dim!r}].coords{keep}}}"
                        ) from None
            unused = [d for d in run_over if d not in used]
            if unused and template is not None:  # a check that reads x.coords may use any of them
                raise DefinitionError(
                    f"check {id!r}: coords give {', '.join(unused)}, but every cell already fixes it; "
                    "remove it from coords"
                )
        self.checks[id] = check
        self._forget_runs(id)
        self._dependents_index = None
        return check

    def remove_check(self, id: str) -> None:
        """Remove a check."""
        if id not in self.checks:
            raise DefinitionError(f"no check {id!r}; checks: {', '.join(self.checks) or 'none'}")
        old = self.checks.pop(id)
        self._forget_runs(id)
        self._dependents_index = None
        self._emit("check.remove", id, _check_data(old, self), None)

    def _template_or_raise(self, formula: Formula, where: str) -> Any:
        try:
            return self._template(formula, None)
        except DefinitionError as e:
            _reraise_with(e, where)

    def _forget_runs(self, id: str) -> None:
        for run_key in [r for r in self._runs if r[0] == id]:
            del self._runs[run_key]

    # == tracing: what a cell reads, and what reads it =========================
    def precedents(self, line_item: str, deep: bool = False, **coords: str) -> list[Trace]:
        """The cells this cell's formula can read (Excel's Trace Precedents), from the formula's
        source: both arms of a branch count. An entered cell has none. With deep=True each one
        carries its own precedents, down to the entered cells."""
        key, _ = self._key(line_item, coords)
        return self._trace(key, deep, {key}, self._precedent_keys)

    def dependents(self, line_item: str, deep: bool = False, **coords: str) -> list[Trace]:
        """The formula cells and check runs that can read this cell (Excel's Trace Dependents).
        With deep=True each one carries its own dependents."""
        key, _ = self._key(line_item, coords)
        return self._trace(key, deep, {key}, self._dependent_keys)

    def _trace(self, key: tuple, deep: bool, seen: set, step: Callable[[tuple], list[tuple]]) -> list[Trace]:
        out = []
        for k in step(key):
            if k[0] == "check":
                t = Trace(k[1], dict(k[2]), "check")
            else:
                t = Trace(k[0], dict(zip(self.items[k[0]].dims, k[1])))
            if k in seen:
                t.repeated = deep and k[0] != "check"
            elif deep:
                seen.add(k)
                t.children = self._trace(k, deep, seen, step)
            out.append(t)
        return out

    def _precedent_keys(self, key: tuple) -> list[tuple]:
        if key[0] == "check":
            return []
        return list(dict.fromkeys(self._cell_reads(key)))

    def _dependent_keys(self, key: tuple) -> list[tuple]:
        if self._dependents_index is None:
            index: dict[tuple, list[tuple]] = {}
            for name, item in self.items.items():
                cells = itertools.product(*(self.dimensions[d].coords for d in item.dims))
                for labels in cells:
                    for read in dict.fromkeys(self._cell_reads((name, labels))):
                        index.setdefault(read, []).append((name, labels))
            for check in self.checks.values():
                for at in check.runs():
                    run = ("check", check.id, tuple(at.items()))
                    for c in self._precedent_cells(check.expect, None, at):
                        k = _read_key(c, at)
                        if k is not None and run not in index.get(k, ()):
                            index.setdefault(k, []).append(run)
            self._dependents_index = index
        return self._dependents_index.get(key, [])

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
        """Run the check with this run's x; a missing cell, an error or a result that isn't
        True or False fails it."""
        formula = check.expect
        raw: list = []
        result: Any = None
        error = lhs = rhs = None
        if not formula.runnable:
            error = "not run: the model was loaded with formulas='off'"
        else:
            try:
                if formula.sides is not None:  # one comparison: its two sides, then the comparison
                    lhs, rhs = (self._side(side, at, raw) for side in formula.sides)
                    result = None if lhs is _NO_VALUE or rhs is _NO_VALUE else compare(formula.op, lhs, rhs)
                    lhs, rhs = (None if v is _NO_VALUE else v for v in (lhs, rhs))
                else:
                    result = run_check(formula, self, at, raw)
            except Missing:  # missing never counts as 0
                pass
            except Exception as e:  # noqa: BLE001 - an error in the check, or a cell it reads can't be calculated
                error = f"{type(e).__name__}: {e}"
                lhs = rhs = None
        if isinstance(check.expect, Formula):
            raw = self._in_source_order(check.expect, at, raw, missing=result is None and error is None)
        reads: list[CellRead] = []
        try:
            for cell, cell_at in raw:
                self._reads([cell], cell_at, reads)
        except Exception as e:  # noqa: BLE001 - a cell it read can't be calculated: fail the run
            error = error or f"{type(e).__name__}: {e}"
        if error is None and result is not None and not isinstance(result, bool):
            error = f"returned {result!r}; a check must return True or False"
        passed = result is True and error is None and not any(r.value is None for r in reads)
        return CheckResult(check, at, lhs, rhs, passed, reads, formula, error)

    def _side(self, side: Formula, at: Mapping[str, str], raw: list) -> Any:
        """One side of a check's comparison, as a value; _NO_VALUE when it reads a missing cell."""
        try:
            value = run_check(side, self, at, raw)
            return value._get() if isinstance(value, Bound) else value
        except Missing:
            return _NO_VALUE

    def _in_source_order(self, formula: Formula, at: dict[str, str], raw: list, missing: bool) -> list:
        """A run's reads in the order the formula names them. A run that stopped at a missing
        cell lists every cell the formula reads, so the report shows what else it needs."""
        hit = self._orders.get(id(formula))
        if hit is not None and hit[0] is formula:
            cells, keys, order = hit[1], hit[2], hit[3]  # type: ignore[misc]
        else:
            try:
                cells = self._precedent_cells(formula, None, at)
            except DefinitionError:
                return raw
            keys = [_shape(c) for c in cells]  # every read here is at the run's labels: the cell's shape names it
            order = {k: i for i, k in reversed(list(enumerate(keys)))}
            if self._template(formula, None) is not None:  # the same for every run
                self._orders[id(formula)] = (formula, cells, keys, order)  # type: ignore[assignment]
        if missing:
            seen = {_shape(c) for c, _ in raw}
            raw = raw + [(c, at) for c, k in zip(cells, keys) if k not in seen]
        if len(raw) < 2:
            return raw
        shapes = [_shape(c) for c, _ in raw]
        positions = [order.get(k, len(order)) for k in shapes]
        if positions == sorted(positions):
            return raw
        return [r for _, r in sorted(zip(positions, raw), key=lambda p: p[0])]

    def _reads(self, cells: list[Cell], at: Mapping[str, str], reads: list[CellRead]) -> None:
        """The cells read, each followed by the inputs of a formula that calculated it."""
        for c in cells:
            try:
                full = c.resolve(at)
            except OutOfRange:  # e.g. the first period's lag: nothing there to read
                continue
            read = CellRead(c.item.name, full, self._value(c, at))
            if read in reads:  # a check may read the same cell twice
                continue
            reads.append(read)
            labels = tuple(full[d] for d in c.item.dims)
            if self._formula_of(c.item, labels) is not None:
                for dep, dep_at in self._deps.get((c.item.name, labels), []):
                    self._reads([dep], dep_at, reads)

    # == 4. values ==========================================================
    def set_value(
        self, line_item: str, coords: Mapping[str, str], value: float, source: Mapping[str, Any]
    ) -> None:
        """Enter a value with its source. The last write wins; the value it replaces, and that
        value's source, are kept in the entry's history. On a formula item it overrides the
        formula for this cell.

        `value` may be a lambda of x, or its source text ("lambda x: ..."): a formula for this
        one cell, e.g. a contract's escalator, sourced like any value.
        """
        key, full = self._key(line_item, coords)
        item = self.items[line_item]
        if (callable(value) or isinstance(value, (FormulaBase, Cell))
                or isinstance(value, str) and item.dtype is not str and value.lstrip().startswith("lambda")):
            value = self._as_formula(value, f"{line_item}{_fmt(full)}: formula")
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
        if item.formula is not None or _is_formula(value) or (previous is not None and _is_formula(previous.value)):
            self._dependents_index = None  # which cells have formulas changed
        if _is_formula(value):
            self._acyclic.clear()  # this cell reads something new
        if _is_formula(value):
            try:
                self._check_cycles_now_or_at_commit(line_item, [key[1]])
            except BaseException:
                if previous is None:
                    del self._entries[key]
                else:
                    self._entries[key] = previous
                self._value_changed(key)
                raise
        self._emit("value.set", {"item": line_item, "coords": full},
                   None if previous is None else _value_data(previous), _value_data(self._entries[key]))

    def clear_value(self, line_item: str, coords: Mapping[str, str]) -> None:
        """Remove a cell's entered value, so it is not entered again (unlike tm.NIL, which says
        the source shows nothing). On a formula item the item's formula calculates it again."""
        key, full = self._key(line_item, coords)
        previous = self._entries.get(key)
        if previous is None:
            raise MissingValue(f"{line_item}{_fmt(full)} has no value to clear")
        del self._entries[key]
        self._value_changed(key)
        if _is_formula(previous.value) or self.items[line_item].formula is not None:
            self._dependents_index = None
            self._acyclic.clear()  # this cell now reads what the item's formula reads
        if self.items[line_item].formula is not None:
            try:
                self._check_cycles_now_or_at_commit(line_item, [key[1]])
            except BaseException:
                self._entries[key] = previous
                self._value_changed(key)
                raise
        self._emit("value.clear", {"item": line_item, "coords": full}, _value_data(previous), None)

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
        return 0 if value is NIL and item.dtype not in _NOT_NUMBERS else value

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

    def evaluate(self, formula: Formula | Callable[..., Any] | str | Cell) -> Table | float:
        """Evaluate a lambda of x for every combination of the dims its cells leave open, e.g.
        model.evaluate(lambda x: x["revenue"] - x["cost"]); a comparison shows each side."""
        title = repr(formula) if isinstance(formula, Cell) else None
        formula = self._as_formula(formula, "evaluate")
        if not isinstance(formula, Formula):
            raise DefinitionError("can't evaluate a formula loaded with formulas='off'")
        title = title or formula.body
        template = self._template_or_raise(formula, "evaluate")
        if template is None:
            raise DefinitionError(f"evaluate {formula.body!r}: it uses x's labels (x.coords, x.period, ...), "
                                  "so it has no dims of its own to run over; add it as an item or a check")
        free: list[str] = []
        for c in template.cells:
            free += [d for d in c.free_dims() if d not in free]
        combos = [dict(zip(free, c)) for c in itertools.product(*(self.dimensions[d].coords for d in free))] or [{}]

        def result(f: Formula, at: dict[str, str]) -> bool | str:
            ok = self._try(f, at)
            return "missing" if ok is None else ok

        if formula.sides is not None:
            rows = []
            for at in combos:
                lhs, rhs = (self._try(side, at) for side in formula.sides)
                rows.append([*at.values(), lhs, rhs, diff(lhs, rhs), result(formula, at)])
            return Table([*free, "left", "right", "diff", "result"], rows, title=title)
        if formula.parts is not None:  # and / or: each part, then the whole
            rows = [[*at.values(), *(result(p, at) for p in formula.parts), result(formula, at)] for at in combos]
            return Table([*free, *(p.body for p in formula.parts), "result"], rows, title=title)

        if not free:
            value = self._try(formula, {})
            if value is None:
                raise MissingValue(f"{title} reads a cell with no value")
            return value
        if "period" in free:
            others = [d for d in free if d != "period"]
            periods = self.dimensions["period"].coords
            row_keys = list(itertools.product(*(self.dimensions[d].coords for d in others))) or [()]
            rows = [
                [" / ".join(key) or title,
                 *(self._try(formula, dict(zip(others, key)) | {"period": p}, show_nil=True) for p in periods)]
                for key in row_keys
            ]
            return Table([" / ".join(others) or "", *periods], rows, title=title)
        rows = [[*at.values(), self._try(formula, at, show_nil=True)] for at in combos]
        return Table([*free, "value"], rows, title=title)

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
    def from_dict(cls, data: Mapping[str, Any], allow_code: bool | None = None, formulas: str = "on") -> Model:
        """A model from `to_dict()` data. Formulas are checked against the formula allow-list
        and compiled with RestrictedPython; nothing else in the file is run. Older files'
        expression text and per-label checks are converted to lambdas.

        formulas="off" loads the dimensions, items and checks with their formulas kept as
        text, not compiled or run: formula cells have no value and checks don't run. For files
        from untrusted sources, prefer running tracemodel in a sandbox (gVisor) as well.
        `allow_code` is ignored: formulas never need it now.
        """
        _no_allow_code(allow_code)
        if formulas not in ("on", "off"):
            raise DefinitionError(f"formulas must be 'on' or 'off', got {formulas!r}")
        if data.get("format") not in READS:
            raise DefinitionError(f"not a tracemodel definition (format {data.get('format')!r}, expected {FORMAT!r})")
        model = cls()
        for d in data["dimensions"]:
            model.add_dimension(d["name"], _dimension_from_data(d))
        with model.transaction():  # a formula may read an item saved after it
            for i in data["items"]:
                formula = None
                if i.get("formula") is not None:
                    formula = _formula_from_data(i, "formula", f"item {i['name']!r}: formula", formulas)
                model.add_item(i["name"], i["dims"], i["dtype"], i.get("attrs"), formula=formula,
                               description=i.get("description"))
        for c in data["checks"]:
            specs = {dim: _spec_from_data(spec) for dim, spec in c.get("coords", {}).items()}
            for dim in specs:
                if dim not in model.dimensions:
                    raise UnknownCoord(f"check {c['id']!r}: no dimension {dim!r}")
            run_over = {dim: _labels(model.dimensions[dim], spec) for dim, spec in specs.items()}
            dynamic = {d: spec for d, spec in specs.items() if spec is ANY or isinstance(spec, AttrFilter)}
            where = f"check {c['id']!r}: expect"
            if "expect_by_label" in c:
                if formulas == "off":
                    expect: Any = Uncompiled({"expect_by_label": c["expect_by_label"], "params": c.get("params", [])})
                else:
                    expect = Formula(from_by_label(c.get("params", ()), c["expect_by_label"], where), None, where)
            else:
                expect = _formula_from_data(c, "expect", where, formulas)
            model._install_check(Check(c["id"], run_over, expect, c.get("description"),
                                       c.get("severity", "error"), dynamic))
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
            row = {"line_item": e.item, "coords": e.coords} | _value_data(e)
            if e.overrides_formula:
                row["overrides_formula"] = True
            lines.append(json.dumps(row) + "\n")
        Path(path).write_text("".join(lines))  # written only once every row could be saved

    def load_jsonl(self, path: str | Path, allow_code: bool | None = None, formulas: str = "on") -> None:
        """Add the values in a JSON lines file, each validated like set_value.

        The file loads in one transaction (unless one is already open): all of it, or nothing.
        Cell formulas are checked and compiled as in tm.load; formulas="off" keeps them as text.
        """
        _no_allow_code(allow_code)
        if self._tx is None:
            with self.transaction():
                self.load_jsonl(path, None, formulas)
            return
        with open(path) as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    value = _value_from_data(row, formulas, f"{row['line_item']}{_fmt(row['coords'])}: formula")
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
        # A formula's exact reads are known only as it runs: when a calculation gets deep,
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

    def _calculate(self, key: tuple[str, tuple[str, ...]], formula: FormulaBase,
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
            reads: list = []
            try:
                value = run(formula, self, self.items[key[0]], full, reads) if isinstance(formula, Formula) else None
            except (Missing, ZeroDivisionError):
                value = None
            self._deps[key] = reads
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
            template = self._template(formula, item) if isinstance(formula, Formula) else None
            if (template is None or template.branched or ready) and key != root:
                try:
                    self._calculate(key, formula, full)
                except _Defer:  # deep: _value's loop calculates what it needs first, then this cell
                    return
                continue
            if template is None or template.branched or ready:  # its reads are known only by running it
                continue
            if key in seen:  # a loop: _calculate reports it when it gets there
                continue
            seen.add(key)
            stack.append((key, True))
            for c in template.cells:
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
            raise Missing
        if value is NIL:
            if cell.item.dtype in _NOT_NUMBERS:  # nil counts as 0, but there is no date 0 or empty text
                what = "text" if cell.item.dtype is str else "date"
                raise DefinitionError(
                    f"{cell!r}{_fmt(cell.resolve(at))} is nil: it has no {what} to compute with; "
                    f"test {cell!r}.is_nil() first"
                )
            return 0
        return value

    def _try(self, formula: Formula, at: Mapping[str, str], show_nil: bool = False) -> Any:
        """A lambda of x run at `at` (no cell of its own); None when a value is missing or a
        division by zero."""
        try:
            value = run_check(formula, self, at, [])
            if isinstance(value, Bound):
                if show_nil and self._value(value._cell, value._at) is NIL:
                    return NIL  # an item's own cell shows "—"; in arithmetic it counts as 0
                value = value._get()
            return value
        except (Missing, ZeroDivisionError):
            return None

    @staticmethod
    def _validate(item: Item, value: object, source: object) -> None:
        if value is NIL or _is_formula(value) or item.dtype in dates.TEMPORAL:  # dates: set_value coerced it
            pass
        elif item.dtype is str:
            if not isinstance(value, str):
                raise InvalidValue(f"{item.name} is str: value must be text, got {value!r}")
            if not value.strip():
                raise InvalidValue(f"{item.name}: empty text; when the source shows nothing, enter tm.NIL")
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


FORMAT = "tracemodel/2"  # formulas saved as lambdas; "tracemodel/1" (0.1, expression text) still loads
READS = ("tracemodel/1", FORMAT)


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
            or (item.dtype is datetime and kind == "datetime") or (item.dtype is str and kind == "str"):
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


def _reraise_with(e: DefinitionError, prefix: str) -> NoReturn:
    """Raise `e` again with `prefix` (where it happened) in front, unless it already names it."""
    if type(e) is not DefinitionError or str(e).startswith(prefix):
        raise e
    raise DefinitionError(f"{prefix}: {e}") from None


def _read_key(cell: Cell, at: Mapping[str, str]) -> tuple | None:
    try:
        full = cell.resolve(at)
    except OutOfRange:
        return None
    return cell.item.name, tuple(full[d] for d in cell.item.dims)


def _shape(cell: Cell) -> tuple:
    """A cell relative to the labels it is read at: its item, fixed coords and shifts."""
    return cell.item.name, tuple(cell.coords.items()), tuple(cell.shifts.items())


def _is_formula(value: object) -> bool:
    return isinstance(value, FormulaBase)


class Transaction:
    """Changes to a model that commit together; see Model.transaction()."""

    def __init__(self, model: Model):
        self._model = model
        self._saved = (dict(model.dimensions), dict(model.items), dict(model.checks), dict(model._entries))
        self._mark = None if model._journal is None else len(model._journal)  # ops recorded before it
        self._pending: list[tuple[str, list[tuple[str, ...]] | None]] = []
        self.open = True

    def commit(self) -> None:
        """Run the deferred checks; on a failure, roll back and raise."""
        self._require_open()
        model, pending, self._pending = self._model, self._pending, []
        model._tx = None  # checks below run as they would outside a transaction
        try:
            for name, starts in pending:
                if name == _EVERYTHING:
                    model._recheck_all(starts)  # type: ignore[arg-type]  # starts: why
                elif name in model.items:
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
        if self._mark is not None and m._journal is not None:
            del m._journal[self._mark:]  # its ops never happened
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


def _entry_tuple(dim: Dimension, label: str) -> tuple[Any, ...]:
    """A label as a coord entry: (code, description, attrs), as add_dimension takes it."""
    return (label, dim.descriptions.get(label), dict(dim.label_attrs.get(label, {})))


def _entry_data(dim: Dimension, label: str) -> str | list[Any]:
    description, attrs = dim.descriptions.get(label), dim.label_attrs.get(label)
    if attrs:
        return [label, description, attrs]
    return [label, description] if description is not None else label


def _dimension_from_data(d: Mapping[str, Any]) -> Any:
    """A saved dimension's coords: a time spine, a Hierarchy or a list of entries."""
    entries = [e if isinstance(e, str) else tuple(e) for e in d["coords"]]
    if "time" in d:
        spine = PeriodRange.from_data(d["time"], len(entries))
        if list(spine.labels) != entries:
            raise DefinitionError(f"dimension {d['name']!r}: its coords don't match its time spine {d['time']}")
        return spine
    return Hierarchy(entries) if d.get("hierarchy") else entries


def _value_data(e: Entry) -> dict[str, Any]:
    """An entered value as plain data: the value (or a cell formula's lambda), and source."""
    data: dict[str, Any] = (e.value.to_data() if _is_formula(e.value)
                            else {"value": None if e.value is NIL else dates.to_json(e.value)})
    data["source"] = e.source
    return data


def _value_from_data(data: Mapping[str, Any], formulas: str = "on", where: str = "formula") -> Any:
    if "formula" in data:
        return _formula_from_data(data, "formula", where, formulas)
    return NIL if data["value"] is None else data["value"]


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
        data |= item.formula.to_data()
    return data


def _formula_from_data(data: Mapping[str, Any], key: str, where: str, formulas: str = "on") -> FormulaBase:
    """A saved formula: "lambda x: ..." with its "env", or an older file's expression text or
    {"python": source, "env": {...}}."""
    saved = data[key]
    if formulas == "off":
        return Uncompiled({key: saved} | ({"env": data["env"]} if "env" in data else {}))
    if isinstance(saved, dict):
        if not isinstance(saved.get("python"), str):
            raise DefinitionError(f"{where}: can't read saved formula {saved!r}")
        return Formula(saved["python"], env_from_data(saved.get("env", {}), where), where)
    return Formula(from_text(saved, where), env_from_data(data.get("env", {}), where), where)


def _no_allow_code(allow_code: bool | None) -> None:
    if allow_code is not None:
        warnings.warn("allow_code is ignored: formulas are checked and compiled safely, and never need it",
                      DeprecationWarning, stacklevel=3)


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
    saved = check.expect.to_data()
    if "formula" in saved:
        saved["expect"] = saved.pop("formula")
    return data | saved


def _identifier(name: object) -> str:
    text = re.sub(r"\W+", "_", str(name)).strip("_") or "item"
    if not text.isidentifier():  # starts with a digit
        text = f"item_{text}"
    return f"{text}_" if keyword.iskeyword(text) or text == "abs" else text


def _kv(attrs: Mapping[str, Any]) -> str:
    return " ".join(f"{k}={v}" for k, v in attrs.items())


def _source_text(source: Mapping[str, Any]) -> str:
    text = f"{source['document']}, {source['section']}"
    return text + (f", p. {source['page']}" if source.get("page") else "")


def _fmt(coords: Mapping[str, str]) -> str:
    return "[" + ", ".join(f"{d}={v}" for d, v in coords.items()) + "]"

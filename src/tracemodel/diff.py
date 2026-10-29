"""What differs between two models: definitions, entered values, and the calculated cells
that moved, each with the changes that moved it.

    tm.diff(before, after)                       # two models
    repo.diff("main", "high-qty", calculated=True)   # two commits or branches
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .ops import Op, same, state
from .table import Table

if TYPE_CHECKING:
    from .model import Model

__all__ = ["Change", "Diff", "diff"]


@dataclass(frozen=True)
class Change:
    """One object that differs: `change` is "added", "removed" or "changed"; `before` and
    `after` are its states as plain data (None where it doesn't exist). A calculated cell's
    states are its values, and `because` names the differing objects it rests on."""

    target: str  # e.g. "value:price[term=T1]", "item:revenue", "calc:revenue[term=T1]"
    change: str
    before: Any
    after: Any
    because: tuple[str, ...] = field(default=())

    @property
    def kind(self) -> str:
        """"dimension", "item", "check", "value" (entered) or "calc" (calculated)."""
        return self.target.split(":", 1)[0]

    def __str__(self) -> str:
        why = f"  because {', '.join(self.because)}" if self.because else ""
        return f"{self.change:<8} {self.target}: {_show(self.before)} -> {_show(self.after)}{why}"


class Diff:
    """The objects that differ between two models, definitions first, then entered values,
    then calculated cells."""

    def __init__(self, changes: list[Change], title: str = "diff"):
        self.changes, self.title = changes, title

    def __iter__(self) -> Iterator[Change]:
        return iter(self.changes)

    def __len__(self) -> int:
        return len(self.changes)

    def __bool__(self) -> bool:
        return bool(self.changes)

    def of(self, kind: str) -> list[Change]:
        """The changes to one kind of object: "dimension", "item", "check", "value" or "calc"."""
        return [c for c in self.changes if c.kind == kind]

    def table(self) -> Table:
        rows = [[c.target, c.change, _show(c.before), _show(c.after), ", ".join(c.because)] for c in self.changes]
        return Table(["object", "change", "before", "after", "because"], rows, title=self.title)

    def show(self) -> None:
        self.table().show()

    def __str__(self) -> str:
        return str(self.table()) if self.changes else f"{self.title}: no differences"

    __repr__ = __str__

    def _repr_html_(self) -> str:
        return self.table()._repr_html_()


def diff(before: Model, after: Model, calculated: bool = False, title: str = "diff") -> Diff:
    """What differs between two models. With calculated=True, also every calculated cell whose
    value differs, each with the differing definitions and entered values it rests on."""
    out: list[Change] = []
    for kind, names in (("dimension", _union(before.dimensions, after.dimensions)),
                        ("item", _union(before.items, after.items)),
                        ("check", _union(before.checks, after.checks))):
        op_kind = {"dimension": "dimension.add", "item": "item.add", "check": "check.set"}[kind]
        for name in names:
            probe = Op(op_kind, name)
            out += _compare(probe.target, state(before, probe), state(after, probe))
    keys = list(dict.fromkeys([*before._entries, *after._entries]))
    for item, labels in keys:
        source = after if (item, labels) in after._entries else before
        probe = Op("value.set", {"item": item, "coords": dict(zip(source.items[item].dims, labels))})
        out += _compare(probe.target, state(before, probe), state(after, probe))
    if calculated:
        moves = {c.target for c in out if c.kind != "item" or _moves_values(c)}
        out += _calculated(before, after, moves)
    return Diff(out, title)


def _moves_values(c: Change) -> bool:
    """An item change that can move a value: its formula or dtype, not its attrs or description."""
    a, b = c.before or {}, c.after or {}
    return any(a.get(k) != b.get(k) for k in ("formula", "env", "dtype", "dims"))


def _compare(target: str, a: Any, b: Any) -> list[Change]:
    if same(a, b):
        return []
    return [Change(target, "added" if a is None else "removed" if b is None else "changed", a, b)]


def _union(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys([*a, *b]))


def _calculated(before: Model, after: Model, differing: set[str]) -> list[Change]:
    out = []
    for name in _union(before.items, after.items):
        cells, was = _formula_cells(after, name), _formula_cells(before, name)
        for model_labels in [*cells, *(c for c in was if c not in cells)]:
            coords = dict(model_labels)  # a cell no longer calculated (entered now) counts as removed
            a = _calc(before, name, coords) if model_labels in was else None
            b = _calc(after, name, coords) if model_labels in cells else None
            if a == b or (a is None and b is None):
                continue
            target = "calc:" + Op("value.set", {"item": name, "coords": coords}).target.split(":", 1)[1]
            change = "added" if a is None else "removed" if b is None else "changed"
            out.append(Change(target, change, a, b, _because(after if b is not None else before, name, coords,
                                                             differing)))
    return out


def _formula_cells(model: Model, name: str) -> dict[tuple[tuple[str, str], ...], None]:
    """The cells a formula calculates: every cell of a formula item without an entered value,
    and every cell formula."""
    from .model import _is_formula

    item = model.items.get(name)
    if item is None:
        return {}
    out: dict[tuple[tuple[str, str], ...], None] = {}  # ordered: the dims' order
    for labels in itertools.product(*(model.dimensions[d].coords for d in item.dims)):
        e = model._entries.get((name, labels))
        if (e is None and item.formula is not None) or (e is not None and _is_formula(e.value)):
            out[tuple(zip(item.dims, labels))] = None
    return out


def _calc(model: Model, name: str, coords: dict[str, str]) -> Any:
    from .expr import Cell

    item = model.items.get(name)
    if item is None or any(coords.get(d) not in model.dimensions[d] for d in item.dims):
        return None
    try:
        return model._value(Cell(model, item, dict(coords)), {})
    except Exception:  # noqa: BLE001 - a cell that can't be calculated has no value to compare
        return None


def _because(model: Model, name: str, coords: dict[str, str], differing: set[str]) -> tuple[str, ...]:
    """The differing objects a calculated cell rests on: the entered values it reads, the items
    whose formulas it runs through, and the dimensions those use."""
    read: list[str] = [f"item:{name}"]
    try:
        traces = model.precedents(name, deep=True, **coords)
    except Exception:  # noqa: BLE001
        traces = []
    for t in traces:
        for cell in [t, *t.flatten()]:
            read.append(f"item:{cell.name}")
            read.append(Op("value.set", {"item": cell.name, "coords": cell.coords}).target)
    read += [f"dimension:{d}" for d in model.items[name].dims]
    return tuple(t for t in dict.fromkeys(read) if t in differing)


def _show(v: Any) -> Any:
    """A state as people read it: a value, "= formula", or what a definition says."""
    if v is None or isinstance(v, (int, float, str)):
        return v
    if isinstance(v, dict):
        if "value" in v:
            return v["value"]
        if "formula" in v and "dims" not in v:
            return f"= {v['formula']}"
        if "dims" in v:
            parts = [f"({', '.join(v['dims'])}) {v.get('dtype', '')}".strip()]
            if v.get("formula"):
                parts.append(f"= {v['formula']}")
            if v.get("attrs"):
                parts.append(" ".join(f"{k}={x}" for k, x in v["attrs"].items()))
            return "  ".join(parts)
        if "expect" in v:
            return v["expect"]
        if "coords" in v:
            return ", ".join(c if isinstance(c, str) else c[0] for c in v["coords"])
    return str(v)

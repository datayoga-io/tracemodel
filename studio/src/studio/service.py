"""What the grid asks of a model, as plain data: definitions, cells, a cell's detail, edits and commits.

Everything here goes through tracemodel's public API (a `Repo`, its `Model`, `check()`), except
one read of a calculated cell's exact reads, noted where it is made.
"""

from __future__ import annotations

import itertools
import threading
from datetime import date, datetime
from typing import Any

import tracemodel as tm

EDIT_SOURCE = {"document": "Studio edit", "section": "Grid", "url": "studio://grid"}


class Studio:
    """A repo opened by one author, and the views on it that the grid needs."""

    def __init__(self, repo: tm.Repo, title: str, tables: list[dict[str, Any]], views: list[dict[str, Any]]):
        self.repo, self.title, self.tables, self.views = repo, title, tables, views
        self.lock = threading.RLock()

    # == what the model is ==
    def definition(self) -> dict[str, Any]:
        with self.lock:
            m = self.repo.model
            return {
                "title": self.title,
                "branch": self.repo.branch,
                "head": self.repo.head[:8],
                "dimensions": {name: {"title": d.title or "", "labels": [{"code": c, "description": d.descriptions.get(c), "attrs": dict(d.attrs(c))}
                                                 for c in d.coords]} for name, d in m.dimensions.items()},
                "items": {name: _item(i) for name, i in m.items.items()},
                "checks": [{"id": c.id, "description": c.description or ""} for c in m.checks.values()],
                "tables": self.tables,
                "views": self.views,
            }

    # == the numbers ==
    def cells(self) -> dict[str, Any]:
        """Every cell of every item: the value (None when missing), whether it was entered as nil, and
        the keys with changes not yet committed."""
        with self.lock:
            m = self.repo.model
            out: dict[str, dict[str, Any]] = {}
            for name, item in m.items.items():
                cells = {}
                for labels in itertools.product(*(m.dimensions[d].coords for d in item.dims)):
                    cells["|".join(labels)] = _cell(m, item, dict(zip(item.dims, labels)))
                out[name] = cells
            return {"cells": out, "pending": sorted(self._pending(m)), "changes": len(self.repo.status()),
                    "head": self.repo.head[:8]}

    def _pending(self, m: tm.Model) -> set[str]:
        keys = set()
        for op in self.repo.status():
            if op.kind in ("value.set", "value.clear"):
                item = op.key["item"]
                keys.add(item + "|" + "|".join(op.key["coords"][d] for d in m.items[item].dims))
        return keys

    def cell(self, item: str, coords: dict[str, str]) -> dict[str, Any]:
        """One cell with what's behind it: its source and history, or its formula and what it read."""
        with self.lock:
            m = self.repo.model
            it = m.item(item)
            out: dict[str, Any] = {"item": item, "coords": coords, "kind": "calc" if it.formula else "entered",
                                   **_cell(m, it, coords), "formula": it.formula.source if it.formula else None,
                                   "checks": self._failing_here(m, item, coords)}
            if it.formula is not None:
                out["reads"] = _reads(m, it, coords)
                return out
            try:
                entry = m.entry(item, **coords)
                out["source"] = dict(entry.source)
            except tm.MissingValue:
                out["source"] = None
            versions = self.repo.history(item, **coords)
            out["history"] = [_version(v) for v in reversed(versions)]  # newest first
            out["approved_by"] = self.repo.approved_by(item, **coords) if versions else None
            return out

    def checks(self) -> list[dict[str, Any]]:
        """Each check with the cells its failing runs read, and why they fail."""
        with self.lock:
            m = self.repo.model
            report = m.check()
            out = []
            for check in m.checks.values():
                failing = []
                for r in report.results:
                    if r.check.id != check.id or r.passed:
                        continue
                    # A missing cell is the failure. Otherwise the cells the rule reads at the run's own coordinates;
                    # `cells` also holds everything those were calculated from, which would mark the whole model.
                    direct = [c for c in r.cells if all(r.at.get(d) == v for d, v in c.coords.items())]
                    shown = r.missing or direct or r.cells[:1]
                    failing.append({"at": r.at, "why": _why(r),
                                    "cells": [{"item": c.item, "coords": c.coords} for c in shown]})
                out.append({"id": check.id, "description": check.description or "", "failing": failing})
            return out

    def _failing_here(self, m: tm.Model, item: str, coords: dict[str, str]) -> list[dict[str, Any]]:
        here = []
        for c in self.checks():
            for run in c["failing"]:
                if any(x["item"] == item and x["coords"] == coords for x in run["cells"]):
                    here.append({"id": c["id"], "why": run["why"]})
        return here

    # == changing it ==
    def set_value(self, item: str, coords: dict[str, str], value: Any, nil: bool = False) -> None:
        with self.lock:
            m = self.repo.model
            if nil:
                m.set_value(item, coords, tm.NIL, EDIT_SOURCE)
            elif value is None:
                m.clear_value(item, coords)
            else:
                m.set_value(item, coords, value, EDIT_SOURCE)

    # == its shape: members of a dimension (rows) and items (columns) ==
    def add_member(self, dim: str, code: str, description: str, values: dict[str, Any]) -> None:
        """Add a label to a dimension, with the values to give it (a pointer to its parent, say), or nothing at all."""
        with self.lock:
            m = self.repo.model
            code = (code or "").strip()
            if dim not in m.dimensions:
                raise tm.UnknownCoord(f"no dimension {dim!r}")
            if not code:
                raise tm.DefinitionError("a new row needs a code")
            if code in m.dimensions[dim].coords:
                raise tm.DefinitionError(f"{dim} {code} already exists")
            with m.transaction():  # all of it, or none
                m.add_coords(dim, [(code, description.strip()) if description.strip() else code])
                for item, value in values.items():
                    if m.item(item).dims != (dim,):
                        raise tm.DefinitionError(f"{item} is not an item of {dim} alone")
                    m.set_value(item, {dim: code}, value, EDIT_SOURCE)

    def remove_member(self, dim: str, code: str) -> None:
        with self.lock:
            self.repo.model.remove_coords(dim, [code])

    def member_impact(self, dim: str, code: str) -> dict[str, Any]:
        """What removing a label would take with it, and what stops it."""
        with self.lock:
            m = self.repo.model
            if dim not in m.dimensions or code not in m.dimensions[dim].coords:
                raise tm.UnknownCoord(f"no {dim} {code!r}")
            values = 0
            for item in m.items.values():
                if dim in item.dims and item.formula is None:
                    try:
                        values += len(m.entry(item.name, **{dim: code}).rows) if len(item.dims) > 1 else 1
                    except tm.TraceModelError:
                        pass
            pointed = []
            for item in m.items.values():
                if item.ref == dim:
                    for labels in itertools.product(*(m.dimensions[d].coords for d in item.dims)):
                        coords = dict(zip(item.dims, labels))
                        if _cell(m, item, coords).get("v") == code:
                            pointed.append({"item": item.name, "coords": coords})
            return {"values": values, "pointed_by": pointed}

    def add_item(self, spec: dict[str, Any]) -> None:
        with self.lock:
            m = self.repo.model
            kind = spec.get("dtype", "float")
            dtype: Any = tm.Ref(spec["ref"]) if kind == "ref" else kind
            unit = (spec.get("unit") or "").strip()
            m.add_item(spec["name"].strip(), spec["dims"], dtype, {"unit": unit} if unit else None,
                       formula=(spec.get("formula") or "").strip() or None,
                       description=(spec.get("description") or "").strip() or None)

    def update_item(self, name: str, description: str | None, unit: str | None, formula: str | None, set_formula: bool) -> None:
        with self.lock:
            m = self.repo.model
            item = m.item(name)
            with m.transaction():
                if description is not None or unit is not None:
                    attrs = dict(item.attrs)
                    if unit is not None:
                        attrs.pop("unit", None)
                        if unit.strip():
                            attrs["unit"] = unit.strip()
                    m.update_item(name, attrs=attrs, description=(description if description is not None else item.description) or None)
                if set_formula:
                    m.set_formula(name, (formula or "").strip() or None)

    def remove_item(self, name: str) -> None:
        with self.lock:
            self.repo.model.remove_item(name)

    def item_impact(self, name: str) -> dict[str, Any]:
        """The values an item holds, and the formulas and checks that read it (which would refuse its removal)."""
        with self.lock:
            m = self.repo.model
            item = m.item(name)
            values = 0
            if item.formula is None:
                values = sum(1 for labels in itertools.product(*(m.dimensions[d].coords for d in item.dims))
                             if _cell(m, item, dict(zip(item.dims, labels))).get("v") is not None
                             or _cell(m, item, dict(zip(item.dims, labels))).get("nil"))
            needles = (f'["{name}"]', f"['{name}']")
            readers = [i.name for i in m.items.values() if i.name != name and i.formula is not None
                       and any(n in i.formula.source for n in needles)]
            readers += [c.id for c in m.checks.values() if any(n in c.expect.source for n in needles)]
            return {"values": values, "read_by": readers}

    def discard(self) -> None:
        with self.lock:
            self.repo.discard()

    def commit(self, message: str) -> dict[str, Any]:
        with self.lock:
            self.repo.model  # noqa: B018 - make sure the model is built before committing
            info = self.repo.commit(message)
            return _commit(info)

    def log(self) -> list[dict[str, Any]]:
        with self.lock:
            return [_commit(c) for c in self.repo.log()]


def _item(i: tm.Item) -> dict[str, Any]:
    dtype = "ref" if i.ref else {int: "int", float: "float", str: "str", date: "date", datetime: "datetime"}[i.dtype]
    return {"name": i.name, "dims": list(i.dims), "kind": "calc" if i.formula is not None else "entered",
            "dtype": dtype, "ref": i.ref, "unit": i.attrs.get("unit", ""), "description": i.description or "", "note": i.attrs.get("note", ""),
            "formula": i.formula.source if i.formula is not None else None}


def _json(v: Any) -> Any:
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _cell(m: tm.Model, item: tm.Item, coords: dict[str, str]) -> dict[str, Any]:
    """{"v": value or None} plus "nil" for a cell entered as nil, and "err" when a formula can't be calculated."""
    try:
        if item.formula is None:
            e = m.entry(item.name, **coords)
            if e.value is tm.NIL:
                return {"v": None, "nil": True}
            if not hasattr(e.value, "source"):  # not a formula for this one cell
                return {"v": _json(e.value)}
        return {"v": _json(m.get(item.name, **coords))}
    except tm.MissingValue:
        return {"v": None}
    except tm.TraceModelError as e:
        return {"v": None, "err": f"{type(e).__name__}: {e}"}


def _reads(m: tm.Model, item: tm.Item, coords: dict[str, str]) -> list[dict[str, Any]]:
    """The cells a calculated cell read when it was last worked out.

    The model keeps these as `_deps`; there is no public call for them yet (precedents() gives
    every cell a formula could read, which is more than it did when it follows a pointer).
    """
    try:
        m.get(item.name, **coords)
    except tm.TraceModelError:
        pass
    out, seen = [], set()
    for cell, at in m._deps.get((item.name, tuple(coords[d] for d in item.dims)), []):  # noqa: SLF001
        try:
            full = cell.resolve(at)
        except tm.TraceModelError:
            continue
        key = (cell.item.name, tuple(full.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append({"item": cell.item.name, "coords": full, **_cell(m, cell.item, full)})
    return out


def _why(r: Any) -> str:
    if r.error:
        return r.error
    if r.missing:
        return "; ".join(f"{c.item} {', '.join(f'{d}={v}' for d, v in c.coords.items())} has no value" for c in r.missing)
    if r.lhs is not None and r.rhs is not None:
        return f"{r.lhs:g} against {r.rhs:g}" if isinstance(r.lhs, (int, float)) else f"{r.lhs} against {r.rhs}"
    return "the rule does not hold"


def _version(v: tm.Version) -> dict[str, Any]:
    """One change to a value, with its commit: `nil` when it was entered as nil, `removed` when cleared."""
    data = v.op.new
    base = {"commit": v.commit.short, "author": v.commit.author, "at": v.commit.at, "message": v.commit.message}
    if data is None:
        return {**base, "value": None, "removed": True, "source": None}
    if "formula" in data:
        return {**base, "value": data["formula"], "source": data.get("source")}
    return {**base, "value": data.get("value"), "nil": data.get("value") is None, "source": data.get("source")}


def _commit(c: tm.CommitInfo) -> dict[str, Any]:
    return {"id": c.short, "author": c.author, "at": c.at, "message": c.message, "changes": len(c.ops)}

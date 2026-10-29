"""Operations: every change to a model as one record of plain data, to replay or undo.

Each verb that changes a model (add_item, set_value, remove_coords, ...) records one `Op` when
it succeeds, while the model is recording (`model.recording()`). A verb made of others records
theirs first: remove_item records a value.clear for each value, then item.remove. An op holds the
object before (`old`) and after (`new`) as plain data, in the shapes the definition and values
files use, so an op undoes itself (`invert`) and replays into another model (`apply`) through the
same verb, checked the same way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model import Model

KINDS = (
    "dimension.add", "dimension.remove", "coords.add", "coords.remove",
    "item.add", "item.update", "item.remove", "check.set", "check.remove",
    "value.set", "value.clear",
)
_OPPOSITE = {"dimension.add": "dimension.remove", "dimension.remove": "dimension.add",
             "coords.add": "coords.remove", "coords.remove": "coords.add",
             "item.add": "item.remove", "item.remove": "item.add",
             "check.remove": "check.set", "value.clear": "value.set"}


@dataclass(frozen=True)
class Op:
    """One change: `kind`, the object's `key`, and the object before (`old`) and after (`new`)
    as plain data, None when it didn't or doesn't exist.

    `key` is a dimension, item or check name, or {"item": ..., "coords": {...}} for a value.
    """

    kind: str
    key: Any
    old: Any = None
    new: Any = None

    @property
    def target(self) -> str:
        """The object as one string, e.g. "item:revenue" or "value:rate[term=T1]": two ops on the
        same object have the same target."""
        kind = self.kind.split(".")[0]
        if kind == "value":
            coords = ", ".join(f"{d}={v}" for d, v in self.key["coords"].items())
            return f"value:{self.key['item']}[{coords}]"
        return f"{'dimension' if kind == 'coords' else kind}:{self.key}"

    def to_data(self) -> dict[str, Any]:
        return {"kind": self.kind, "key": self.key, "old": self.old, "new": self.new}

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> Op:
        if data.get("kind") not in KINDS:
            raise ValueError(f"not an op: {data!r}")
        return cls(data["kind"], data["key"], data.get("old"), data.get("new"))

    def __repr__(self) -> str:
        return f"<Op {self.kind} {self.target}>"


def invert(op: Op) -> Op:
    """The op that undoes `op`: before and after swapped."""
    kind = _OPPOSITE.get(op.kind, op.kind)
    if op.kind == "value.set" and op.old is None:
        kind = "value.clear"
    elif op.kind == "check.set" and op.old is None:
        kind = "check.remove"
    return Op(kind, op.key, op.new, op.old)


def apply(model: Model, op: Op) -> None:
    """Make the change `op` records, through the verb that makes it, checked as that verb checks."""
    from .model import _dimension_from_data, _formula_from_data, _spec_from_data, _value_from_data

    kind, key, old, new = op.kind, op.key, op.old, op.new
    if kind == "dimension.add":
        model.add_dimension(key, _dimension_from_data(new))
    elif kind == "dimension.remove":
        model.remove_dimension(key)
    elif kind == "coords.add":
        if "time" in new:  # the whole spine: add_coords takes the periods that continue it
            model.add_coords(key, _dimension_from_data(new))
        else:
            known = set(model.dimensions[key].coords)
            model.add_coords(key, [e for e in _entries(new) if _code(e) not in known])
    elif kind == "coords.remove":
        keep = {_code(e) for e in new["coords"]}
        model.remove_coords(key, [c for c in model.dimensions[key].coords if c not in keep])
    elif kind == "item.add":
        model.add_item(key, new["dims"], new["dtype"], new.get("attrs"), _item_formula(new),
                       new.get("description"))
    elif kind == "item.update":
        if (old or {}).get("formula") != new.get("formula") or (old or {}).get("env") != new.get("env"):
            model.set_formula(key, _item_formula(new))
        if (old or {}).get("attrs") != new.get("attrs") or (old or {}).get("description") != new.get("description"):
            model.update_item(key, attrs=new.get("attrs"), description=new.get("description"))
    elif kind == "item.remove":
        model.remove_item(key)
    elif kind == "check.set":
        where = f"check {key!r}: expect"
        model.add_check(key, _formula_from_data(new, "expect", where),
                        {d: _spec_from_data(s) for d, s in new.get("coords", {}).items()} or None,
                        new.get("description"), new.get("severity", "error"))
    elif kind == "check.remove":
        model.remove_check(key)
    elif kind == "value.set":
        model.set_value(key["item"], key["coords"], _value_from_data(new), new["source"])
    elif kind == "value.clear":
        model.clear_value(key["item"], key["coords"])
    else:
        raise ValueError(f"unknown op kind {kind!r}")


def state(model: Model, op: Op) -> Any:
    """The object `op` changes, as it stands in `model`, in the shape of `op.new`: None when the
    model doesn't have it."""
    from .model import _check_data, _dimension_data, _item_data, _value_data

    kind = op.kind.split(".")[0]
    if kind in ("dimension", "coords"):
        dim = model.dimensions.get(op.key)
        return None if dim is None else _dimension_data(dim)
    if kind == "item":
        item = model.items.get(op.key)
        return None if item is None else _item_data(item, model)
    if kind == "check":
        check = model.checks.get(op.key)
        return None if check is None else _check_data(check, model)
    item = model.items.get(op.key["item"])
    if item is None:
        return None
    entry = model._entries.get((item.name, tuple(op.key["coords"].get(d) for d in item.dims)))
    return None if entry is None else _value_data(entry)


def same(a: Any, b: Any) -> bool:
    """Two objects' states are the same, compared as plain data."""
    return canonical(a) == canonical(b)


def replay(model: Model, ops: list[Op]) -> Model:
    """Apply `ops` in order, in one transaction: all of them, or none."""
    with model.transaction():
        for op in ops:
            apply(model, op)
    return model


def canonical(data: Any) -> str:
    """One spelling of plain data, so equal ops hash equally."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _item_formula(data: dict[str, Any]) -> Any:
    from .model import _formula_from_data

    return None if data.get("formula") is None else _formula_from_data(data, "formula",
                                                                        f"item {data['name']!r}: formula")


def _entries(data: dict[str, Any]) -> list[Any]:
    return [e if isinstance(e, str) else tuple(e) for e in data["coords"]]


def _code(entry: Any) -> str:
    return entry if isinstance(entry, str) else entry[0]

"""Reviewing a proposal: a branch whose changes a person accepts or rejects before they land.

An agent (or anyone) works on a branch; a reviewer reads what it changes (`repo.review`), rules
on it, and lands what was accepted (`repo.approve`). A ruling is a `Review` record in the log,
never a field on the value: the value's meta is what its writer said, a ruling is what someone
else decided later.

A ruling names a scope, narrowest first:

| scope                         | covers                                                        |
| ----------------------------- | ------------------------------------------------------------- |
| `cell("rate", term="T1")`     | one entered value or cell formula                             |
| `slice(contract="C-104")`     | every value the branch changed at that label                  |
| `item("fee")`, `check(id)`, `dimension(name)` | the definition, and an item's values too      |
| `commit(id)`                  | every change in one commit                                    |
| `ALL`                         | everything the branch changed                                 |

The narrowest ruling that covers a change decides it; at the same level the later one wins, so
a reject can follow an accept. A ruling covers the changes up to the branch head it was made
at: a value changed again after it is undecided again. Nobody's ruling counts for a change they
made themselves; a person's own changes on a repo with `self_approve` are accepted by them as
they commit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ops import Op
from .table import Table

__all__ = ["ALL", "Item", "Report", "cell", "check", "commit", "dimension", "item", "slice"]

ALL = "*"
ACTIONS = ("accept", "reject", "comment")
LEVELS = ("cell", "slice", "item", "commit", "all")  # narrowest first


def cell(line_item: str, **coords: str) -> str:
    """The scope of one value: cell("rate", term="T1")."""
    return Op("value.set", {"item": line_item, "coords": coords}).target


def slice(**coords: str) -> str:
    """The scope of every value at these labels: slice(contract="C-104")."""
    if not coords:
        raise ValueError("a slice names at least one label, e.g. slice(contract='C-104')")
    return "slice:" + ",".join(f"{d}={v}" for d, v in coords.items())


def item(name: str) -> str:
    return f"item:{name}"


def check(id: str) -> str:
    return f"check:{id}"


def dimension(name: str) -> str:
    return f"dimension:{name}"


def commit(id: str) -> str:
    return f"commit:{id}"


def level(scope: str, target: str, key: Any, commit_id: str) -> int | None:
    """How narrowly `scope` covers the change to `target` (key: the op's key) made in
    `commit_id`: an index into LEVELS, or None when it doesn't cover it."""
    if scope == target:
        return 0
    if scope.startswith("slice:") and target.startswith("value:"):
        wanted = dict(part.split("=", 1) for part in scope[6:].split(","))
        return 1 if all(key["coords"].get(d) == v for d, v in wanted.items()) else None
    if scope.startswith("item:") and target.startswith("value:") and key["item"] == scope[5:]:
        return 2
    if scope.startswith("commit:") and commit_id.startswith(scope[7:]):
        return 3
    if scope == ALL:
        return 4
    return None


@dataclass(frozen=True)
class Item:
    """One object a branch changed, as a reviewer reads it.

    `before` is the object at the branch point and `after` on the branch, as plain data (None
    where it doesn't exist); `meta` is what the writer said about a value (confidence,
    reasoning, origin). `decision` is "accepted", "rejected" or None (undecided), with who
    decided, at which level, and their note.
    """

    target: str
    change: str  # "added", "removed" or "changed"
    before: Any
    after: Any
    author: str
    commit: str
    meta: dict[str, Any] = field(default_factory=dict)
    decision: str | None = None
    by: str | None = None
    level: str | None = None
    note: str = ""
    comments: tuple[str, ...] = ()

    @property
    def kind(self) -> str:
        return self.target.split(":", 1)[0]


class Report:
    """What a branch proposes against its target, object by object, with each decision; and the
    calculated cells that move, with the undecided changes each rests on."""

    def __init__(self, branch: str, onto: str, items: list[Item], calculated: list[Any]):
        self.branch, self.onto, self.items, self.calculated = branch, onto, items, calculated

    def undecided(self) -> list[Item]:
        return [i for i in self.items if i.decision is None]

    def accepted(self) -> list[Item]:
        return [i for i in self.items if i.decision == "accepted"]

    def rejected(self) -> list[Item]:
        return [i for i in self.items if i.decision == "rejected"]

    def __getitem__(self, target: str) -> Item:
        for i in self.items:
            if i.target == target:
                return i
        raise KeyError(target)

    def table(self) -> Table:
        from .diff import _show

        rows = [[i.target, i.change, _show(i.before), _show(i.after), i.meta.get("confidence"), i.author,
                 i.decision or "undecided", i.by] for i in self.items]
        rows += [[c.target, c.change, _show(c.before), _show(c.after), None, None,
                  "rests on undecided" if c.because else "", ", ".join(c.because) or None] for c in self.calculated]
        return Table(["object", "change", "before", "after", "confidence", "by", "decision", "decided by / rests on"],
                     rows, title=f"{self.branch} -> {self.onto}")

    def show(self) -> None:
        self.table().show()

    def __str__(self) -> str:
        return str(self.table())

    __repr__ = __str__

    def _repr_html_(self) -> str:
        return self.table()._repr_html_()

"""Value entries and check results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

from . import dates
from .errors import UnknownCoord
from .table import Table, _fmt

if TYPE_CHECKING:
    from .formula import Formula
    from .model import Check, Model

SOURCE_REQUIRED = ("document", "url", "section")
SOURCE_OPTIONAL = ("page", "quote")


class _Nil:
    """The value of a cell the filing reports as nil ("—"). Checks count it as 0."""

    _instance: _Nil | None = None

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "tm.NIL"

    def __str__(self) -> str:
        return "—"


NIL = _Nil()


@dataclass
class Entry:
    item: str
    coords: dict[str, str]
    value: float | _Nil | Formula  # a Formula calculates this one cell
    source: dict[str, Any]
    history: list[tuple[float | _Nil | Formula, dict[str, Any]]] = field(default_factory=list)  # replaced (value, source)
    overrides_formula: bool = False  # entered over the item's formula

    def to_json(self) -> dict[str, Any]:
        row = {"line_item": self.item, "coords": self.coords,
               "value": None if self.value is NIL else dates.to_json(self.value), "source": self.source}
        return row | ({"overrides_formula": True} if self.overrides_formula else {})


@dataclass
class CellRead:
    item: str
    coords: dict[str, str]
    value: float | _Nil | None  # None when missing

    def __str__(self) -> str:
        where = ", ".join(f"{d}={v}" for d, v in self.coords.items())
        return f"{self.item}[{where}] = {'MISSING' if self.value is None else _fmt(self.value)}"


@dataclass
class Trace:
    """A cell or a check run in a precedents or dependents trace, as Excel's Trace Precedents
    and Trace Dependents draw them. `children` is filled with deep=True; a cell already shown
    higher up the trace is listed again with `repeated` set and no children."""

    name: str  # the item, or the check id
    coords: dict[str, str]
    kind: str = "cell"  # "cell" or "check"
    children: list[Trace] = field(default_factory=list)
    repeated: bool = False

    def flatten(self) -> list[Trace]:
        """Every cell and check below this one, each once, nearest first."""
        out, seen, level = [], {self.key}, list(self.children)
        while level:
            nxt = []
            for t in level:
                if t.key not in seen:
                    seen.add(t.key)
                    out.append(t)
                    nxt += t.children
            level = nxt
        return out

    @property
    def key(self) -> tuple:
        return self.kind, self.name, tuple(self.coords.items())

    def __str__(self) -> str:
        where = ", ".join(f"{d}={v}" for d, v in self.coords.items())
        head = f"check {self.name}[{where}]" if self.kind == "check" else f"{self.name}[{where}]"
        lines = [head + (" (see above)" if self.repeated else "")]
        for c in self.children:
            lines += ["  " + line for line in str(c).splitlines()]
        return "\n".join(lines)

    def __repr__(self) -> str:
        return str(self).splitlines()[0]


@dataclass
class CheckResult:
    check: Check
    at: dict[str, str]
    lhs: float | None
    rhs: float | None
    passed: bool
    cells: list[CellRead]
    expect: Formula  # the check's lambda
    error: str | None = None  # a check that raised or didn't return a bool

    @property
    def diff(self) -> float | None:
        from .expr import diff

        return diff(self.lhs, self.rhs)

    @property
    def missing(self) -> list[CellRead]:
        return [c for c in self.cells if c.value is None]


class Report:
    def __init__(self, results: list[CheckResult], model: Model | None = None):
        self.results = results
        self._model = model

    def filter(self, check: str | None = None, **coords: Any) -> Report:
        """The runs of one check and/or on some labels, e.g.
        report.filter(period=tm.filter_by_attrs(kind="forecast")).ok

        Each coord takes a label, a list of labels, tm.ANY or tm.filter_by_attrs(...); a run
        that doesn't have the dim (a check that fixes it) is left out.
        """
        from .model import _labels

        keep = {}
        for dim, spec in coords.items():
            if self._model is None or dim not in self._model.dimensions:
                raise UnknownCoord(f"no dimension {dim!r}")
            keep[dim] = set(_labels(self._model.dimensions[dim], spec))
        results = [r for r in self.results
                   if (check is None or r.check.id == check)
                   and all(r.at.get(d) in labels for d, labels in keep.items())]
        return Report(results, self._model)

    @property
    def ok(self) -> bool:
        """True when no error check fails; failing warnings don't count."""
        return not self.errors

    @property
    def failures(self) -> list[CheckResult]:
        """Every run that didn't pass, errors and warnings."""
        return [r for r in self.results if not r.passed]

    @property
    def errors(self) -> list[CheckResult]:
        return [r for r in self.failures if r.check.severity == "error"]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.failures if r.check.severity == "warning"]

    def table(self) -> Table:
        rows = []
        for r in self.results:
            where = ", ".join(f"{d}={v}" for d, v in r.at.items())
            rows.append([r.check.id, where, _status(r), r.lhs, getattr(r.expect, "rule", ""), r.rhs, r.diff])
        n_pass = sum(r.passed for r in self.results)
        n_warn = len(self.warnings)
        title = f"{n_pass}/{len(self.results)} check runs passed" + (
            f", {n_warn} warning{'s' * (n_warn != 1)}" if n_warn else "")
        return Table(["check", "coords", "result", "left", "rule", "right", "diff"], rows, title=title)

    def explain(self, check_id: str | None = None) -> str:
        """Every run with the cells it read: the trace behind each pass or fail."""
        lines = []
        for r in self.results:
            if check_id and r.check.id != check_id:
                continue
            where = ", ".join(f"{d}={v}" for d, v in r.at.items())
            status = _status(r)
            sides = "" if r.lhs is None and r.rhs is None else (
                f"  left {_fmt(r.lhs)}  {r.expect.rule}  right {_fmt(r.rhs)}  diff {_fmt(r.diff)}")
            lines.append(f"{r.check.id}  {where}  {status}{sides}")
            lines.append(f"    {r.expect!r}")
            lines.extend(f"      {c}" for c in r.cells)
            if r.error:
                lines.append(f"      error: {r.error}")
        return "\n".join(lines)

    def show(self) -> None:
        self.table().show()

    def __str__(self) -> str:
        return str(self.table())

    __repr__ = __str__

    def _repr_pretty_(self, p, cycle) -> None:
        p.text(str(self))

    def _repr_html_(self) -> str:
        return self.table()._repr_html_()


def _status(r: CheckResult) -> str:
    """pass, FAIL or missing; a failing warning check shows warn (or warn: missing)."""
    if r.passed:
        return "pass"
    if r.check.severity == "warning":
        return "warn: missing" if r.missing else "warn"
    return "missing" if r.missing else "FAIL"

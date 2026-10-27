"""tracemodel: labeled financial models with checked, sourced values.

Build a model in four steps, in order:

    model = tm.Model()
    model.add_dimension(...)          # 1. dimensions and coords
    model.add_item(...)               # 2. items scaffold
    model.add_check(...)              # 3. data quality
    model.set_value(...)              # 4. values, addressed by coords
    model.check()
"""

import json
from pathlib import Path

from . import ops
from .diff import Diff, diff
from .dimensions import Dimension, Hierarchy
from .errors import (
    DefinitionError,
    DimMismatch,
    InvalidValue,
    MissingValue,
    TraceModelError,
    UnknownCoord,
    UnknownItem,
)
from .formula import Formula
from .log import LogError, StaleRef, TornLog
from .model import ANY, Check, Item, Model, filter_by_attrs
from .ops import Op
from .periods import Period, PeriodRange, period_range
from .repo import CommitInfo, Conflict, Conflicted, Rebased, Repo, Version
from .table import Table
from .values import NIL, Entry, Report, Trace


def load(path: str | Path, allow_code: bool | None = None, formulas: str = "on") -> Model:
    """A model from a definition file written by `model.save(path)`.

    Formulas are checked against the formula allow-list and compiled with RestrictedPython;
    nothing else runs. formulas="off" keeps them as text without compiling them. `allow_code`
    is ignored.
    """
    if allow_code is not None:
        import warnings

        warnings.warn("allow_code is ignored: formulas are checked and compiled safely, and never need it",
                      DeprecationWarning, stacklevel=2)
    return Model.from_dict(json.loads(Path(path).read_text()), formulas=formulas)


__all__ = [
    "ANY",
    "NIL",
    "Check",
    "CommitInfo",
    "Conflict",
    "Conflicted",
    "DefinitionError",
    "Diff",
    "DimMismatch",
    "Dimension",
    "Entry",
    "Formula",
    "Hierarchy",
    "InvalidValue",
    "Item",
    "LogError",
    "MissingValue",
    "Model",
    "Op",
    "Period",
    "PeriodRange",
    "Rebased",
    "Repo",
    "Report",
    "StaleRef",
    "Table",
    "TornLog",
    "Trace",
    "TraceModelError",
    "UnknownCoord",
    "UnknownItem",
    "Version",
    "diff",
    "filter_by_attrs",
    "load",
    "ops",
    "period_range",
]

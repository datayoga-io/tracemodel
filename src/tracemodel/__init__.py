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
from .model import ANY, Check, Item, Model, filter_by_attrs
from .table import Table
from .values import NIL, Entry, Report


def load(path: str | Path, allow_code: bool = False) -> Model:
    """A model from a definition file written by `model.save(path)`.

    Formulas saved as Python functions are code; loading them needs allow_code=True.
    """
    return Model.from_dict(json.loads(Path(path).read_text()), allow_code=allow_code)


__all__ = [
    "ANY",
    "NIL",
    "Check",
    "DefinitionError",
    "DimMismatch",
    "Dimension",
    "Entry",
    "Hierarchy",
    "InvalidValue",
    "Item",
    "MissingValue",
    "Model",
    "Report",
    "Table",
    "TraceModelError",
    "UnknownCoord",
    "UnknownItem",
    "filter_by_attrs",
    "load",
]

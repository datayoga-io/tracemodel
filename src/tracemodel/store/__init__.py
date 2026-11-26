"""Where models are kept between runs, behind one small interface.

A `Store` holds any number of named models and gives one back as a `Repo`. Everything above it (the studio, an agent,
a script) asks for `store.repo(name)` and never sees how the models are kept, so a new backend is one class.

A backend says how its models are kept. It does four reads and one write:

    models()                       which models it holds, and how big each is
    definition(name)               the model's dimensions, items and checks, as `Model.to_dict()` writes them
    cells(name, commit=None)       the latest value of each cell, as `to_jsonl` rows; or the cells of one commit
    commits(name)                  the model's commits, oldest first, as (id, message)
    import_model(name, ...)        store a model

`Store` builds `repo()` and `import_run()` from those, so a backend does not repeat them.

The DuckDB backend is `tracemodel.store.duck.DuckStore` (install with `pytracemodel[duckdb]`). It is not imported
here, so this module needs no extra package.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Self

from .. import ops as tm_ops
from ..repo import Repo

__all__ = ["Store", "UnknownModel"]


class UnknownModel(KeyError):
    """No model of that name in the store."""


class Store(ABC):
    """A place that keeps models. Subclasses fill in the reads and the write; the rest is built from them."""

    # == what a backend provides ==
    @abstractmethod
    def models(self) -> list[dict[str, Any]]:
        """The models held, in a stable order: `{name, title, dimensions, items, checks, cells}` for each."""

    @abstractmethod
    def definition(self, name: str) -> dict[str, Any]:
        """The model's definition (`Model.to_dict()` data). Raises `UnknownModel`."""

    @abstractmethod
    def cells(self, name: str, commit: int | None = None) -> list[dict[str, Any]]:
        """The latest value of each cell, as the rows `Model.to_jsonl` writes; or only the cells entered in `commit`."""

    @abstractmethod
    def commits(self, name: str) -> list[tuple[int, str]]:
        """The model's commits, oldest first, as `(id, message)`. A store that kept none has one commit, `(1, "")`."""

    @abstractmethod
    def import_model(self, name: str, definition: dict[str, Any], values: Iterable[dict[str, Any]],
                     title: str | None = None, commits: list[str] | None = None) -> int:
        """Store a model under `name`, replacing one of that name. `values` are `to_jsonl` rows; a row may say which
        commit it belongs to (`"commit": 1` is the first). Returns the number of cells."""

    def close(self) -> None:
        """Release what the store holds open. Does nothing unless a backend has something to release."""

    # == built from those ==
    def repo(self, name: str, author: str = "store", message: str | None = None) -> Repo:
        """The model as a repo in memory, with one commit for each commit the store holds."""
        definition = self.definition(name)
        repo = Repo.memory(author)
        steps = [tm_ops.Op("dimension.add", d["name"], None, d) for d in definition["dimensions"]]
        steps += [tm_ops.Op("item.add", i["name"], None, i) for i in definition["items"]]
        steps += [tm_ops.Op("check.set", c["id"], None, c) for c in definition["checks"]]
        for commit, text in self.commits(name):
            steps += [tm_ops.Op("value.set", {"item": r["line_item"], "coords": r["coords"]}, None,
                                {k: v for k, v in r.items() if k not in ("line_item", "coords")})
                      for r in self.cells(name, commit)]
            tm_ops.replay(repo.model, steps)
            repo.commit(message or text or f"Loaded {name}")
            steps = []
        return repo

    def import_run(self, directory: str | Path, name: str | None = None) -> str:
        """Store the model in a folder holding `model.json` and `values.jsonl`; the name is the folder's."""
        d = Path(directory)
        definition = json.loads((d / "model.json").read_text())
        with open(d / "values.jsonl") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        name = name or d.resolve().name
        self.import_model(name, definition, rows)
        return name

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

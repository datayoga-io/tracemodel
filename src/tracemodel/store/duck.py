"""Models kept in a DuckDB file: the first `Store` backend.

A file holds any number of models. Each is stored as its parts, in the shapes the definition and values files use:

    model(name, title, position)                 one row per model
    dimension(model, position, name, def)        one row per dimension; `def` is a VARIANT (labels, hierarchy, attrs)
    item(model, position, name, def)             one row per item (dimensions, type, pointer target, formula text, attrs)
    check_def(model, position, id, def)          one row per check
    commit_info(model, commit_id, message)       the commits, oldest first; a file without them is one commit
    cell(model, item, coords, value, source, commit_id)
                                                 one row per value ever entered, append-only. `coords` and `source` are
                                                 VARIANTs, so a model change adds rows and never changes a table.
                                                 A missing cell has no row; a cell entered as nil has a row whose value
                                                 is SQL NULL; a cell formula is a VARIANT object ({"formula": ...}).

A cell's value is its row with the highest commit_id, so an update is an insert and nothing is overwritten. Nothing here
knows what a model is about. `repo(name)` comes from `Store`, so the grid, the cell detail and the checks work on it
like on any other repo.

Needs DuckDB 2.0 or later (the VARIANT type): `pip install pytracemodel[duckdb]`.
"""

from __future__ import annotations

import json
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import Store, UnknownModel

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS model(name VARCHAR PRIMARY KEY, title VARCHAR, position INT)",
    "CREATE TABLE IF NOT EXISTS dimension(model VARCHAR, position INT, name VARCHAR, def VARIANT)",
    "CREATE TABLE IF NOT EXISTS item(model VARCHAR, position INT, name VARCHAR, def VARIANT)",
    "CREATE TABLE IF NOT EXISTS check_def(model VARCHAR, position INT, id VARCHAR, def VARIANT)",
    "CREATE TABLE IF NOT EXISTS cell(model VARCHAR, item VARCHAR, coords VARIANT, value VARIANT, source VARIANT, commit_id INT)",
    "CREATE TABLE IF NOT EXISTS commit_info(model VARCHAR, commit_id INT, message VARCHAR)",
)
FORMAT = "tracemodel/2"


class DuckStore(Store):
    """A DuckDB file of models. Opened read-only unless `write=True`, which makes the file if it is missing."""

    def __init__(self, path: str | Path, *, write: bool = False):
        import duckdb

        self.path = Path(path)
        if not write and not self.path.is_file():
            raise FileNotFoundError(f"no DuckDB file at {self.path}")
        self._duckdb = duckdb
        self.con = duckdb.connect(str(self.path), read_only=not write,
                                  **({} if write else {"config": {"enable_external_access": False}}))
        if write:
            for sql in SCHEMA:
                self.con.execute(sql)
        tables = {r[0] for r in self.con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        if not {"model", "dimension", "item", "check_def", "cell"} <= tables:
            raise ValueError(f"{self.path.name} is not a tracemodel DuckDB file (no model tables)")
        self._lock = threading.Lock()
        self._commits = "commit_info" in tables   # files made before commits were kept have no such table

    def close(self) -> None:
        self.con.close()

    # == writing ==
    def import_model(self, name: str, definition: dict[str, Any], values: Iterable[dict[str, Any]], title: str | None = None,
                     commits: list[str] | None = None) -> int:
        """Store a model (`Model.to_dict()` data and `to_jsonl` rows) under `name`, replacing one of that name.
        Returns the number of cells.

        `commits` are the messages of its commits, oldest first. A value row may say which one it belongs to
        (`"commit": 1` is the first); a cell entered again in a later commit keeps its earlier values as history."""
        if definition.get("format") != FORMAT:
            raise ValueError(f"not a tracemodel definition (format {definition.get('format')!r}, expected {FORMAT!r})")
        with self._lock:
            c = self.con
            c.execute("BEGIN")
            try:
                kept = c.execute("SELECT position FROM model WHERE name = ?", [name]).fetchall()   # a re-import keeps its place
                for t, col in (("dimension", "model"), ("item", "model"), ("check_def", "model"), ("cell", "model"),
                               ("commit_info", "model"), ("model", "name")):
                    c.execute(f"DELETE FROM {t} WHERE {col} = ?", [name])
                position = kept[0][0] if kept else c.execute("SELECT coalesce(max(position), 0) + 1 FROM model").fetchone()[0]
                c.execute("INSERT INTO model VALUES (?, ?, ?)", [name, title or name, position])
                for table, key, rows in (("dimension", "name", definition["dimensions"]), ("item", "name", definition["items"]),
                                         ("check_def", "id", definition["checks"])):
                    self._bulk(table, [(name, i, r[key], json.dumps(r)) for i, r in enumerate(rows)])
                n = self._cells(name, values)
                for i, message in enumerate(commits or [], 1):
                    c.execute("INSERT INTO commit_info VALUES (?, ?, ?)", [name, i, message])
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        return n

    def _bulk(self, table: str, rows: list[tuple]) -> None:
        """Rows with a VARIANT in the last column, through a temporary JSON lines file: a row-by-row insert converts
        each value separately and is two orders of magnitude slower."""
        if not rows:
            return
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for r in rows:
                f.write(json.dumps({"a": r[0], "b": r[1], "c": r[2], "d": json.loads(r[3])}) + "\n")
        try:
            self.con.execute(f"INSERT INTO {table} SELECT t.a, t.b, t.c, t.d::VARIANT FROM read_json('{f.name}', "
                             "format='newline_delimited', columns={'a': 'VARCHAR', 'b': 'INT', 'c': 'VARCHAR', 'd': 'JSON'}) t")
        finally:
            Path(f.name).unlink(missing_ok=True)

    def _cells(self, name: str, values: Iterable[dict[str, Any]]) -> int:
        n = 0
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for row in values:
                data = {k: v for k, v in row.items() if k not in ("line_item", "coords", "source", "overrides_formula")}
                stored = data if "formula" in data else data["value"]   # a number, text, nil (None) or, for a formula, its data
                f.write(json.dumps({"item": row["line_item"], "coords": row["coords"], "value": stored, "source": row.get("source"),
                                    "commit": row.get("commit", 1)}) + "\n")
                n += 1
        try:
            if n:
                self.con.execute(
                    "INSERT INTO cell SELECT ?, t.item, t.coords::VARIANT, CASE WHEN json_type(t.value) = 'NULL' THEN NULL ELSE t.value::VARIANT END, "
                    f"t.source::VARIANT, t.commit FROM read_json('{f.name}', format='newline_delimited', "
                    "columns={'item': 'VARCHAR', 'coords': 'JSON', 'value': 'JSON', 'source': 'JSON', 'commit': 'INT'}) t", [name])
        finally:
            Path(f.name).unlink(missing_ok=True)
        return n

    # == reading ==
    def models(self) -> list[dict[str, Any]]:
        """The models in the file: name, title and how big each is."""
        rows = self.con.cursor().execute(
            "SELECT m.name, m.title, "
            "(SELECT count(*) FROM dimension d WHERE d.model = m.name), (SELECT count(*) FROM item i WHERE i.model = m.name), "
            "(SELECT count(*) FROM check_def c WHERE c.model = m.name), (SELECT count(*) FROM cell c WHERE c.model = m.name) "
            "FROM model m ORDER BY m.position, m.name").fetchall()
        return [{"name": n, "title": t, "dimensions": d, "items": i, "checks": c, "cells": v} for n, t, d, i, c, v in rows]

    def definition(self, name: str) -> dict[str, Any]:
        cur = self.con.cursor()
        if not cur.execute("SELECT 1 FROM model WHERE name = ?", [name]).fetchall():
            raise UnknownModel(name)

        def parts(table: str) -> list[Any]:
            return [json.loads(r[0]) for r in cur.execute(f"SELECT to_json(def) FROM {table} WHERE model = ? ORDER BY position", [name]).fetchall()]

        return {"format": FORMAT, "dimensions": parts("dimension"), "items": parts("item"), "checks": parts("check_def")}

    def cells(self, name: str, commit: int | None = None) -> list[dict[str, Any]]:
        """The latest value of each cell, as the rows of a values file; or the cells entered in one commit."""
        where, params = ("AND commit_id = ?", [name, commit]) if commit is not None else ("", [name])
        rows = self.con.cursor().execute(
            f"SELECT item, to_json(coords), to_json(value), to_json(source), value IS NULL FROM cell WHERE model = ? {where} "
            "QUALIFY row_number() OVER (PARTITION BY item, CAST(coords AS VARCHAR) ORDER BY commit_id DESC) = 1", params).fetchall()
        out = []
        for item, coords, value, source, nil in rows:
            v = None if nil else json.loads(value)
            row = {"line_item": item, "coords": json.loads(coords), "source": json.loads(source) if source else None}
            out.append(row | (v if isinstance(v, dict) else {"value": v}))
        return out

    def commits(self, name: str) -> list[tuple[int, str]]:
        """The model's commits, oldest first, as (id, message). A commit that only cells mention has no message; a file
        that kept none is one commit."""
        cur = self.con.cursor()
        said = dict(cur.execute("SELECT commit_id, message FROM commit_info WHERE model = ?", [name]).fetchall()) if self._commits else {}
        for (i,) in cur.execute("SELECT DISTINCT commit_id FROM cell WHERE model = ?", [name]).fetchall():
            said.setdefault(i, "")
        return sorted(said.items()) or [(1, "")]

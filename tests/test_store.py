"""The store interface, and the DuckDB backend: models kept in a file and read back as repos."""

import json
from datetime import date

import pytest

duckdb = pytest.importorskip("duckdb")

import tracemodel as tm
from tracemodel.store import Store, UnknownModel
from tracemodel.store.duck import DuckStore


def build():
    """A small model with a formula and a check, made up for these tests."""
    m = tm.Model()
    m.add_dimension("t", ["Q1", "Q2", "Q3"])
    m.add_item("sales", ("t",), description="Sales")
    m.add_item("cost", ("t",))
    m.add_item("margin", ("t",), formula=lambda x: x["sales"] - x["cost"])
    m.add_check("margin_positive", lambda x: x["margin"] > 0, coords={"t": "Q1"}, description="margin stays above zero")
    for t, sales, cost in (("Q1", 100, 60), ("Q2", 120, 70), ("Q3", 90, 95)):
        m.set_value("sales", {"t": t}, sales, {"document": "Report", "page": 3})
        m.set_value("cost", {"t": t}, cost, {"document": "Report"})
    return m


def parts(model, tmp_path, name="m"):
    """A model as the two files `tm` writes: its definition, and one row per value."""
    model.to_jsonl(tmp_path / f"{name}.jsonl")
    rows = [json.loads(line) for line in (tmp_path / f"{name}.jsonl").read_text().splitlines()]
    return model.to_dict(), rows


def values_of(model, tmp_path):
    """The rows `to_jsonl` writes: one per entered cell, a nil as value null. A cell never entered has none."""
    model.to_jsonl(tmp_path / "v.jsonl")
    return {(r["line_item"], r["coords"]["t"]): r["value"] for r in (json.loads(x) for x in (tmp_path / "v.jsonl").read_text().splitlines())}


def sort_rows(rows):
    return sorted(rows, key=lambda r: json.dumps([r["line_item"], r["coords"]]))


@pytest.fixture
def store(tmp_path):
    s = DuckStore(tmp_path / "models.duckdb", write=True)
    yield s
    s.close()


# == a model goes in and comes out as the same model ==
def test_a_model_comes_back_the_same(store, tmp_path):
    original = build()
    definition, rows = parts(original, tmp_path)
    assert store.import_model("built", definition, rows) == len(rows)
    back = store.repo("built").model
    assert back.to_dict() == definition                      # dimensions, items with formulas and pointers, checks
    back.to_jsonl(tmp_path / "back.jsonl")
    again = [json.loads(line) for line in (tmp_path / "back.jsonl").read_text().splitlines()]
    assert sort_rows(again) == sort_rows(rows)               # every entered value, with its source


def test_calculated_cells_follow_through_the_store(store, tmp_path):
    original = build()
    store.import_model("built", *parts(original, tmp_path))
    back = store.repo("built").model
    assert back.get("margin", t="Q2") == original.get("margin", t="Q2") == 50
    assert [f.check.id for f in back.check().failures] == [f.check.id for f in original.check().failures]


def test_nil_missing_and_every_kind_of_value(store, tmp_path):
    m = tm.Model()
    m.add_dimension("t", ["A", "B", "C", "D"])
    for name, dtype in (("n", float), ("count", int), ("s", str), ("d", "date")):
        m.add_item(name, ("t",), dtype=dtype)
    src = {"document": "Doc"}
    m.set_value("n", {"t": "A"}, 1.5, src)
    m.set_value("n", {"t": "B"}, tm.NIL, src)            # entered as nil: a value that says nothing
    m.set_value("count", {"t": "A"}, 4_000_000_000_000, src)
    m.set_value("s", {"t": "A"}, "text", src)
    m.set_value("d", {"t": "A"}, date(2026, 1, 31), src)
    store.import_model("kinds", *parts(m, tmp_path))
    back = store.repo("kinds").model
    got = values_of(back, tmp_path)
    assert got[("n", "A")] == 1.5 and got[("n", "B")] is None and ("n", "C") not in got   # nil is kept; never entered stays missing
    assert back.get("count", t="A") == 4_000_000_000_000 and back.get("s", t="A") == "text"
    assert back.get("d", t="A") == date(2026, 1, 31)


def test_a_missing_cell_is_not_a_nil_cell(store, tmp_path):
    m = tm.Model()
    m.add_dimension("t", ["A", "B"])
    m.add_item("n", ("t",))
    m.set_value("n", {"t": "A"}, tm.NIL, {"document": "Doc"})
    store.import_model("m", *parts(m, tmp_path))
    got = values_of(store.repo("m").model, tmp_path)
    assert got == {("n", "A"): None}                      # A was entered as nil; B was never entered, and has no cell


# == the file ==
def test_a_later_commit_wins_and_the_earlier_value_stays(store, tmp_path):
    m = tm.Model()
    m.add_dimension("t", ["A"])
    m.add_item("n", ("t",))
    m.set_value("n", {"t": "A"}, 1, {"document": "Doc"})
    store.import_model("m", *parts(m, tmp_path))
    store.con.execute("INSERT INTO cell VALUES ('m', 'n', '{\"t\":\"A\"}'::JSON::VARIANT, '2'::JSON::VARIANT, "
                      "'{\"document\":\"Doc\"}'::JSON::VARIANT, 2)")
    assert store.repo("m").model.get("n", t="A") == 2
    assert store.con.execute("SELECT count(*) FROM cell").fetchone()[0] == 2     # nothing was overwritten


def test_models_are_listed_and_reimporting_replaces(store, tmp_path):
    a = build()
    store.import_model("first", *parts(a, tmp_path, "a"), title="First model")
    small = tm.Model()
    small.add_dimension("t", ["A"])
    small.add_item("n", ("t",))
    small.set_value("n", {"t": "A"}, 1, {"document": "Doc"})
    store.import_model("second", *parts(small, tmp_path, "b"))
    listed = store.models()
    assert [m["name"] for m in listed] == ["first", "second"] and listed[0]["title"] == "First model"
    assert listed[1] == {"name": "second", "title": "second", "dimensions": 1, "items": 1, "checks": 0, "cells": 1}
    store.import_model("first", *parts(small, tmp_path, "c"))
    assert [(m["name"], m["cells"]) for m in store.models()] == [("first", 1), ("second", 1)]
    assert store.con.execute("SELECT count(*) FROM dimension WHERE model = 'first'").fetchone()[0] == 1


def test_a_model_that_is_not_there(store):
    with pytest.raises(UnknownModel):
        store.repo("nope")


def test_a_folder_of_a_run_is_imported_under_its_name(store, tmp_path):
    run = tmp_path / "run42"
    run.mkdir()
    definition, rows = parts(build(), tmp_path)
    (run / "model.json").write_text(json.dumps(definition))
    (run / "values.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert store.import_run(run) == "run42"
    assert store.models()[0]["cells"] == len(rows)


def test_a_file_opened_to_read_cannot_be_changed(store, tmp_path):
    path = store.path
    store.import_model("m", *parts(build(), tmp_path))
    store.close()
    reader = DuckStore(path)
    with pytest.raises(duckdb.Error):
        reader.con.execute("DELETE FROM cell")
    assert reader.models()[0]["name"] == "m"
    reader.close()


def test_other_files_are_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        DuckStore(tmp_path / "missing.duckdb")
    other = tmp_path / "other.duckdb"
    c = duckdb.connect(str(other))
    c.execute("CREATE TABLE x(a INT)")
    c.close()
    with pytest.raises(ValueError, match="not a tracemodel DuckDB file"):
        DuckStore(other)


def test_a_definition_in_another_format_is_refused(store):
    with pytest.raises(ValueError, match="not a tracemodel definition"):
        store.import_model("m", {"format": "other", "dimensions": [], "items": [], "checks": []}, [])


# == commits ==
def test_each_commit_is_kept_and_a_restated_cell_has_history(store, tmp_path):
    m = tm.Model()
    m.add_dimension("t", ["A", "B"])
    m.add_item("n", ("t",))
    m.set_value("n", {"t": "A"}, 1, {"document": "r1"})
    m.set_value("n", {"t": "B"}, 5, {"document": "r1"})
    definition, rows = parts(m, tmp_path)
    for r in rows:
        r["commit"] = 1
    rows.append({"line_item": "n", "coords": {"t": "A"}, "value": 2, "source": {"document": "r2"}, "commit": 2})   # restated by the next report
    store.import_model("m", definition, rows, commits=["Report 1", "Report 2"])
    assert store.commits("m") == [(1, "Report 1"), (2, "Report 2")]
    repo = store.repo("m")
    assert [c.message for c in repo.log()] == ["Report 2", "Report 1"]
    assert repo.model.get("n", t="A") == 2 and repo.model.get("n", t="B") == 5            # the later report wins
    assert sorted(v.value["value"] for v in repo.history("n", t="A")) == [1, 2]            # and the earlier value is kept
    assert len(store.cells("m")) == 2 and len(store.cells("m", commit=1)) == 2 and len(store.cells("m", commit=2)) == 1


def test_a_file_without_commit_info_is_one_commit(store, tmp_path):
    store.import_model("m", *parts(build(), tmp_path))
    store.con.execute("DROP TABLE commit_info")
    store.close()
    old = DuckStore(store.path)
    assert old.commits("m") == [(1, "")] and len(old.repo("m").log()) == 1
    old.close()


# == the interface ==
class ListStore(Store):
    """The smallest backend: models kept in a dict. Anything that fills in the five calls gets `repo` and `import_run`."""

    def __init__(self):
        self.kept = {}

    def models(self):
        return [{"name": n, "title": n, "dimensions": len(d["dimensions"]), "items": len(d["items"]),
                 "checks": len(d["checks"]), "cells": len(rows)} for n, (d, rows) in self.kept.items()]

    def definition(self, name):
        if name not in self.kept:
            raise UnknownModel(name)
        return self.kept[name][0]

    def cells(self, name, commit=None):
        return [r for r in self.kept[name][1] if commit is None or r.get("commit", 1) == commit]

    def commits(self, name):
        return [(1, "")]

    def import_model(self, name, definition, values, title=None, commits=None):
        self.kept[name] = (definition, list(values))
        return len(self.kept[name][1])


def test_another_backend_gets_repo_and_import_run_for_free(tmp_path):
    run = tmp_path / "run7"
    run.mkdir()
    definition, rows = parts(build(), tmp_path)
    (run / "model.json").write_text(json.dumps(definition))
    (run / "values.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    with ListStore() as other:
        assert other.import_run(run) == "run7"
        repo = other.repo("run7")
        assert repo.model.get("margin", t="Q1") == 40 and [c.message for c in repo.log()] == ["Loaded run7"]
        with pytest.raises(UnknownModel):
            other.repo("nope")


def test_the_duckdb_store_is_a_store(store):
    assert isinstance(store, Store)

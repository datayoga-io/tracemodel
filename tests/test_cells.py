"""model.cells: many cells at once, one row each."""

import pytest

import tracemodel as tm

SRC = {"document": "Plan", "page": 3}


@pytest.fixture
def m():
    m = tm.Model()
    m.add_dimension("use", ["homes", "offices"])
    m.add_dimension("period", ["P1", "P2"])
    m.add_item("area", ("use",))
    m.add_item("price", ("use", "period"))
    m.add_item("sales", ("use", "period"), formula=lambda x: x["area"] * x["price"])
    m.add_item("fee", ())
    m.set_value("area", {"use": "offices"}, 10.0, SRC)
    m.set_value("area", {"use": "homes"}, 20.0, SRC)
    m.set_value("price", {"use": "offices", "period": "P1"}, 3.0, SRC)
    m.set_value("fee", {}, 0.5)
    return m


def rows(table):
    return {(r["item"], r["coords"]): r for r in table.to_records()}


def test_coords_read_every_item_that_has_the_dim_across_its_other_dims(m):
    t = m.cells(use="offices")
    assert t.columns == ["item", "coords", "value", "kind", "source"]
    got = rows(t)
    assert set(got) == {("area", "[use=offices]"), ("price", "[use=offices, period=P1]"),
                        ("price", "[use=offices, period=P2]"), ("sales", "[use=offices, period=P1]"),
                        ("sales", "[use=offices, period=P2]")}  # fee has no use
    assert got["area", "[use=offices]"]["value"] == 10.0 and got["area", "[use=offices]"]["kind"] == "entered"
    assert got["area", "[use=offices]"]["source"] == "Plan, p. 3"
    assert got["sales", "[use=offices, period=P1]"]["value"] == 30.0
    assert got["sales", "[use=offices, period=P1]"]["kind"] == "calculated"


def test_a_cell_without_a_value_is_missing_and_does_not_stop_the_rest(m):
    got = rows(m.cells(use="offices"))
    assert got["price", "[use=offices, period=P2]"]["value"] is None
    assert got["price", "[use=offices, period=P2]"]["kind"] == "missing"
    assert got["sales", "[use=offices, period=P2]"]["kind"] == "missing"  # its formula reads that price


def test_named_items_read_in_full_and_scalars_are_allowed(m):
    t = m.cells(["fee", "area"])
    assert [(r["item"], r["coords"]) for r in t.to_records()] == [
        ("fee", ""), ("area", "[use=homes]"), ("area", "[use=offices]")]
    assert t.to_records()[0]["value"] == 0.5


def test_named_items_at_coords_and_in_the_order_given(m):
    t = m.cells(["sales", "price"], use="homes", period="P1")
    assert [r[:2] for r in t.rows] == [["sales", "[use=homes, period=P1]"], ["price", "[use=homes, period=P1]"]]


def test_no_arguments_reads_every_cell(m):
    assert len(m.cells()) == 2 + 4 + 4 + 1


def test_a_repeated_item_is_read_once(m):
    assert len(m.cells(["fee", "fee"])) == 1


def test_a_nil_cell_reads_as_the_cell_does(m):
    m.set_value("price", {"use": "homes", "period": "P1"}, tm.NIL, SRC)
    got = rows(m.cells(["price"], use="homes", period="P1"))
    assert got["price", "[use=homes, period=P1]"]["value"] == m.get("price", use="homes", period="P1") == 0


@pytest.mark.parametrize("args,kwargs,error,words", [
    ((), {"nope": "x"}, tm.DimMismatch, "no dimension 'nope'"),
    ((), {"use": "mall"}, tm.UnknownCoord, "mall"),
    ((["nope"],), {}, tm.UnknownItem, "nope"),
    ((["fee"],), {"use": "homes"}, tm.DimMismatch, "fee has no dim 'use'"),
])
def test_bad_arguments_name_what_is_wrong(m, args, kwargs, error, words):
    with pytest.raises(error, match=words):
        m.cells(*args, **kwargs)


def test_a_dimension_named_items_is_still_a_coord():
    m = tm.Model()
    m.add_dimension("items", ["a", "b"])
    m.add_item("x", ("items",))
    m.set_value("x", {"items": "a"}, 1.0)
    assert [r["coords"] for r in m.cells(items="a").to_records()] == ["[items=a]"]


def test_it_reads_and_changes_nothing(m):
    before = m.to_dict()
    m.cells(use="offices")
    assert m.to_dict() == before

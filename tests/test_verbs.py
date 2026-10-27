"""The verbs that undo the others: remove_item, set_formula, update_item, remove_check,
clear_value and remove_coords. Each refuses, with nothing changed, while something reads what
it would remove."""

import pytest

import tracemodel as tm

SRC = {"document": "Contract register", "url": "https://example.com/register.pdf", "section": "Rates"}
CONTRACTS = ["C-101", "C-104", "P-301"]


@pytest.fixture
def m():
    m = tm.Model()
    m.add_dimension("contract", CONTRACTS)
    m.add_dimension("month", tm.period_range("2026-01", periods=3, freq="M"))
    m.add_item("rate", ("contract",))
    m.add_item("units", ("contract",))
    m.add_item("fee", ("contract",), formula=lambda x: x["rate"] * x["units"])
    m.add_check("RATE-POSITIVE", coords={"contract": tm.ANY}, expect=lambda x: x["rate"] > 0)
    with m.transaction():
        for c in CONTRACTS:
            m.set_value("rate", {"contract": c}, 10.0, SRC)
            m.set_value("units", {"contract": c}, 2.0, SRC)
    return m


def _state(m):
    return m.to_dict(), [(e.item, e.coords, e.value) for e in m]


# == remove_item ==============================================================
def test_remove_item_removes_its_values(m):
    m.remove_item("fee")
    m.remove_item("units")
    assert list(m.items) == ["rate"] and len(m) == 3


@pytest.mark.parametrize("name, reader", [("units", "fee"), ("rate", "x\\['rate'\\]")])
def test_remove_item_refuses_while_something_reads_it(m, name, reader):
    before = _state(m)
    with pytest.raises(tm.DefinitionError, match=f"can't remove item '{name}'.*{reader}"):
        m.remove_item(name)
    assert _state(m) == before  # the values it cleared are back


def test_remove_item_read_by_a_cell_formula(m):
    m.remove_item("fee")
    m.add_item("bonus", ())
    m.set_value("rate", {"contract": "C-104"}, lambda x: x["bonus"] * 2, SRC)
    with pytest.raises(tm.DefinitionError, match="can't remove item 'bonus'"):
        m.remove_item("bonus")


def test_remove_items_that_read_each_other_in_one_transaction(m):
    with m.transaction():
        m.remove_item("units")  # fee still reads it here; checked at commit
        m.remove_item("fee")
    assert list(m.items) == ["rate"]


def test_a_transaction_that_leaves_a_reader_rolls_back(m):
    before = _state(m)
    with pytest.raises(tm.DefinitionError, match="can't remove item 'units'"), m.transaction():
        m.remove_item("units")
    assert _state(m) == before


# == set_formula and update_item =============================================
def test_set_formula_changes_and_removes_a_formula(m):
    m.set_formula("fee", lambda x: x["rate"] * 3)
    assert m.get("fee", contract="C-101") == 30
    m.set_formula("fee", None)
    with pytest.raises(tm.MissingValue):
        m.get("fee", contract="C-101")


def test_set_formula_keeps_values_entered_over_it(m):
    m.set_value("fee", {"contract": "C-104"}, 99.0, SRC)
    m.set_formula("fee", lambda x: x["rate"] * 3)
    assert m.get("fee", contract="C-104") == 99 and m.get("fee", contract="C-101") == 30


@pytest.mark.parametrize("formula, error, match", [
    (lambda x: x["fee"] + 1, tm.DefinitionError, "circular formula"),
    (lambda x: x["nothing"], tm.UnknownItem, "neither an item nor a label"),
])
def test_set_formula_refuses_a_bad_formula(m, formula, error, match):
    m.clear_value("units", {"contract": "C-101"})
    with pytest.raises(error, match=match):
        m.set_formula("units", formula)
    assert m.item("units").formula is None


def test_add_item_points_to_set_formula(m):
    with pytest.raises(tm.DefinitionError, match="set_formula"):
        m.add_item("fee", ("contract",), formula=lambda x: x["rate"])


def test_update_item(m):
    item = m.update_item("fee", attrs={"units": "USD"}, description="Fee earned")
    assert (item.unit_label, item.description, item.formula) == ("USD", "Fee earned", m.item("fee").formula)
    assert m.update_item("fee", description=None).attrs == {"units": "USD"}  # attrs not given: kept
    with pytest.raises(tm.DefinitionError, match="attrs must be a dict"):
        m.update_item("fee", attrs=["USD"])


# == remove_check =============================================================
def test_remove_check(m):
    m.remove_check("RATE-POSITIVE")
    assert m.check().results == []
    with pytest.raises(tm.DefinitionError, match="no check 'RATE-POSITIVE'"):
        m.remove_check("RATE-POSITIVE")


# == clear_value ==============================================================
def test_clear_value_is_not_entered_again(m):
    m.clear_value("rate", {"contract": "C-101"})
    with pytest.raises(tm.MissingValue):
        m.entry("rate", contract="C-101")
    assert m.check().failures[0].at == {"contract": "C-101"}  # missing, not 0
    with pytest.raises(tm.MissingValue, match="no value to clear"):
        m.clear_value("rate", {"contract": "C-101"})


def test_clear_value_gives_the_cell_back_to_the_item_formula(m):
    m.set_value("fee", {"contract": "C-104"}, lambda x: x["rate"] * 5, SRC)
    assert m.get("fee", contract="C-104") == 50
    m.clear_value("fee", {"contract": "C-104"})
    assert m.get("fee", contract="C-104") == 20


def test_clear_value_refuses_to_reveal_a_loop(m):
    m.set_formula("rate", lambda x: x["fee"] / 2)  # every rate is entered: no loop yet
    with pytest.raises(tm.DefinitionError, match="circular formula"):
        m.clear_value("rate", {"contract": "C-101"})
    assert m.entry("rate", contract="C-101").value == 10


# == remove_coords ============================================================
def test_remove_coords_removes_the_labels_and_their_values(m):
    m.remove_coords("contract", ["C-104"])
    assert m.dimensions["contract"].coords == ("C-101", "P-301")
    assert len(m) == 4
    assert [r.at["contract"] for r in m.check().results] == ["C-101", "P-301"]  # tm.ANY shrinks


def test_remove_coords_refuses_while_a_formula_reads_the_label(m):
    m.add_item("c104_fee", (), formula=lambda x: x["fee"].sel(contract="C-104"))
    before = _state(m)
    with pytest.raises(tm.DefinitionError, match="can't remove C-104 from 'contract'"):
        m.remove_coords("contract", "C-104")
    assert _state(m) == before


def test_remove_coords_refuses_while_a_check_lists_the_label(m):
    m.add_check("C104", coords={"contract": ["C-104"]}, expect=lambda x: x["rate"] < 100)
    with pytest.raises(tm.DefinitionError, match="checks C104 list them"):
        m.remove_coords("contract", ["C-104"])


def test_remove_coords_of_a_time_dimension_from_its_end(m):
    with pytest.raises(tm.DefinitionError, match="remove periods from its end"):
        m.remove_coords("month", ["2026-02"])
    dim = m.remove_coords("month", ["2026-03"])
    assert dim.coords == ("2026-01", "2026-02") and dim.period("2026-02").days == 28
    assert m.add_coords("month", tm.period_range("2026-03", periods=1, freq="M")).coords[-1] == "2026-03"


def test_remove_coords_of_a_hierarchy_takes_the_children_or_nothing():
    m = tm.Model()
    m.add_dimension("region", tm.Hierarchy(["/", "/north", "/north/boston", "/south"]))
    with pytest.raises(tm.DefinitionError, match="/north/boston would lose a parent"):
        m.remove_coords("region", ["/north"])
    assert m.remove_coords("region", ["/north", "/north/boston"]).coords == ("/", "/south")


def test_remove_coords_refuses_every_label_and_unknown_labels(m):
    with pytest.raises(tm.DefinitionError, match="remove the dimension instead"):
        m.remove_coords("contract", CONTRACTS)
    with pytest.raises(tm.UnknownCoord):
        m.remove_coords("contract", ["C-999"])

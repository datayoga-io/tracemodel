"""Yes/no items: dtype=bool."""

import pytest

import tracemodel as tm

SRC = {"document": "Term sheet", "section": "Payments"}


@pytest.fixture
def m():
    m = tm.Model()
    m.add_dimension("term", ["T1", "T2", "T3"])
    m.add_item("amount", ("term",))
    m.add_item("firm", ("term",), dtype=bool)
    m.add_item("firm_amount", ("term",), formula=lambda x: x["firm"] * x["amount"])
    m.add_item("label", ("term",), dtype=str, formula=lambda x: "firm" if x["firm"] else "contingent")
    m.add_item("big", ("term",), dtype=bool, formula=lambda x: x["amount"] > 100)
    with m.transaction():
        for t, amount, firm in [("T1", 150, True), ("T2", 80, False), ("T3", 40, True)]:
            m.set_value("amount", {"term": t}, amount, SRC)
            m.set_value("firm", {"term": t}, firm, SRC)
    return m


def test_declared_by_type_or_name():
    m = tm.Model()
    m.add_dimension("term", ["T1"])
    assert m.add_item("a", ("term",), dtype=bool).dtype is bool
    assert m.add_item("b", ("term",), dtype="bool").dtype is bool
    assert "bool" in str(m)


def test_values_and_formulas(m):
    assert m.get("firm", term="T1") is True and m.get("firm", term="T2") is False
    assert m.get("firm_amount", term="T1") == 150 and m.get("firm_amount", term="T2") == 0
    assert m.get("label", term="T1") == "firm" and m.get("label", term="T2") == "contingent"
    assert m.get("big", term="T1") is True and m.get("big", term="T3") is False


@pytest.mark.parametrize("value", [0, 1, 2, -1, 1.0, "yes", "true", ""])
def test_only_true_and_false_are_accepted(m, value):
    with pytest.raises(tm.InvalidValue, match="firm is bool"):
        m.set_value("firm", {"term": "T1"}, value, SRC)


def test_a_bool_is_not_a_number_item_value(m):
    with pytest.raises(tm.InvalidValue, match="must be a number"):
        m.set_value("amount", {"term": "T1"}, True, SRC)


def test_a_formula_must_give_a_bool(m):
    m.add_item("bad", ("term",), dtype=bool, formula=lambda x: x["amount"])
    with pytest.raises(tm.DefinitionError, match="bad.*is bool, but its formula.*gave a number"):
        m.get("bad", term="T1")


def test_a_number_item_does_not_take_a_bool_formula(m):
    m.add_item("worse", ("term",), formula=lambda x: x["amount"] > 100)
    with pytest.raises(tm.DefinitionError, match="gave a bool"):
        m.get("worse", term="T1")


def test_nil_is_not_false(m):
    m.set_value("firm", {"term": "T2"}, tm.NIL, SRC)
    assert m.get("firm", term="T2") is tm.NIL
    m.add_item("known", ("term",), dtype=bool, formula=lambda x: not x["firm"].is_nil())
    assert m.get("known", term="T2") is False and m.get("known", term="T1") is True
    with pytest.raises(tm.DefinitionError, match="is nil.*is_nil"):
        m.get("label", term="T2")


def test_a_check_may_read_a_bool_item(m):
    m.add_check("firm_when_big", lambda x: x["firm"] or x["amount"] < 100, coords={"term": tm.ANY})
    assert m.check().ok
    m.add_check("all_firm", lambda x: x["firm"], coords={"term": tm.ANY})
    assert {f.check.id for f in m.check().failures} == {"all_firm"}


def test_tables_show_yes_and_no(m):
    text = str(m.table("firm"))
    assert "yes" in text and "no" in text and "True" not in text and "False" not in text


def test_saved_as_json_booleans(m, tmp_path):
    m.save(tmp_path / "m.json")
    m.to_jsonl(tmp_path / "m.jsonl")
    text = (tmp_path / "m.json").read_text() + (tmp_path / "m.jsonl").read_text()
    assert '"dtype": "bool"' in text and "true" in text and "false" in text
    again = tm.load(tmp_path / "m.json")
    again.load_jsonl(tmp_path / "m.jsonl")
    assert again.item("firm").dtype is bool
    assert again.get("firm", term="T1") is True and again.get("firm", term="T2") is False


def test_repo_log_round_trip(tmp_path):
    repo = tm.Repo.open(tmp_path / "r", author="t")
    m = repo.model
    m.add_dimension("term", ["T1"])
    m.add_item("firm", ("term",), dtype=bool)
    m.set_value("firm", {"term": "T1"}, True, SRC)
    repo.commit("add flag")
    again = tm.Repo.open(tmp_path / "r", author="t").model
    assert again.item("firm").dtype is bool and again.get("firm", term="T1") is True

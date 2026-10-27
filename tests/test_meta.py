"""What a writer says about a value, beside its source: confidence, reasoning, origin. An
assumption is an ordinary cell entered with confidence "assumed"; what rests on it is found by
following formulas down to the entered cells."""

import json

import pytest

import tracemodel as tm

SRC = {"document": "Architect agreement", "url": "https://example.com/a.pdf", "section": "4.2",
       "page": 3, "sha256": "9f2c..."}
GUESS = {"confidence": "assumed", "reasoning": "the agreement leaves units open; 470 from the feasibility study",
         "origin": "agent:fee-forecast-build"}


@pytest.fixture
def m():
    m = tm.Model()
    m.add_dimension("term", ["T1", "T2"])
    m.add_item("rate", ("term",))
    m.add_item("units", ("term",), dtype=int)
    m.add_item("fee", ("term",), formula=lambda x: x["rate"] * x["units"])
    m.add_item("total", (), formula=lambda x: x["fee"]["T1"] + x["fee"]["T2"])
    with m.transaction():
        m.set_value("rate", {"term": "T1"}, 6_000.0, SRC, meta={"confidence": "stated", "origin": "agent"})
        m.set_value("rate", {"term": "T2"}, 5_500.0, SRC, meta={"confidence": "inferred",
                                                              "reasoning": "tier 2 of the table on p. 3"})
        m.set_value("units", {"term": "T1"}, 470, SRC, meta=GUESS)
        m.set_value("units", {"term": "T2"}, 30, SRC)
    return m


def test_a_value_carries_its_meta(m):
    e = m.entry("units", term="T1")
    assert e.meta == GUESS and e.source["sha256"] == "9f2c..."


def test_a_new_value_never_keeps_the_old_meta(m):
    m.set_value("units", {"term": "T1"}, 480, SRC)
    assert m.entry("units", term="T1").meta == {}


@pytest.mark.parametrize("meta, match", [
    ({"confidence": "sure"}, "confidence must be one of assumed, inferred, stated"),
    ({"why": "because"}, "unknown why"),
    ({"reasoning": "  "}, "reasoning must be text"),
    ("assumed", "meta must be a dict"),
])
def test_meta_is_checked(m, meta, match):
    with pytest.raises(tm.InvalidValue, match=match):
        m.set_value("rate", {"term": "T1"}, 1.0, SRC, meta=meta)
    assert m.get("rate", term="T1") == 6_000


def test_inputs_are_the_entered_cells_a_figure_rests_on(m):
    assert [(e.item, e.coords["term"]) for e in m.inputs("total")] == [
        ("rate", "T1"), ("units", "T1"), ("rate", "T2"), ("units", "T2")]
    assert [e.item for e in m.inputs("rate", term="T1")] == ["rate"]  # an entered cell rests on itself


def test_a_figure_is_as_sure_as_its_weakest_input(m):
    assert m.confidence("total") == "assumed"
    assert m.confidence("fee", term="T2") == "inferred"  # units T2 states nothing: not counted
    assert m.confidence("rate", term="T1") == "stated"
    m.set_value("units", {"term": "T1"}, 470, SRC, meta={"confidence": "stated"})
    assert m.confidence("total") == "inferred"


def test_what_rests_on_an_assumption(m):
    assumed = [e for e in m.inputs("total") if e.meta.get("confidence") == "assumed"]
    assert [(e.item, e.coords) for e in assumed] == [("units", {"term": "T1"})]
    readers = {t.name for top in m.dependents("units", deep=True, term="T1") for t in [top, *top.flatten()]}
    assert readers == {"fee", "total"}


def test_a_cell_formula_is_followed_to_what_it_reads(m):
    m.set_value("fee", {"term": "T2"}, lambda x: x["rate"] * 10, SRC, meta={"confidence": "stated"})
    assert [(e.item, e.coords["term"]) for e in m.inputs("fee", term="T2")] == [("rate", "T2")]


def test_meta_survives_the_values_file(m, tmp_path):
    m.to_jsonl(tmp_path / "v.jsonl")
    rows = [json.loads(line) for line in (tmp_path / "v.jsonl").read_text().splitlines()]
    assert rows[2]["meta"] == GUESS and "meta" not in rows[3]
    again = tm.Model.from_dict(m.to_dict())
    again.load_jsonl(tmp_path / "v.jsonl")
    assert again.entry("units", term="T1").meta == GUESS and again.confidence("total") == "assumed"


def test_entries_table_shows_confidence_when_stated(m):
    table = m.entry("rate")
    assert "confidence" in table.columns and "inferred" in str(table)


def test_excel_shows_meta(m, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    m.to_excel(tmp_path / "m.xlsx")
    wb = openpyxl.load_workbook(tmp_path / "m.xlsx")
    values = wb["Values"]
    header = [c.value for c in values[1]]
    rows = [dict(zip(header, (c.value for c in row))) for row in values.iter_rows(min_row=2)]
    guess = next(r for r in rows if r["item"] == "units" and r["term"] == "T1")
    assert (guess["confidence"], guess["origin"]) == ("assumed", "agent:fee-forecast-build")
    notes = [c.comment.text for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.comment]
    assert any("Confidence: assumed" in n and "Reasoning: the agreement" in n for n in notes)

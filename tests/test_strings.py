"""Text items: dtype=str, after pandas' "string" dtype."""

import pytest

import tracemodel as tm

SRC = {"document": "Contract register", "url": "https://example.com/register.pdf", "section": "Status"}


@pytest.fixture
def m():
    m = tm.Model()
    m.add_dimension("contract", ["C-101", "C-104", "P-301"])
    m.add_item("status", ("contract",), dtype=str)
    m.add_item("vendor", ("contract",), dtype="string")  # pandas' name
    m.add_item("eac", ("contract",))
    m.add_item("committed", ("contract",))
    m.add_item("flag", ("contract",), dtype="str",
               formula=lambda x: "over" if x["eac"] > x["committed"] else "within")
    with m.transaction():
        for c, status, vendor, eac, committed in [("C-101", "signed", "Earthworks Ltd", 567, 480),
                                                  ("C-104", "signed", "Concrete Co", 1150, 1150),
                                                  ("P-301", "not bid", "TBD", 600, 600)]:
            m.set_value("status", {"contract": c}, status, SRC)
            m.set_value("vendor", {"contract": c}, vendor, SRC)
            m.set_value("eac", {"contract": c}, eac, SRC)
            m.set_value("committed", {"contract": c}, committed, SRC)
    return m


def test_text_values(m):
    assert m.get("status", contract="C-101") == "signed"
    assert m.item("vendor").dtype is str
    assert m.get("flag", contract="C-101") == "over" and m.get("flag", contract="C-104") == "within"
    assert "status" in str(m) and "str" in str(m)


@pytest.mark.parametrize("item, value, match", [
    ("status", 5, "must be text"),
    ("status", True, "must be text"),
    ("status", "", "tm.NIL"),
    ("status", "   ", "tm.NIL"),
    ("eac", "567", "must be a number"),
])
def test_values_of_the_wrong_kind(m, item, value, match):
    with pytest.raises(tm.InvalidValue, match=match):
        m.set_value(item, {"contract": "C-101"}, value, SRC)


def test_text_that_looks_like_a_lambda_is_text(m):
    m.set_value("status", {"contract": "C-101"}, "lambda calculus", SRC)
    assert m.get("status", contract="C-101") == "lambda calculus"
    m.set_value("status", {"contract": "C-101"}, lambda x: x["vendor"] + " (signed)", SRC)  # a formula
    assert m.get("status", contract="C-101") == "Earthworks Ltd (signed)"


def test_formula_results_must_match_the_dtype(m):
    m.add_item("bad_text", ("contract",), dtype=str, formula=lambda x: x["eac"] * 2)
    with pytest.raises(tm.DefinitionError, match="is str, but its formula .* gave a number"):
        m.get("bad_text", contract="C-101")
    m.add_item("bad_number", ("contract",), formula=lambda x: x["status"])
    with pytest.raises(tm.DefinitionError, match="is float, but its formula .* gave a str"):
        m.get("bad_number", contract="C-101")


def test_checks_on_text(m):
    m.add_check("SIGNED-OR-NOT-BID", coords={"contract": tm.ANY},
                expect=lambda x: x["status"] in ("signed", "not bid"))
    m.add_check("NAMED-VENDOR", coords={"contract": tm.ANY}, expect=lambda x: x["vendor"] != "TBD")
    report = m.check()
    assert [r.at["contract"] for r in report.failures] == ["P-301"]
    failed = report.failures[0]
    assert (failed.lhs, failed.rhs, failed.diff) == ("TBD", "TBD", None)


def test_text_against_a_number_is_an_error(m):
    m.add_check("BAD", coords={"contract": tm.ANY}, expect=lambda x: x["status"] > 0)
    result = m.check().results[0]
    assert not result.passed and "can't compare a str with a number" in str(result.error)


def test_str_accessor(m):
    m.add_item("short", ("contract",), dtype=str, formula=lambda x: x["vendor"].str.upper())
    m.add_item("is_ltd", ("contract",), dtype=int, formula=lambda x: int(x["vendor"].str.contains("Ltd")))
    m.add_item("chars", ("contract",), dtype=int, formula=lambda x: x["vendor"].str.len())
    assert m.get("short", contract="C-104") == "CONCRETE CO"
    assert m.get("is_ltd", contract="C-101") == 1 and m.get("is_ltd", contract="C-104") == 0
    assert m.get("chars", contract="P-301") == 3
    m.add_item("bad", ("contract",), dtype=str, formula=lambda x: x["eac"].str.lower())
    with pytest.raises(tm.DefinitionError, match=r"\.str is for str items"):
        m.get("bad", contract="C-101")


def test_nil_text(m):
    m.set_value("vendor", {"contract": "P-301"}, tm.NIL, SRC)
    assert m.get("vendor", contract="P-301") is tm.NIL  # not 0: there is no empty text to count
    m.add_check("VENDOR-KNOWN", coords={"contract": tm.ANY},
                expect=lambda x: x["vendor"].is_nil() or x["vendor"].str.len() > 2)
    assert m.check().ok
    m.add_item("label", ("contract",), dtype=str, formula=lambda x: x["vendor"] + "!")
    with pytest.raises(tm.DefinitionError, match="is nil"):
        m.get("label", contract="P-301")
    assert "—" in str(m.table("vendor"))


def test_save_and_load(m, tmp_path):
    m.set_value("status", {"contract": "C-104"}, lambda x: "signed" if x["eac"] > 0 else "void", SRC)
    m.save(tmp_path / "m.json")
    m.to_jsonl(tmp_path / "v.jsonl")
    again = tm.load(tmp_path / "m.json")
    again.load_jsonl(tmp_path / "v.jsonl")
    assert again.item("status").dtype is str
    assert again.get("vendor", contract="C-101") == "Earthworks Ltd"
    assert again.get("flag", contract="C-101") == "over"
    assert again.entry("status", contract="C-104").value.source.startswith("lambda")


def test_table_shows_text(m):
    shown = str(m.table("vendor"))
    assert "Earthworks Ltd" in shown and "TBD" in shown


def test_excel_keeps_text_as_text(m, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    m.set_value("vendor", {"contract": "P-301"}, "=HYPERLINK(\"https://example.com\")", SRC)
    m.to_excel(tmp_path / "m.xlsx")
    wb = openpyxl.load_workbook(tmp_path / "m.xlsx")
    cells = [c for ws in wb.worksheets for row in ws.iter_rows() for c in row
             if isinstance(c.value, str) and "HYPERLINK" in c.value]
    assert cells and all(c.data_type == "s" for c in cells)  # never an Excel formula
    assert any(c.value == "Earthworks Ltd" for ws in wb.worksheets for row in ws.iter_rows() for c in row)

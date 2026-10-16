"""model.to_excel: a small model with every kind of cell, read back with openpyxl."""

import sys
from datetime import date

import pytest

import tracemodel as tm

openpyxl = pytest.importorskip("openpyxl")

SRC = {"document": "Annual report 2025", "url": "https://example.com/ar-2025.pdf", "section": "Note 3"}
BLUE, BLACK = "000000FF", "00000000"
OVERRIDE, FAILING, BANNER = "00FFF2CC", "00F8CBAD", "001F4E78"


def build():
    m = tm.Model()
    m.add_dimension("period", ["FY2024", "FY2025"])
    m.add_dimension("segment", tm.Hierarchy([("/", "Total"), ("/a", "Segment A"), ("/b", "Segment B")]))
    usd = {"units": "USD", "scale": "millions"}
    m.add_item("revenue", ("segment", "period"), attrs=usd, description="Net revenue")
    m.add_item("cost", ("segment", "period"), attrs=usd, formula=lambda x: x["revenue"] * 0.6,
               description="Cost of revenue")
    m.add_item("margin", ("period",), attrs=usd, formula=lambda x: x["revenue"]["/"] - x["cost"]["/"])
    m.add_item("launch", ("period",), dtype="date")
    m.add_item("headcount", ("segment",), dtype=int)
    m.add_item("offices", ("segment",), dtype=int)
    m.add_check("REV-TOTAL", coords={"period": tm.ANY},
                expect=lambda x: x["revenue"]["/"] == x["revenue"]["/a"] + x["revenue"]["/b"],
                description="Total revenue is the sum of the segments")
    with m.transaction():
        for period, total, a, b in [("FY2024", 70, 70, tm.NIL), ("FY2025", 120, 80, 30)]:
            m.set_value("revenue", {"segment": "/", "period": period}, total, SRC)
            m.set_value("revenue", {"segment": "/a", "period": period}, a, SRC)
            m.set_value("revenue", {"segment": "/b", "period": period}, b, SRC)
        m.set_value("cost", {"segment": "/a", "period": "FY2025"}, 50, SRC)  # over the formula
        m.set_value("launch", {"period": "FY2025"}, date(2025, 3, 31), SRC)
        m.set_value("headcount", {"segment": "/a"}, 12, SRC)
    return m


SECTIONS = {"Income statement": ["revenue", "cost", "margin"], "Dates": ["launch"]}


@pytest.fixture
def m():
    return build()


def rows(ws):
    """{first-column text: the row's cells}."""
    return {r[0].value: r for r in ws.iter_rows() if isinstance(r[0].value, str)}


def test_sections_on_one_sheet(m, tmp_path):
    m.to_excel(tmp_path / "m.xlsx", title="Test model", sections=SECTIONS)
    wb = openpyxl.load_workbook(tmp_path / "m.xlsx")
    assert wb.sheetnames == ["Summary", "Statements", "by segment",
                             "Dimensions", "Items", "Checks", "Check results", "Values"]
    ws = wb["Statements"]
    banners = [r[0].value for r in ws.iter_rows() if r[0].fill.fgColor.rgb == BANNER]
    assert banners == ["Income statement", "Dates"]
    assert [b.id for b in ws.row_breaks.brk] == [ws.max_row - 2]  # "Dates" starts a page
    assert ws.print_title_rows == "$4:$4"
    assert [c.value for c in ws[4]][-2:] == ["FY2024", "FY2025"]
    by = rows(ws)
    assert by["Net revenue"][0].comment.text == "item: revenue"
    assert by["Margin"][2].value == 28  # no description: the name in words; values on its title row
    assert wb["Summary"]["A1"].value == "Test model"


def test_cells_show_how_they_were_made(m, tmp_path):
    m.to_excel(tmp_path / "m.xlsx", sections=SECTIONS)
    ws = openpyxl.load_workbook(tmp_path / "m.xlsx")["Statements"]
    revenue, cost = [], []
    block = None
    for r in ws.iter_rows():
        if r[0].value in ("Net revenue", "Cost of revenue", "Margin"):
            block = r[0].value
        elif r[0].value and block == "Net revenue":
            revenue.append(r)
        elif r[0].value and block == "Cost of revenue":
            cost.append(r)
    total, a, b = revenue
    assert total[0].value == "Total" and b[0].value == "Segment B" and b[0].alignment.indent == 3  # depth 1, in a block, in a section
    assert a[3].value == 80 and a[3].font.color.rgb == BLUE and "Annual report 2025, Note 3" in a[3].comment.text
    assert b[2].value == "—"  # nil
    assert total[3].fill.fgColor.rgb == FAILING and b[3].fill.fgColor.rgb == FAILING  # FY2025 fails: 120 != 110
    assert total[2].fill.fgColor.rgb != FAILING  # FY2024 passes
    assert cost[0][2].value == 42 and cost[0][2].font.color.rgb == BLACK
    assert cost[0][2].comment.text == "= x['revenue'] * 0.6"
    assert cost[1][3].value == 50 and cost[1][3].fill.fgColor.rgb == OVERRIDE
    assert "Overrides the formula" in cost[1][3].comment.text
    launch = rows(ws)["Launch"]
    assert launch[2].value is None and launch[3].value.date() == date(2025, 3, 31)
    assert launch[3].number_format == "yyyy-mm-dd"


def test_a_sheet_per_section(m, tmp_path):
    m.to_excel(tmp_path / "m.xlsx", sections=SECTIONS, layout="sheets", page_breaks=False)
    wb = openpyxl.load_workbook(tmp_path / "m.xlsx")
    assert wb.sheetnames[1:3] == ["Income statement", "Dates"]
    assert not any(r[0].fill.fgColor.rgb == BANNER for r in wb["Income statement"].iter_rows())


def test_without_sections_items_group_by_dims(m, tmp_path):
    m.to_excel(tmp_path / "m.xlsx")
    wb = openpyxl.load_workbook(tmp_path / "m.xlsx")
    assert wb.sheetnames[1:3] == ["Model", "by segment"]
    ws = wb["by segment"]  # only hierarchy dims: the items go across
    assert [c.value for c in ws[4]] == ["segment", "Headcount", "Offices"]
    assert ws["B6"].value == 12 and ws["A6"].alignment.indent == 1


def test_tables(m, tmp_path):
    m.to_excel(tmp_path / "m.xlsx", sections=SECTIONS)
    wb = openpyxl.load_workbook(tmp_path / "m.xlsx")
    assert wb["Values"].max_row == len(m) + 1
    results = {r[1].value: r[2].value for r in wb["Check results"].iter_rows(min_row=2)}
    assert results == {"period=FY2024": "pass", "period=FY2025": "FAIL"}
    items = rows(wb["Items"])
    assert items["revenue"][1].value == "Net revenue" and items["revenue"][7].value == "Statements"
    assert items["headcount"][7].value == "by segment"


def test_split_after(m, tmp_path):
    m.to_excel(tmp_path / "m.xlsx", sections=SECTIONS, split_after=("period", "FY2024"))
    ws = openpyxl.load_workbook(tmp_path / "m.xlsx")["Statements"]
    margin = rows(ws)["Margin"]
    assert margin[2].border.right.style == "medium" and margin[3].border.right.style is None


@pytest.mark.parametrize("kwargs, error, match", [
    ({"layout": "tabs"}, tm.DefinitionError, "layout must be one of"),
    ({"columns": "month"}, tm.DefinitionError, "not a dimension"),
    ({"sections": {"A": ["revenue"], "B": ["revenue"]}}, tm.DefinitionError, "in two sections"),
    ({"sections": {"A": []}}, tm.DefinitionError, "has no items"),
    ({"sections": {"A": ["sales"]}}, tm.UnknownItem, "not a declared item"),
])
def test_bad_arguments(m, tmp_path, kwargs, error, match):
    with pytest.raises(error, match=match):
        m.to_excel(tmp_path / "m.xlsx", **kwargs)


def test_needs_openpyxl(m, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    with pytest.raises(ImportError, match=r"pytracemodel\[excel\]"):
        m.to_excel(tmp_path / "m.xlsx")

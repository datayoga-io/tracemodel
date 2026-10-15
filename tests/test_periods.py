"""Time dimensions whose labels know their dates: tm.period_range and x.period."""

import json
from datetime import date

import pytest

import tracemodel as tm

SRC = {"document": "Contract C-201", "url": "https://example.com/c201.pdf", "section": "Fees"}


# -- Period, as pandas' --------------------------------------------------------

@pytest.mark.parametrize("value, freq, label, start, end", [
    ("2026-03", "M", "2026-03", date(2026, 3, 1), date(2026, 3, 31)),
    ("2024-02", "M", "2024-02", date(2024, 2, 1), date(2024, 2, 29)),
    ("2026Q1", "Q", "2026Q1", date(2026, 1, 1), date(2026, 3, 31)),
    ("2026Q1", "Q-SEP", "2026Q1", date(2025, 10, 1), date(2025, 12, 31)),  # fiscal Q1 of FY2026
    ("2025-09", "Q-SEP", "2025Q4", date(2025, 7, 1), date(2025, 9, 30)),
    ("2025", "Y-SEP", "2025", date(2024, 10, 1), date(2025, 9, 30)),       # named by the year it ends in
    (date(2025, 10, 5), "Y-SEP", "2026", date(2025, 10, 1), date(2026, 9, 30)),
    ("2026", "Y", "2026", date(2026, 1, 1), date(2026, 12, 31)),
    ("2026", "A-DEC", "2026", date(2026, 1, 1), date(2026, 12, 31)),       # pandas' older alias
])
def test_period(value, freq, label, start, end):
    p = tm.Period(value, freq)
    assert (p.label, p.start_time, p.end_time) == (label, start, end)
    assert p.days == (end - start).days + 1


def test_period_fields_and_asfreq():
    p = tm.Period("2026-03")
    assert (p.year, p.quarter, p.month, p.freq) == (2026, 1, 3, "M")
    assert p.asfreq("Q-SEP") == tm.Period("2026Q2", "Q-SEP")
    assert p.asfreq("Y-SEP").label == "2026"
    assert tm.Period("2025Q4", "Q-SEP").year == 2025


def test_period_and_dates():
    p = tm.Period("2026-03")
    assert date(2026, 3, 31) in p and date(2026, 4, 1) not in p and "2026-03-15" in p
    assert date(2026, 2, 28) < p and date(2026, 4, 1) > p
    assert date(2026, 3, 10) <= p and date(2026, 3, 10) >= p  # within: not after it, not before it
    assert not date(2026, 3, 10) < p
    assert p.overlap(date(2026, 3, 16)) == 16 / 31                 # from the 16th, open-ended
    assert p.overlap(None, date(2026, 3, 10)) == 10 / 31
    assert p.overlap(date(2026, 4, 1), date(2026, 5, 1)) == 0
    assert p.overlap() == 1
    assert p == "2026-03" and p != "2026-04" and p == tm.Period(date(2026, 3, 9))


@pytest.mark.parametrize("freq", ["W", "M-SEP", "Q-XYZ", "2M", 12])
def test_bad_freq(freq):
    with pytest.raises(tm.DefinitionError, match="freq"):
        tm.Period("2026-01", freq)


@pytest.mark.parametrize("value, freq", [("2026Q1", "M"), ("2026", "Q"), ("March", "M"), ("2026-13", "M")])
def test_bad_period(value, freq):
    with pytest.raises(tm.DefinitionError, match="period"):
        tm.Period(value, freq)


# -- period_range ----------------------------------------------------------------

def test_period_range():
    assert tuple(tm.period_range("2026-11", "2027-02")) == ("2026-11", "2026-12", "2027-01", "2027-02")
    assert tuple(tm.period_range("2026-11", periods=2)) == ("2026-11", "2026-12")
    assert tuple(tm.period_range(end="2026Q1", periods=3, freq="Q-SEP")) == ("2025Q3", "2025Q4", "2026Q1")
    fy = tm.period_range("2024", "2025", freq="Y-SEP", label="FY{year}")
    assert tuple(fy) == ("FY2024", "FY2025")
    assert fy.period("FY2024").start_time == date(2023, 10, 1)
    assert tuple(tm.period_range(date(2026, 1, 15), periods=2, freq="Q")) == ("2026Q1", "2026Q2")


@pytest.mark.parametrize("kwargs, match", [
    ({"start": "2026-01"}, "two of"),
    ({"start": "2026-01", "end": "2026-03", "periods": 3}, "two of"),
    ({"start": "2026-03", "end": "2026-01"}, "before start"),
    ({"start": "2026-01", "periods": 0}, "positive"),
    ({"start": "2026", "periods": 2, "freq": "Y", "label": "FY{yr}"}, "fields"),
    ({"start": "2026-01", "periods": 13, "label": "M{month}"}, "same label"),
])
def test_period_range_errors(kwargs, match):
    with pytest.raises(tm.DefinitionError, match=match):
        tm.period_range(**kwargs)


# -- a time dimension ------------------------------------------------------------

@pytest.fixture
def m():
    """A retainer billed by the day it covers, and a milestone placed in its month."""
    m = tm.Model()
    m.add_dimension("month", tm.period_range("2026-01", "2026-06", freq="M"))
    m.add_item("fee", ())
    m.add_item("start", (), dtype="date")
    m.add_item("end", (), dtype="date")
    m.add_item("milestone_date", (), dtype="date")
    m.add_item("milestone_amount", ())
    m.add_item("retainer", ("month",), formula=lambda x: x["fee"] * x.period.overlap(x["start"], x["end"]))
    m.add_item("milestone", ("month",),
               formula=lambda x: x["milestone_amount"] if x["milestone_date"] in x.period else 0)
    m.add_item("after_start", ("month",), dtype=int, formula=lambda x: int(x["start"] < x.period))
    with m.transaction():
        m.set_value("fee", {}, 31_000, SRC)
        m.set_value("start", {}, "2026-03-16", SRC)
        m.set_value("end", {}, "2026-05-31", SRC)
        m.set_value("milestone_date", {}, "2026-04-30", SRC)
        m.set_value("milestone_amount", {}, 450_000, SRC)
    return m


def column(m, item):
    return [m.get(item, month=mo) for mo in m.dimensions["month"].coords]


def test_formulas_use_the_cells_period(m):
    assert column(m, "retainer") == [0, 0, 16_000, 31_000, 31_000, 0]  # half of March, from the 16th
    assert column(m, "milestone") == [0, 0, 0, 450_000, 0, 0]
    assert column(m, "after_start") == [0, 0, 0, 1, 1, 1]  # months wholly after the start date
    m.set_value("milestone_date", {}, "2026-06-02", SRC)  # the milestone slips into June
    assert column(m, "milestone") == [0, 0, 0, 0, 0, 450_000]


def test_dimension_knows_its_dates(m):
    month = m.dimensions["month"]
    assert month.period("2026-02").end_time == date(2026, 2, 28)
    assert repr(month).startswith("<Dimension month (time, M)")
    m.add_dimension("region", ["east"])
    with pytest.raises(tm.DefinitionError, match="not a time dimension"):
        m.dimensions["region"].period("east")


def test_a_date_matches_its_label(m):
    m.add_item("same_month", ("month",), dtype=int,
               formula=lambda x: int(x["milestone_date"].dt.to_period("M") == x.period))
    assert column(m, "same_month") == [0, 0, 0, 1, 0, 0]


def test_add_coords_continues_the_spine(m):
    m.add_coords("month", tm.period_range("2026-07", periods=2))
    assert m.dimensions["month"].coords[-1] == "2026-08"
    assert m.dimensions["month"].period("2026-08").days == 31
    assert m.get("retainer", month="2026-08") == 0
    m.add_coords("month", tm.period_range("2026-08", periods=1))  # already there: no-op
    with pytest.raises(tm.DefinitionError, match="after 2026-08 comes 2026-09"):
        m.add_coords("month", tm.period_range("2026-10", periods=1))
    with pytest.raises(tm.DefinitionError, match="can't add 'Q'"):
        m.add_coords("month", tm.period_range("2026Q4", periods=1, freq="Q"))
    with pytest.raises(tm.DefinitionError, match="time dimension"):
        m.add_coords("month", ["2026-09"])


def test_x_period_needs_one_time_dim():
    m = tm.Model()
    m.add_dimension("month", tm.period_range("2026-01", periods=2))
    m.add_dimension("year", tm.period_range("2026", periods=1, freq="Y"))
    m.add_dimension("line", ["a"])
    m.add_item("by_line", ("line",), formula=lambda x: x.period.days)
    m.add_item("both", ("month", "year"), formula=lambda x: x.period.days)
    m.add_item("days", ("month", "year"), formula=lambda x: x.periods["month"].days)
    with pytest.raises(tm.DefinitionError, match="none of this cell's dims"):
        m.get("by_line", line="a")
    with pytest.raises(tm.DefinitionError, match=r"x.periods\['month'\]"):
        m.get("both", month="2026-01", year="2026")
    assert m.get("days", month="2026-02", year="2026") == 28


def test_value_check_reads_the_period(m):
    m.add_check("MILESTONE-IN-RANGE", coords={"month": ["2026-04"]},
                expect=lambda x: x["milestone_date"] in x.period)
    assert m.check().ok


def test_save_and_load(m, tmp_path):
    m.add_dimension("fy", tm.period_range("2024", "2025", freq="Y-SEP", label="FY{year}"))
    m.save(tmp_path / "model.json")
    m.to_jsonl(tmp_path / "values.jsonl")
    saved = {d["name"]: d for d in json.loads((tmp_path / "model.json").read_text())["dimensions"]}
    assert saved["month"]["time"] == {"freq": "M", "first": "2026-01"}
    assert saved["fy"]["time"] == {"freq": "Y-SEP", "first": "2024", "label": "FY{year}"}
    again = tm.load(tmp_path / "model.json", allow_code=True)
    again.load_jsonl(tmp_path / "values.jsonl")
    assert again.dimensions["fy"].period("FY2025").start_time == date(2024, 10, 1)
    assert column(again, "retainer") == column(m, "retainer")

    data = json.loads((tmp_path / "model.json").read_text())
    data["dimensions"][0]["coords"][2] = "2026-04"
    with pytest.raises(tm.DefinitionError, match="don't match its time spine"):
        tm.Model.from_dict(data, allow_code=True)


def test_excel_shows_each_periods_dates(m, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    m.to_excel(tmp_path / "m.xlsx")
    ws = openpyxl.load_workbook(tmp_path / "m.xlsx")["Dimensions"]
    header = [c.value for c in ws[1]]
    feb = next(r for r in ws.iter_rows(min_row=2, values_only=True) if r[1] == "2026-02")
    assert feb[header.index("start")].date() == date(2026, 2, 1)
    assert feb[header.index("end")].date() == date(2026, 2, 28)

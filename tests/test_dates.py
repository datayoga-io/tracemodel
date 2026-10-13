"""Date and datetime items: a construction schedule whose milestone dates place contract payments."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest

import tracemodel as tm

SRC = {"document": "Schedule rev 2", "url": "https://example.com/schedule.pdf", "section": "Milestones"}
MONTHS = ["2026-03", "2026-04", "2026-05", "2026-06"]
MILESTONES = ["footings", "topped_out"]


def build():
    m = tm.Model()
    m.add_dimension("month", MONTHS)
    m.add_dimension("milestone", MILESTONES)
    m.add_dimension("contract", ["C-104", "C-110"])
    m.add_item("baseline_date", ("milestone",), dtype="date")
    m.add_item("forecast_date", ("milestone",), dtype=date, formula=lambda x: x["baseline_date"])
    m.add_item("slip_days", ("milestone",), dtype=int,
               formula=lambda x: (x["forecast_date"] - x["baseline_date"]).days)
    m.add_item("payment", ("contract", "milestone"), formula=lambda x: 0)
    m.add_item("planned_cost", ("contract", "month"), formula=lambda x: sum(
        x["payment"].sel(milestone=ms) for ms in MILESTONES
        if x["forecast_date"].sel(milestone=ms).dt.to_period("M") == x.coords["month"]))
    with m.transaction():
        m.set_value("baseline_date", {"milestone": "footings"}, date(2026, 3, 20), SRC)
        m.set_value("baseline_date", {"milestone": "topped_out"}, "2026-06-15", SRC)  # ISO, as pandas reads it
        m.set_value("payment", {"contract": "C-104", "milestone": "footings"}, 450_000, SRC)
        m.set_value("payment", {"contract": "C-104", "milestone": "topped_out"}, 700_000, SRC)
        m.set_value("payment", {"contract": "C-110", "milestone": "topped_out"}, 200_000, SRC)
    return m


@pytest.fixture
def m():
    return build()


def test_dtypes_and_iso_strings(m):
    assert m.items["baseline_date"].dtype is date
    assert m.get("baseline_date", milestone="topped_out") == date(2026, 6, 15)
    m.add_item("kickoff", (), dtype="datetime64[ns]")  # numpy's name, as xarray shows it
    assert m.items["kickoff"].dtype is datetime
    m.set_value("kickoff", {}, "2026-01-05T09:30", SRC)
    assert m.get("kickoff") == datetime(2026, 1, 5, 9, 30)
    m.set_value("kickoff", {}, date(2026, 1, 6), SRC)  # a date is midnight
    assert m.get("kickoff") == datetime(2026, 1, 6)


@pytest.mark.parametrize("value, why", [
    (20260320, "is a date"),
    ("March 20", "not an ISO date"),
    (datetime(2026, 3, 20, 9, 30), "time of day"),
])
def test_date_item_rejects(m, value, why):
    with pytest.raises(tm.InvalidValue, match=why):
        m.set_value("baseline_date", {"milestone": "footings"}, value, SRC)


def test_datetime_rejects_time_zone(m):
    m.add_item("kickoff", (), dtype=datetime)
    with pytest.raises(tm.InvalidValue, match="time zone"):
        m.set_value("kickoff", {}, datetime(2026, 1, 5, tzinfo=UTC), SRC)


def test_number_item_rejects_date(m):
    with pytest.raises(tm.InvalidValue, match="must be a number"):
        m.set_value("payment", {"contract": "C-104", "milestone": "footings"}, date(2026, 3, 1), SRC)


def test_milestone_date_places_payments(m):
    assert m.get("planned_cost", contract="C-104", month="2026-03") == 450_000
    assert m.get("planned_cost", contract="C-110", month="2026-06") == 200_000
    # the project slips "topped out" to May: every contract's payment on it moves
    m.set_value("forecast_date", {"milestone": "topped_out"}, "2026-05-28", SRC)
    assert m.get("planned_cost", contract="C-104", month="2026-05") == 700_000
    assert m.get("planned_cost", contract="C-110", month="2026-05") == 200_000
    assert m.get("planned_cost", contract="C-110", month="2026-06") == 0
    assert m.get("slip_days", milestone="topped_out") == -18


def test_expressions_with_dates(m):
    m.add_item("deadline", ("milestone",), dtype=date, formula=m["baseline_date"] + timedelta(days=14))
    assert repr(m.items["deadline"].formula) == "baseline_date + timedelta(days=14)"
    assert m.get("deadline", milestone="footings") == date(2026, 4, 3)
    m.add_check("ON-TIME", coords={"milestone": tm.ANY},
                expect=m["forecast_date"] <= m["baseline_date"] + timedelta(days=30))
    m.add_check("AFTER-START", coords={"milestone": tm.ANY}, expect=m["baseline_date"] >= date(2026, 1, 1))
    assert m.check().ok
    m.set_value("forecast_date", {"milestone": "footings"}, "2026-05-01", SRC)
    (late,) = m.check().failures
    assert late.at == {"milestone": "footings"}
    assert late.diff == timedelta(days=12)  # May 1 - (Mar 20 + 30 days)
    assert "2026-05-01" in str(m.check()) and "12 days" in str(m.check())


def test_value_check_compares_with_iso_string(m):
    m.add_check("IN-2026", coords={"milestone": tm.ANY},
                expect=lambda x: "2026-01-01" <= x["forecast_date"] and x["forecast_date"] <= "2026-12-31")
    assert m.check().ok


def test_dt_fields(m):
    m.add_item("quarter", ("milestone",), dtype=int, formula=lambda x: x["baseline_date"].dt.quarter)
    m.add_item("month_end", ("milestone",), dtype=date,
               formula=lambda x: x["baseline_date"].dt.date.replace(day=x["baseline_date"].dt.days_in_month))
    assert m.get("quarter", milestone="topped_out") == 2
    assert m.get("month_end", milestone="footings") == date(2026, 3, 31)


def test_mixing_dates_and_numbers_is_an_error(m):
    m.add_check("BAD", coords={"milestone": tm.ANY}, expect=m["baseline_date"] > 0)
    run = m.check().results[0]
    assert not run.passed and "can't compare a date with a number" in run.error
    with pytest.raises(tm.DefinitionError, match="can't compute date \\+ number"):
        m.evaluate(m["baseline_date"] + 1)


def test_formula_result_must_match_dtype(m):
    m.add_item("duration", ("milestone",), formula=m["forecast_date"] - m["baseline_date"])
    with pytest.raises(tm.DefinitionError, match=r"is float, but .* gave a timedelta.*\.days"):
        m.get("duration", milestone="footings")


def test_nil_date(m):
    m.add_item("actual_date", ("milestone",), dtype=date)
    m.set_value("actual_date", {"milestone": "footings"}, tm.NIL, SRC)  # cancelled: will never happen
    assert m.get("actual_date", milestone="footings") is tm.NIL
    m.add_check("DONE-OR-NIL", coords={"milestone": ["footings"]},
                expect=lambda x: x["actual_date"].is_nil() or x["actual_date"] >= x["baseline_date"])
    assert m.check().ok
    m.add_check("NO-GUARD", coords={"milestone": ["footings"]}, expect=m["actual_date"] >= m["baseline_date"])
    run = m.check().filter("NO-GUARD").results[0]
    assert not run.passed and "is nil" in run.error


def test_table_shows_iso_dates(m):
    text = str(m.table("baseline_date"))
    assert "2026-03-20" in text and "2026-06-15" in text


def test_save_and_load(m, tmp_path):
    cutoff = date(2026, 6, 1)
    m.add_item("before_cutoff", ("milestone",), dtype=int,
               formula=lambda x, cutoff=cutoff: int(x["forecast_date"] < cutoff))
    m.add_item("deadline", ("milestone",), dtype=date, formula=m["baseline_date"] + timedelta(days=14))
    m.set_value("forecast_date", {"milestone": "topped_out"}, date(2026, 5, 28), SRC)
    m.save(tmp_path / "model.json")
    m.to_jsonl(tmp_path / "values.jsonl")
    rows = [json.loads(line) for line in (tmp_path / "values.jsonl").read_text().splitlines()]
    assert rows[0]["value"] == "2026-03-20"
    saved = json.loads((tmp_path / "model.json").read_text())
    assert {i["name"]: i["dtype"] for i in saved["items"]}["baseline_date"] == "date"

    again = tm.load(tmp_path / "model.json", allow_code=True)
    again.load_jsonl(tmp_path / "values.jsonl", allow_code=True)
    for item, coords in [("planned_cost", {"contract": "C-104", "month": "2026-05"}),
                         ("deadline", {"milestone": "footings"}),
                         ("before_cutoff", {"milestone": "topped_out"}),
                         ("slip_days", {"milestone": "topped_out"})]:
        assert again.get(item, **coords) == m.get(item, **coords)
    assert again.get("deadline", milestone="footings") == date(2026, 4, 3)

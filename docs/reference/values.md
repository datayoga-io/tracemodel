# Dates, text and missing values

## Dates

Items can hold dates, as pandas and xarray columns do:

```python
from datetime import date, timedelta

model.add_dimension("milestone", ["footings", "topped_out"])
model.add_item("baseline_date", ("milestone",), dtype=date)            # or dtype="date"
model.add_item("forecast_date", ("milestone",), dtype=date,
               formula=lambda x: x["baseline_date"])                   # re-forecast by entering it
model.set_value("baseline_date", {"milestone": "footings"}, date(2026, 3, 20), SRC)
model.set_value("baseline_date", {"milestone": "topped_out"}, "2026-06-15", SRC)   # ISO string
```

| dtype | Holds | Accepts in `set_value` |
| --- | --- | --- |
| `date` (`"date"`) | `datetime.date` | a date, an ISO string `"2026-03-31"`, or a datetime at midnight (a pandas `Timestamp`) |
| `datetime` (`"datetime"`, `"datetime64[ns]"`) | `datetime.datetime`, no time zone (as numpy's `datetime64`) | a datetime, a date (midnight), an ISO string `"2026-03-31T09:30"`; pandas `Timestamp` and numpy `datetime64` too |

Arithmetic follows pandas: a date minus a date is a `timedelta`, and a date plus a `timedelta`
is a date. Comparisons are exact; there is no tolerance for dates.

```python
model.add_item("deadline", ("milestone",), dtype=date,
               formula=lambda x: x["baseline_date"] + timedelta(days=14))
model.add_check("ON-TIME", coords={"milestone": tm.ANY},
                expect=lambda x: x["forecast_date"] <= x["baseline_date"] + timedelta(days=30))
model.add_item("slip_days", ("milestone",), dtype=int,
               formula=lambda x: (x["forecast_date"] - x["baseline_date"]).days)
```

In a formula or check, `.dt` gives a date cell's fields with pandas' names:
`year`, `month`, `day`, `quarter`, `dayofweek` (Monday is 0), `dayofyear`, `days_in_month`,
`is_month_start`, `is_month_end`, `is_quarter_start`, `is_quarter_end`, `is_year_start`,
`is_year_end`, `hour`, `minute`, `second`, `date`, `strftime(fmt)`, and `to_period(freq)`: the
period label as pandas prints it (`"M"` → `"2026-03"`, `"Q"` → `"2026Q1"`, `"Y"` → `"2026"`,
`"D"` → `"2026-03-31"`). A date also compares with an ISO string there:
`x["start"] >= "2026-01-01"`.

A date places a number on a period dimension. Here each contract's milestone payment lands in
the month its milestone is forecast, so re-forecasting the milestone moves every contract's
payment:

```python
model.add_item("payment", ("contract", "milestone"), formula=lambda x: 0)   # 0 unless the terms say so
model.add_item("planned_cost", ("contract", "month"), formula=lambda x: sum(
    x["payment"].sel(milestone=ms) for ms in x.labels("milestone")
    if x["forecast_date"].sel(milestone=ms).dt.to_period("M") == x.coords["month"]))
```

- A formula's result must fit its item's dtype: a timedelta in a number item raises
  `DefinitionError` (take `.days`), and so does a datetime in a date item (take `.dt.date`).
- Mixing dates and numbers (`date + 1`, `date > 0`) raises `DefinitionError`; in a check, the
  run fails with the error. Use `timedelta(days=n)`, or `x["days"] * timedelta(days=1)`.
- `tm.NIL` on a date item means there is no date (a milestone that was dropped). It doesn't
  count as 0: test `is_nil()` before computing with it, or the calculation raises.
- Tables show ISO dates and timedeltas in days (`18 days`). The values file writes dates as ISO
  strings; a date a formula uses from where it was written is saved as `{"$date": "2026-03-31"}`.

## Text

Items can hold text, as pandas' `"string"` columns do: a contract's status, its vendor, the
reason for a change order. Text is sourced, replaced and kept in the history like any value,
which attrs (fixed in the definition, with no source) can't be.

```python
model.add_item("status", ("contract",), dtype=str)        # or dtype="str" / "string"
model.set_value("status", {"contract": "C-101"}, "signed", SRC)
model.add_item("flag", ("contract",), dtype=str,
               formula=lambda x: "over" if x["eac"] > x["committed"] else "within")
model.add_check("KNOWN-STATUS", coords={"contract": tm.ANY},
                expect=lambda x: x["status"] in ("signed", "LOI", "not bid"))
```

- In a formula or check, text compares with `==`, `!=`, `<` (alphabetical) and `in`, and joins
  with `+`. Comparing text with a number raises `DefinitionError`.
- `.str` gives a text cell's methods with pandas' names: `contains(text, case=True)` (plain
  text, not a regular expression), `startswith`, `endswith`, `len()`, `lower()`, `upper()`,
  `strip()` and `replace(old, new)`: `x["vendor"].str.contains("Ltd")`.
- Empty text is rejected: when the source shows nothing, enter `tm.NIL`. As with dates, a nil
  text cell isn't 0: `get()` gives `tm.NIL`, and computing with it raises; test `is_nil()` first.
- On a `str` item, text passed to `set_value` is always the value, even `"lambda ..."`; pass a
  cell formula as the lambda itself.
- A formula's result must fit its dtype: text in a number item raises, and so does a number in
  a `str` item.
- Excel writes text cells as text, so a value starting with `=` never becomes an Excel formula.

## Time dimensions

A time dimension's labels know their dates, as pandas' `PeriodIndex` does (and as Anaplan's and
Pigment's time dimensions do). Build one with `tm.period_range`, named and used as pandas'
`period_range`:

```python
model.add_dimension("month", tm.period_range("2026-01", "2027-12", freq="M"))   # "2026-01" ... "2027-12"
model.add_dimension("quarter", tm.period_range("2026Q1", periods=8, freq="Q-SEP"))  # fiscal quarters
model.add_dimension("period", tm.period_range("2024", "2025", freq="Y-SEP", label="FY{year}"))  # FY2024, FY2025
```

- **Frequencies** are pandas' period aliases: `"M"`; `"Q"` or `"Q-SEP"`, quarters of a fiscal
  year ending in September; `"Y"` or `"Y-SEP"`. A fiscal year is named by the year it ends in:
  with `"Y-SEP"`, `2025` runs from 1 October 2024 to 30 September 2025.
- **52/53-week fiscal years** end on a weekday, as pandas' `FY5253` offset: `"Y-SEP-SAT-LAST"`
  on the last Saturday of September (Apple's: `2025` runs from 29 September 2024 to 27 September
  2025), `"Y-JAN-SAT-NEAREST"` on the Saturday nearest 31 January (the retail 4-5-4 calendar).
  A year has 364 or 371 `days`. Its quarters, `"Q-SEP-SAT-LAST"`, are 13 weeks; in a 53-week
  year the first quarter has 14, or the one named at the end, as `FY5253Quarter`'s
  `qtr_with_extra_week`: `"Q-JAN-SAT-NEAREST-4"`. A year is named by the year of its month, as
  with `"Y-JAN"`: the retail year ending 3 February 2024 is `2024` (the NRF calls it 2023).
- **Labels** are strings in pandas' form (`"2026-03"`, `"2026Q1"`, `"2026"`), or `label=` with
  the fields `{year}`, `{quarter}` and `{month}`. Being strings, they work as any label: `lag`,
  `sel`, checks, saving, Excel. `.dt.to_period("M")` on a date gives the same strings.
- **Give two** of `start`, `end` and `periods`; `start` and `end` are period strings or dates.
- **Each label is a `tm.Period`**: `model.dimensions["month"].period("2026-03")`, with
  `start_time` and `end_time` (dates; the period includes both), `days`, `year` (a fiscal
  year for `Q-`/`Y-` frequencies), `quarter`, `month`, `asfreq(freq)` and `overlap(start, end)`.

In a formula or check, `x.period` is the cell's period (`x.periods["month"]` when
the cell has several time dims). A date compares with it: `d in x.period` falls within it,
`d < x.period` is before it, `d > x.period` after it. `overlap` gives the share of the period's
days in a date range, which prorates and spreads:

```python
# a retainer from 16 March bills 16/31 of March's fee; a milestone lands in the month of its date
model.add_item("retainer", ("month",), formula=lambda x: x["fee"] * x.period.overlap(x["start"], x["end"]))
model.add_item("milestone", ("month",),
               formula=lambda x: x["milestone_amount"] if x["milestone_date"] in x.period else 0)
```

The definition file saves the spine as `"time": {"freq": "M", "first": "2026-01"}` beside the
labels. Months of a 52/53-week year (4-4-5 weeks) aren't modelled.

## Not entered vs nil

A cell is either **not entered** (the default: no `set_value` yet) or has a value. When the
source reports a cell as nil ("—" or blank in a table that has numbers elsewhere), enter it
explicitly, with a source like any value:

```python
model.set_value("cost", coords={"segment": "/corporate", "period": "FY2025"}, value=tm.NIL, source=SRC)
```

| | Not entered | `tm.NIL` |
| --- | --- | --- |
| Meaning | Nobody has entered it yet | The source shows nothing here |
| In checks | `missing`: the check doesn't pass | Counts as 0 |
| In tables and `explain()` | blank / `MISSING` | `—` |
| `get()` | raises `MissingValue` | 0 (the entry's `value` is `tm.NIL`) |
| Values file | no row | `"value": null` |
| In a formula or check | `x["cost"].is_nil()` is missing | `x["cost"].is_nil()` holds |

Don't enter 0 for a "—": a real 0 and a nil cell are different facts.

Not entered is normal, not an error. A model's dims are a grid, and a source rarely fills all of
it: a three-year income statement next to a two-year balance sheet, or a segment that has no
costs. The library can't know which cells a source was meant to fill, so it never reports empty
cells by themselves. Only a check that reads a cell reports it as `missing`; to leave cells out
of a check, give the check `coords` (a list of labels, or `tm.filter_by_attrs(...)`). Don't add
lines or skip a period to avoid empty cells, and don't enter nil for a cell the source doesn't
show at all.

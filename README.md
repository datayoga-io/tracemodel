# tracemodel

tracemodel lets you build a financial model in Python in which every number has a fixed address,
a source you can open, and rules that check it.

**What it is.** tracemodel is a Python library for financial and business models. A model holds
items (revenue, cash, a balance sheet line) over named dimensions (period, region, line). Each
number is entered with its source, or calculated by a formula. Checks state what must hold, such
as "total = sum of the parts".

**Why we built it.** Financial models usually live in spreadsheets. In a spreadsheet, a cell has
no source, a person can overwrite a formula with no trace, and a broken total can stay hidden. A
DataFrame does not solve this: it has no declared definition, no per-cell formulas and no record
of where a number came from. tracemodel keeps the definition, the values, their sources and the
checks in one model. A person or an AI agent fills it in, and a reviewer can trace each number.

**Why financial modeling is different.**

- Reported and calculated numbers mix. A subtotal in a report is an input, not a formula: you
  enter it and check the arithmetic.
- Actuals override forecasts. Past periods are entered; later periods are calculated from the
  period before.
- Balances roll forward. Each opening balance is the previous ending; the first one comes from
  outside the model.
- Reports round their numbers. A total can differ from its parts by a small amount, so checks
  need a tolerance.
- Some cells follow their own terms, such as one contract with a different escalator.
- Every number must tie back to a page in a filing, report or contract.

**What it is good for.**

Above all, tracemodel gives AI a **financial brain**. A language model can read a filing, but it
does not reliably keep hundreds of linked numbers consistent, roll balances forward or notice that
a total does not tie. tracemodel gives the agent that structure: a declared model to fill,
formulas that calculate the rest, and checks that catch its errors. The agent writes values; the
model keeps them consistent, and each value traces back to its source.

- Building an operating or three-statement model from filings and reports, with a source on each
  number.
- Letting AI agents extract values into a fixed definition that rejects values that do not fit.
- Reviewing a model: `explain()` shows the cells each check and formula read, back to the entered
  values, and `precedents()` / `dependents()` trace any cell as Excel does.

## Install

Python 3.12 or later. One dependency, [RestrictedPython](https://github.com/zopefoundation/RestrictedPython),
which runs formulas safely; the Excel export needs openpyxl.

```bash
pip install pytracemodel
pip install "pytracemodel[excel]"    # with model.to_excel
```

```python
import tracemodel as tm
```

## Concepts

| Concept | What it is |
| --- | --- |
| **Dimension** | A named axis with an ordered list of coordinate labels, e.g. `period`: `Q1`, `Q2`. |
| **Coords** | The labels along a dimension. A value is addressed by one label per dimension: `{"region": "/north", "period": "Q1"}`. |
| **Hierarchy** | A dimension whose labels are paths: `/`, `/north`, `/north/boston`. A path's parent is the path minus its last segment. |
| **Item** | A named measure (e.g. `revenue`) declared over some dimensions, with a data type and attributes such as units. Holds no numbers until values are set. |
| **Formula** | A Python lambda of `x`, the cell being calculated: `lambda x: x["revenue"] * 0.6`. The same lambda is checked, saved and loaded. |
| **Check** | A data quality rule: a lambda of `x` that returns `True` or `False`, run once for each label of the dimensions you choose. |
| **Value** | One number for one item at full coords, with a `source` saying where it came from. |

## Workflow

A model is built in four steps, in this order:

1. **Dimensions** — `model.add_dimension(...)`
2. **Items scaffold** — `model.add_item(...)`
3. **Data quality** — `model.add_check(...)`
4. **Values** — `model.set_value(...)`, then `model.check()`

Steps 1–3 are the *definition*: what can exist and what must hold. Step 4 fills it in.
They can be done by different people or agents: whoever writes values only calls
`set_value`, and every call is validated against the definition.

To run steps 1–3 and step 4 as two AI agents, see [docs/agents.md](docs/agents.md).

## Example

A retail chain's sales by region and quarter.

```python
import tracemodel as tm

model = tm.Model()

# 1. Dimensions
model.add_dimension("period", [
    ("Q1", "First quarter", {"end": "2026-03-31"}),   # (code, description, attrs)
    ("Q2", "Second quarter", {"end": "2026-06-30"}),
])
model.add_dimension("region", tm.Hierarchy([
    ("/", "All regions"),                              # (code, description)
    "/north",                                          # code only
    "/north/boston",
    "/north/portland",
    ("/south", "South"),
]))
model.add_dimension("channel", tm.Hierarchy(["/store", "/online"]))

# 2. Items scaffold
USD_K = {"units": "USD", "scale": "thousands"}
model.add_item("revenue", dims=("region", "period"), dtype=float, attrs=USD_K, description="Net revenue")
model.add_item("returns", dims=("channel", "period"), dtype=float, attrs=USD_K, description="Customer returns")
model.add_item("stores", dims=("period",), dtype=int, attrs={"units": "stores"})

# 3. Data quality: total revenue equals north plus south, in every period
model.add_check("DQ-1",
    coords={"period": tm.ANY},
    expect=lambda x: abs(
        x["revenue"]["/"] - (x["revenue"]["/north"] + x["revenue"]["/south"])
    ) <= 0.5,                                  # within 0.5 (USD thousands) for rounding
    description="Total revenue = North + South",
)

# 4. Values
SRC = {"document": "Q2 sales report", "url": "https://example.com/q2.pdf", "section": "Table 1"}
model.set_value("revenue", coords={"region": "/", "period": "Q1"}, value=1_200, source=SRC)
model.set_value("revenue", coords={"region": "/north", "period": "Q1"}, value=700, source=SRC)
model.set_value("revenue", coords={"region": "/south", "period": "Q1"}, value=500, source=SRC)
model.set_value("stores", coords={"period": "Q1"}, value=42, source=SRC)

model.check()            # DQ-1 passes for Q1; Q2 fails because its values are missing
model.table("revenue")   # regions down, periods across
```

## API reference

### Model

```python
model = tm.Model()
```

**Definition**

| Call | Does |
| --- | --- |
| `add_dimension(name, coords)` | `coords` is a list of coord entries (see Coord entries), a `tm.Hierarchy`, or a time spine, `tm.period_range(...)` (see Time dimensions). Re-adding an identical dimension is a no-op; a different one under the same name raises `DefinitionError`. |
| `add_coords(name, entries)` | Appends labels to a dimension, e.g. forecast periods (entries as in Coord entries; a hierarchy path needs its parent). Items get the new cells, not entered. Checks given `tm.ANY` or `tm.filter_by_attrs(...)` for that dimension are re-expanded to cover the new labels; checks with listed labels keep them. Labels already there with the same entry are a no-op. Labels can't be removed or reordered. A time dimension takes the periods that continue it: `add_coords("month", tm.period_range("2027-01", periods=12))`. |
| `remove_dimension(name)` | Removes a dimension that no item uses; raises `DefinitionError` naming the items that do. |
| `add_item(name, dims, dtype=float, attrs=None, formula=None, description=None)` | `dims`: tuple of declared dimension names. `dtype`: `int`, `float`, `str`, `date` or `datetime` (as types or names; see Text and Dates). `attrs`: free-form dict; `units` and `scale` are shown in tables. By convention `period_type` is `"duration"` (a flow over the period) or `"instant"` (a balance at its end), for whoever reads the model; the library doesn't act on it. `formula`: a lambda of `x`, or its source text, that calculates the item's cells (see Formulas). `description`: the item's title as people read it, e.g. the caption the source prints ("Lease-related assets and liabilities"), as a dimension label has one; `name` stays the code formulas use. Re-adding an identical item is a no-op. |
| `add_check(id, expect, coords=None, description=None, severity="error")` | `expect`: a lambda of `x`, or its source text, that returns `True` or `False` (see Data quality). `coords`: `{dim: tm.ANY \| tm.filter_by_attrs(...) \| label \| [labels]}` — the check runs once per combination. Every dimension a cell it reads leaves open must appear here, and every dimension here must be left open by some cell (otherwise `DefinitionError`). When every cell fixes all its coords, omit `coords`: the check runs once. `severity`: `"error"` or `"warning"`; a failing warning is reported but doesn't make `report.ok` false. An existing `id` is replaced. |
| `model[item]` | The item's cells, to show as a table; see Selecting and showing cells. |
| `model.item(name)` | The declared `Item`; raises `UnknownItem`. |
| `model.dimensions`, `model.items`, `model.checks` | Dicts by name/id of `Dimension`, `Item` and `Check` objects (below). |
| `print(model)` | A summary: each dimension with every label and its description and attrs, each item with its dims, dtype, attrs and formula, each check with its condition and the dims it runs over, and the number of values. |

**Declared objects** (read-only)

| Object | Fields |
| --- | --- |
| `Dimension` | `name`, `coords` (tuple of labels), `hierarchy` (the `tm.Hierarchy`, or `None` for a plain list), `description(label)`, `attrs(label)` |
| `Item` | `name`, `dims`, `dtype`, `attrs`, `formula` (or `None`), `unit_label` (`units` and `scale` joined) |
| `Check` | `id`, `expect` (a `tm.Formula`), `coords` (dim → labels it runs over), `description`, `severity`, `runs()` (the coords of each run) |
| `Formula` | `source` (`"lambda x: x['revenue'] * 0.6"`), `env` (the closure values it uses), `repr()` (its body); `tm.Formula("lambda x: ...")` checks and compiles one |

**Values**

| Call | Does |
| --- | --- |
| `set_value(line_item, coords, value, source)` | `coords` must give exactly one declared label for each of the item's dims. `value` must match the item's `dtype` (`int` items reject non-integers; `str` items take text; date items take a date or an ISO string), or `tm.NIL` (see below), or a lambda (or its text, `"lambda x: ..."`, except on a `str` item, where text is the value): a formula for this one cell (see Formulas). The last write wins: setting a cell again replaces its value and source, and the old ones are kept in the entry's `history`. |
| `get(line_item, **coords)` | The value at full coords: a number, or a date for a date item (0 for a nil cell; `tm.NIL` for a nil date). |
| `entry(line_item, **coords)` | With full coords: an `Entry` (`value`, `source`, `history`). With fewer coords: a `Table` of matching entries. |
| `table(line_item)` | The item as a grid: `period` across (if the item has it), other dims down. Cells not entered are blank; nil cells show `—`. |
| `evaluate(formula)` | Runs a lambda of `x` for every combination of the dims its cells leave open, e.g. `model.evaluate(lambda x: x["revenue"] - x["cost"])`: a `Table`, or a number when no dim is open. |
| `precedents(line_item, deep=False, **coords)` / `dependents(...)` | What a cell's formula can read, and what can read the cell; see Tracing. |
| `check()` | Runs every check; returns a `Report`. |
| `transaction()` | Groups changes; see Transactions. |
| `to_jsonl(path)` / `load_jsonl(path)` | Save / load values, one JSON object per value. Loading validates like `set_value`, in one transaction: a bad row loads nothing. |
| `to_excel(path, *, title="Model", sections=None, layout="sheet", columns=None, split_after=None, page_breaks=True)` | Writes the model as an Excel workbook; see Excel export. |

**Saving the definition**

| Call | Does |
| --- | --- |
| `save(path)` | Writes the definition (dimensions, items, formulas, checks) to a JSON file. Values are not included; save them with `to_jsonl`. |
| `tm.load(path, formulas="on")` | A model from a file written by `save`. Formulas are checked and compiled as when they were added; `formulas="off"` keeps them as text and runs none (see Saving a model). |
| `to_dict()` / `tm.Model.from_dict(data, formulas="on")` | The same, as plain data. |

**Source.** Every value needs a `source` dict with `document`, `url` and `section`;
`page` and `quote` are optional. Other keys are rejected.

### Coord entries

Every dimension, plain or hierarchy, lists its coords as entries of one of three forms:

| Entry | Example |
| --- | --- |
| `"code"` | `"Q1"`, `"/north"` |
| `(code, description)` | `("Q1", "First quarter")` |
| `(code, description, attrs)` | `("Q1", "First quarter", {"end": "2026-03-31"})` |

`attrs` is a free-form dict. Use `None` as the description to give attrs only.
`dim.description(label)` returns the description, falling back to the code (for a hierarchy,
the last path segment); `dim.attrs(label)` returns the attrs, or `{}`.

### Hierarchy

```python
tm.Hierarchy(["/", "/a", ("/a/x", "Description"), ("/b", "B", {"note": "..."})])
```

- Entries as above; each code is a path.
- Every path's parent must also be declared, except the root `/`: top-level paths such as
  `/a` and `/b` may stand alone.
- `h.parent(path)`, `h.children(path)`, `h.description(path)` (falls back to the last path segment).
- Reach it with `model.dimensions["region"].hierarchy`.

### Selecting and showing cells

`model["revenue"]` is the whole item. Fix coords by label or by name; displayed in
IPython/Jupyter, it shows as a table:

```python
model["revenue"]["/north"]                       # the label must belong to exactly one of the item's dims
model["revenue"].sel(region="/north", period="Q1")
model["revenue"]["/"].lag("period")              # each period's previous one; the first shows blank
```

To compute with cells, write a lambda of `x` (see Formulas); `model.evaluate` runs one for
every combination of the dims its cells leave open, as xarray broadcasts by dimension name:

```python
model.evaluate(lambda x: x["revenue"] / x["revenue"]["/"])     # each region's share, by region and period
model.evaluate(lambda x: x["revenue"]["/"] / x["stores"])      # revenue per store, by period
```

Arithmetic on `model["revenue"]` itself raises `DefinitionError`, with the lambda to write:
`model["revenue"] * 0.6` says `formula=lambda x: x['revenue'] * 0.6`.

**Earlier and later labels.** `lag` and `lead` (named as in SQL's window functions) read the
same item a number of labels earlier or later along a plain dimension; `shift` is the
xarray / pandas spelling of the same thing.

| Write | Reads |
| --- | --- |
| `x.lag("period")`, `x.lag("period", 2)` | the previous period, two periods back |
| `x.lead("period")` | the next period |
| `x.shift(period=1)`, `x.shift(period=-1)` | the same as `lag` and `lead` |

A check that lags must leave the first label out of its coords, because it has nothing before
it; a lead leaves out the last. Slice the dimension's labels, like any Python tuple:

```python
model.add_check("GROWTH-1",
    coords={"period": model.dimensions["period"].coords[1:]},   # every period but the first
    expect=lambda x: x["revenue"]["/"] >= x["revenue"]["/"].lag("period"),
    description="Total revenue doesn't shrink",
)
# runs once, period=Q2: revenue['/'] at Q2 >= revenue['/'] at Q1
```

With `coords={"period": tm.ANY}` instead, `add_check` raises `DefinitionError`: there is no
period before Q1, and the message suggests `coords[1:]`. Lag and lead work on plain dimensions
only: a hierarchy has no previous line.

### Data quality

A check's `expect` is a lambda of `x`, the run, that returns `True` or `False`. It runs once per
combination of the labels in `coords`; `x["item"]` reads an item at the run's labels, and any
Python comparison works:

| Write | Holds when |
| --- | --- |
| `a < b`, `a <= b`, `a > b`, `a >= b`, `a == b`, `a != b` | the comparison is true |
| `0 <= a <= 1` | both comparisons hold |
| `a and b`, `a or b`, `not a` | both hold / at least one holds / it does not hold |
| `x["cost"].is_nil()` | the cell was entered as `tm.NIL`, not as a number such as 0 (not entered: missing) |
| `max`, `min`, `abs`, `round`, `sum`, `any`, `all`, `a if c else b` | as in Python |

A tolerance is arithmetic too: `abs(a - b) <= 0.5` allows a difference of 0.5 in the item's
units, and `abs(a - b) <= 0.001 * abs(a)` allows 0.1%. Comparisons allow for float rounding
(about one part in a billion), so `abs(1_000.2 - 1_000) <= 0.2` holds; a date never compares
with a number (`DefinitionError`).

```python
model.add_check("SIGN-1", coords={"region": tm.ANY, "period": tm.ANY},
    expect=lambda x: x["revenue"] >= 0)
model.add_check("MIX-1", coords={"period": tm.ANY},
    expect=lambda x: 0.4 <= x["revenue"]["/north"] / x["revenue"]["/"] <= 0.7)
```

- A check whose body is one comparison (`a <= b`) reports left, right and diff for each run.
- The cells it reads are traced. A missing cell fails the run and is listed in `missing`.
- An error, or a return value that isn't `True` or `False`, fails the run. The result's `error`
  says why. An unknown item or label is caught when the check is added.
- In a check, `x` is the run, not a cell: `x["revenue"]` reads a cell, `x.coords` gives the
  run's labels, `x.attrs(dim)` their attrs.

`tm.ANY` in check coords means "every label of that dimension".
`tm.filter_by_attrs(kind="forecast")` (named after xarray's `Dataset.filter_by_attrs`) means
"every label whose attrs match": `kind` is `"forecast"` in the label's attrs, and with several
attrs, all of them match. Both are resolved again when `add_coords` adds labels, so a check
over `filter_by_attrs(kind="forecast")` covers forecast periods added later:

```python
FORECAST = tm.filter_by_attrs(kind="forecast")
model.add_dimension("year", [("FY2024", None, {"kind": "actual"}),
                             ("FY2025", None, {"kind": "actual"}),
                             ("FY2026", None, {"kind": "forecast"})])
model.add_item("sales", dims=("year",), formula=lambda s: s.lag("year") * 1.03)
model.add_check("FC-SALES", coords={"year": FORECAST}, expect=lambda x: x["sales"] > 0)
model.add_coords("year", [("FY2027", None, {"kind": "forecast"})])   # FC-SALES now runs FY2026, FY2027
model.check().filter(year=FORECAST).ok                               # only the forecast years
```

**Rollups.** When a rule depends on a hierarchy, such as "a region equals the sum of *its*
children", read the run's children with `x.children(dim)`. It covers labels added later:

```python
model.add_check("ROLLUP-1",
    coords={"region": ["/", "/north"], "period": tm.ANY},
    expect=lambda x: abs(x["revenue"] - sum(x["revenue"].sel(region=c) for c in x.children("region"))) <= 0.5,
    description="Each region = the sum of its children",
)
# region="/":      revenue['/'] against revenue['/north'] + revenue['/south']
# region="/north": revenue['/north'] against revenue['/north/boston'] + revenue['/north/portland']
```

`x.parent(dim)` is the label above (`None` at the top), and `x.labels(dim)` every label of a
dimension, for one that isn't a hierarchy.

### Results and display

- `Report`: `ok` (no failing error check), `filter(check=None, **coords)` (the runs of one check and/or on some labels: a label, a list, `tm.ANY` or `tm.filter_by_attrs(...)`; returns a `Report`), `results`, `failures` (every run that didn't pass), `errors` and `warnings` (failures by severity), `explain(check_id=None)` (each run with every cell it read), `table()`, `show()`.
- Each result: `check`, `at` (the coords of the run), `lhs`, `rhs`, `diff`, `passed`, `cells`, `missing`, `error`.
- `lhs`, `rhs` and `diff` are set for a check that is one comparison, else `None`. `error` holds the error text when the check raised or didn't return a bool.
- A run fails with `error` set, instead of `check()` raising, when a cell it reads can't be calculated: say a formula's `lag` before the first period, or an `int` formula that gives a fraction.
- A missing value fails the check that reads it; it is never treated as 0.
- Each run's result: `pass`, `FAIL`, `missing`, or for a warning check `warn` / `warn: missing`.
- `Table`: `columns`, `rows`, `title`, `to_records()`, `show()`. Renders as HTML in Jupyter and as aligned text elsewhere.

### Formulas

An item with `formula=` is calculated, not entered. A formula is a lambda of `x`, the cell
being calculated, run once per cell with numbers, so any Python expression works: `max`, `min`,
`if`/`else`, `math`. Use it for numbers the source doesn't report, such as ratios, growth or
recomputed figures:

```python
model.add_item("avg_revenue_per_store", dims=("period",), attrs=USD_K,
               formula=lambda x: x["revenue"]["/"] / x["stores"])

FLOOR = 25_000                                   # minimum cash
model.add_item("cash", dims=("period",), attrs=USD_K)
model.add_item("revolver_draw", dims=("period",), attrs=USD_K,
               formula=lambda x: max(0, FLOOR - x["cash"]))
# cash 40,000 → draw 0;  cash 18,000 → draw 7,000
```

A cell is either **entered** with `set_value` (the last write wins) or **calculated** by its
item's formula. An entered cell overrides the formula for that one cell, with a source, as in
Anaplan; the entry has `overrides_formula=True`.

| In the lambda | Reads |
| --- | --- |
| `x["cash"]` | item `cash` at this cell's coords (a label of the item itself if no item has that name) |
| `x["cash"]["/north"]`, `x["cash"].sel(region="/north")` | `cash` with a coord fixed (on a cell reached from `x`, `[...]` is always a label) |
| `x.lag("period")`, `x["cash"].lag("period")` | this item / `cash` one period earlier |
| `x.coords` | this cell's labels, e.g. `{"period": "Q2"}` |
| `x.attrs("period")` | the attrs of this cell's period label, e.g. `{"kind": "forecast"}` |
| `x.children("region")`, `x.parent("region")`, `x.labels("line")` | labels from the dimensions (see Rollups) |
| `x.period`, `x.periods["month"]` | the cell's period, with its dates (see Time dimensions) |
| `x["cash"].is_nil()` | whether the cell read was entered as `tm.NIL` |
| `x["start"].dt.month`, `.dt.to_period("M")` | fields of a date cell (see Dates) |

**One formula for many lines.** When an item spans lines that each need their own formula (a
balance sheet over a `bs_line` hierarchy), give the item one lambda that picks by label.
Entered actuals still win; returning `None` leaves a cell missing:

```python
model.add_item("balance_sheet", dims=("bs_line", "period"),
    formula=lambda x: None if x.attrs("period").get("kind") != "forecast"
    else x["sales"] * x["dso"] / 365 if x.coords["bs_line"] == "/current/receivables"
    else x["cogs"] * x["dio"] / 365 if x.coords["bs_line"] == "/current/inventory"
    else x["/current/receivables"] + x["/current/inventory"] if x.coords["bs_line"] == "/current"
    else None)
```

Forecast periods added later with `add_coords` are calculated too. (`x["/current/receivables"]`
reads a line of the item itself, since no item has that name.)

- `x` and what it reads behave as their values: numbers, or dates for date items. A cell that
  isn't entered makes the whole cell missing; a division by zero too.
- A formula is checked when it is added: an unknown item or label, a dim left open, or a cell
  that would depend on itself raises `DefinitionError`. Both arms of an `if`/`else` on a value
  count, since either may run. Inside a transaction these are checked at commit, so a formula
  may read an item added later in it.
- A formula is its source text: the lambda's source is read when it is added, and that text is
  what runs, is shown (`print(model)` shows `max(0, FLOOR - x['cash'])`) and is saved. The
  lambda itself is never called. Pass the text instead when there is no source file to read
  (a lambda typed at a plain `python` prompt): `formula="lambda x: max(0, 25_000 - x['cash'])"`.
- The names it uses from where it is written (`FLOOR`) are copied as data: numbers, strings,
  lists and dicts of them, and dates. A closure over the model or its cells raises, saying to
  read cells through `x`. See What a formula may use.

**Projections: reading the item's own earlier cells.** Pass the formula as a function of the
item's cells. Entered actuals win; the later periods are calculated, each from the one before:

```python
model.add_dimension("year", ["FY2024", "FY2025", "FY2026", "FY2027"])
model.add_item("sales", dims=("year",), attrs=USD_K,
               formula=lambda sales: sales.lag("year") * 1.03)      # 3% growth
model.set_value("sales", coords={"year": "FY2024"}, value=4_800, source=SRC)
model.set_value("sales", coords={"year": "FY2025"}, value=5_100, source=SRC)
model.table("sales")
# year       value
# FY2024     4,800      entered
# FY2025     5,100      entered
# FY2026     5,253      = 5,100 × 1.03
# FY2027  5,410.59      = 5,253 × 1.03
```

**Opening balances.** A balance that rolls forward has one number from outside the model: the
balance before the first period. It's a single value, not a column, so it needs no extra period.
Give the beginning line a formula that reads the previous period's ending, and enter the first
period's beginning; entered cells win over the formula:

```python
model.add_dimension("period", ["FY2024", "FY2025", "FY2026"])
model.add_item("cash_end", dims=("period",), attrs=USD_K,
               formula=lambda x: x["cash_begin"] + x["cash_flow"])
model.add_item("cash_begin", dims=("period",), attrs=USD_K,
               formula=lambda x: x["cash_end"].lag("period"))
model.add_item("cash_flow", dims=("period",), attrs=USD_K)
model.set_value("cash_begin", coords={"period": "FY2024"}, value=29_943, source=SRC)  # the opening balance
# cash_begin  FY2024 29,943 entered;  FY2025, FY2026 = cash_end one period earlier
```

When the source reports every period's beginning balance (a cash flow statement does), they are
inputs: enter each one, with no formula, and check that it ties to the previous ending from the
second period on (`coords={"period": PERIODS[1:]}`). The first period's beginning ties to
nothing in the model; that is expected. When several items read the opening balance, make it
an item with no dims (`dims=()`), entered once.

**A formula for one cell.** `set_value` also takes a lambda, or its text, as the value: that cell is calculated by its own formula, sourced like any value. Use it
when one cell follows different terms, such as a contract with its own escalator:

```python
model.add_dimension("contract", ["C-101", "C-104"])
model.add_item("fee", dims=("contract", "year"), attrs=USD_K,
               formula=lambda fee: fee.lag("year") * 1.02)          # the standard 2% escalator
for c in ["C-101", "C-104"]:
    model.set_value("fee", coords={"contract": c, "year": "FY2024"}, value=100, source=SRC)
MSA = {"document": "MSA C-104", "url": "https://example.com/msa-c104.pdf", "section": "4.2"}
model.set_value("fee", coords={"contract": "C-104", "year": "FY2026"},
                value=lambda fee: fee.lag("year") * 1.10, source=MSA)   # a 10% step-up in FY2026
model.table("fee")
# contract  year       value
# C-101     FY2026    104.04      the item's 2%
# C-104     FY2026     112.2      = 102 × 1.10, this cell's formula
# C-104     FY2027   114.444      = 112.2 × 1.02, back on the item's 2%
# ...
```

When only the terms differ (rate, start year), prefer entering the terms as items of their
own under one item formula; a cell formula is for a cell whose formula really is different.

- A formula may leave open only dims the item has; fix the rest with `["label"]` / `.sel()`.
- On an `int` item, a formula's result must be a whole number (float noise such as
  `9.999999999999998` is rounded); otherwise reading the cell raises `DefinitionError`. Round in
  the formula: `formula=lambda x: round(x["buyback"] / x["price"])`.
- A formula that would make a cell depend on itself is rejected when it is added, e.g.
  `lambda x: x.lag("year") + x.lead("year")` (`DefinitionError: circular formula: ...`).
- A cell is missing when any cell its formula reads is missing; the first period of a lag
  has nothing before it, so enter it.
- Checks read calculated cells like any other; `explain()` lists the cells each formula read,
  back to the entered ones.
- `entry()` shows a cell formula as `= fee.lag('year') * 1.1`; the values file saves it as
  `"formula": "lambda fee: fee.lag('year') * 1.1"` instead of `"value"`.

A number the source reports is an input, even when it equals other numbers' arithmetic (a
subtotal, gross margin on the income statement): enter it with its source and add a check
for the arithmetic. A formula on it would hide the reported number.

### Transactions

Filling in a model takes many calls, and some states in between aren't valid yet: a cell
formula can read a cell that, for now, reads it back. Group the calls in a transaction:

```python
with model.transaction():
    for year in ["FY2026", "FY2027"]:
        model.set_value("fee", coords={"contract": "C-101", "year": year},
                        value=lambda fee: fee.lag("year") * 1.05, source=MSA)
# committed here
```

- Checks that need the whole picture (circular formulas) run once, at commit.
- If the commit fails, or an exception leaves the block, every change made inside it is undone:
  values, items, labels and checks.
- Reads inside the transaction (`get`, `table`, `check`) see the changes so far; reading a cell
  that loops for now raises `DefinitionError`.
- Without `with`: `tx = model.transaction()`, then `tx.commit()` or `tx.rollback()`. One
  transaction at a time.

Calculated cells are cached, so a long chain of formulas (years of monthly periods, each
reading the one before) is calculated once per cell. As a spreadsheet does, the model records
which cells each formula read; a new value recalculates only the cells that read it, directly
or through other formulas, and `check()` re-runs only the runs that read those. Changing a
definition (an item, a dimension, its labels) recalculates everything. The values a formula
uses from where it was written are copied when it is added, so changing them later doesn't
change the formula.

### Dates

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

### Text

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

### Time dimensions

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
labels. Periods are calendar months, quarters and years;
52/53-week fiscal years (Apple's ends on the last Saturday of September) aren't modelled.

### Not entered vs nil

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

### Tracing: precedents and dependents

As Excel's Trace Precedents and Trace Dependents do, a model answers what a cell reads and what
reads it, from the formulas' source, before anything is calculated:

```python
model.precedents("net_income", period="FY2025")             # [ebit[period=FY2025], tax_rate[period=FY2025]]
model.dependents("revenue", period="FY2025")                # formula cells and check runs that read it
model.precedents("net_income", deep=True, period="FY2025")  # each with its own, down to the entered cells
```

- Each is a list of `tm.Trace`: `name` (the item, or the check id), `coords`, `kind` (`"cell"` or
  `"check"`), `children` (with `deep=True`), `flatten()`; `print()` shows the tree indented.
- Both arms of an `if`/`else` on a value count, so precedents can list a cell a run doesn't read;
  `report.explain()` lists the cells each run did read.
- An entered cell has no precedents, even when its item has a formula.

### Errors

All derive from `tm.TraceModelError`.

| Error | Raised when |
| --- | --- |
| `DefinitionError` | A dimension, item or check is invalid or conflicts with an existing one. |
| `UnknownItem` | The item name is not declared. |
| `UnknownCoord` | A label or dimension name is not declared. |
| `DimMismatch` | Coords miss one of the item's dims or add one it doesn't have. |
| `MissingValue` | No value at these coords. |
| `InvalidValue` | Wrong type for the item's `dtype`, or a bad `source`. |

## Saving a model

The code that builds a model runs once. Save the definition, and from then on load it:

```python
model.save("retail.model.json")          # dimensions, items, formulas, checks
model.to_jsonl("retail.values.jsonl")    # values, kept apart

model = tm.load("retail.model.json")
model.load_jsonl("retail.values.jsonl")
```

- The definition and the values are separate files, as in XBRL (taxonomy vs instance): one
  agent defines the model, another enters values against it.
- A formula or check is saved as its lambda's source, with the values it uses beside it:
  `"formula": "lambda x: max(0, FLOOR - x['cash'])", "env": {"FLOOR": 25000}`. Loading checks it
  against the formula allow-list and compiles it with RestrictedPython, as `add_item` does;
  nothing else in the file runs, and `allow_code` isn't needed (it is accepted and ignored, with
  a `DeprecationWarning`).
- Files saved by earlier versions load: expression text (`"cost * 0.6"`), checks saved per
  label (`expect_by_label`) and `{"python": ...}` formulas are converted to lambdas, and saving
  again writes the lambdas. A saved formula that used the model itself (`{"$model": true}`)
  raises: rewrite it to read cells through `x`.
- `tm.load(path, formulas="off")` loads the dimensions, items, checks and values but keeps
  formulas as text: nothing is compiled or run, formula cells have no value and checks don't
  run. Use it to read a file you don't trust; to calculate one, also run tracemodel in a sandbox
  such as [gVisor](https://gvisor.dev) (issue #7). RestrictedPython is one barrier, not a sandbox.
- `tm.ANY` is saved as `"ANY"`. Item names must be Python identifiers (`add_item` checks), and
  attrs must hold JSON values.
- Loading validates everything, as building the model in code does.

### What a formula may use

A formula is checked against an allow-list when it is added or loaded, then compiled with
RestrictedPython, which routes every attribute, `[]` and loop through tracemodel's guards:

- **Expressions only**: arithmetic, comparisons, `and`/`or`/`not`, `a if c else b`, calls,
  `[]`, list, dict and set literals and comprehensions. No statements, `def`, nested `lambda`,
  `:=`, f-strings, `*args`/`**kwargs`, `&`/`|`/`~` or `%` formatting of text.
- **Names**: the lambda's parameter, comprehension variables, the values it was written with,
  and `abs`, `all`, `any`, `bool`, `dict`, `enumerate`, `float`, `int`, `len`, `list`, `max`, `min`,
  `range`, `reversed`, `round`, `sorted`, `sum`, `tuple`, `zip`, `date`, `datetime`, `timedelta`
  and `math` (its arithmetic functions and constants).
- **Attributes**: only those listed for each kind of object (`x`, periods, dates, `.dt`, text,
  lists and dicts); never a name starting with `_`.
- **Size**: text, lists and `range` up to 100,000 items, whole numbers up to about 3,000 digits,
  a million loop steps per run; `9**9**9` or `[0] * 10**9` raise instead of hanging.

## Excel export

`model.to_excel(path)` writes the model for people who read models in Excel (needs
`pip install "pytracemodel[excel]"`):

```python
model.to_excel("apple.xlsx", title="Apple FY2024-FY2025 (Form 10-K)", sections={
    "Income statement": ["net_sales", "cost_of_sales", "gross_margin", "operating_expenses", "net_income"],
    "Balance sheet": ["balance_sheet"],
    "Cash flow": ["cash_flow"],
})
```

- **Grids.** Items are blocks one under the other, sharing the columns: `columns`, else
  `period`, else each item's last dim that isn't a hierarchy. A block's title is the item's
  `description` (else its name in words); its rows are its other dims, in the first column,
  hierarchies indented and grouped so they collapse. With more than one dim down, they nest
  there as a pivot table's compact form: each label of the other dims is a group row, with the
  hierarchy's tree beneath it (`version / contract`: Plan, then the contracts, then Actual, ...);
  a group with no values is left out. Items with only hierarchy dims go across instead, one
  column each.
- **Sections** group items, e.g. a statement each; items in no section group by their dims.
  With `layout="sheet"` the sections that share the columns go on one sheet, one after the
  other, each under a banner and, with `page_breaks`, on its own printed page; with
  `layout="sheets"` each section has its own sheet.
- **Cells** follow the modelling convention: blue is entered, black is calculated; a yellow
  fill is a value entered over a formula, a red fill a cell a failing check reads or found
  missing. Nil shows as `—`; a cell with no value is blank. Each cell's note gives its source,
  or its formula. `split_after=("month", "2026-03")` draws a line after a column, e.g.
  between actuals and forecast.
- **Tables** follow the grids: Dimensions, Items, Checks, Check results, and Values, every
  entered value with its source and a link to it. A Summary sheet comes first, with the check
  status, a legend and links.

The workbook is a report: it holds values, not Excel formulas, and isn't read back.

## Values file format

`to_jsonl` writes one object per value:

```json
{"line_item": "revenue", "coords": {"region": "/north", "period": "Q1"}, "value": 700, "source": {"document": "Q2 sales report", "url": "https://example.com/q2.pdf", "section": "Table 1"}}
```

A date is written as an ISO string (`"value": "2026-03-31"`). A nil cell is written with `"value": null`; a cell formula with `"formula": "lambda x: ..."` (and `"env"` when it uses values) instead of `"value"`. A value that overrides a formula adds `"overrides_formula": true`.

## Development

```
uv run pytest
```

## Appendix: Inspiration

| Project | What we take from it |
| --- | --- |
| [pandas](https://pandas.pydata.org/) | Date and datetime columns, ISO strings parsed as dates, date differences as timedeltas, the `.dt` accessor's field names and `to_period` labels. |
| [xarray](https://docs.xarray.dev/) | Named dimensions, coordinate labels, selection by label (`.sel`), broadcasting by dimension name. |
| [Great Expectations](https://docs.greatexpectations.io/) | Data quality rules as declared *expectations*, grouped into suites and run against the data; `severity` (critical / warning / info) and `mostly` (pass when a share of rows passes). |
| [XBRL Formula](https://www.xbrl.org/specification/validation/REC-2009-06-22/validation-REC-2009-06-22.html) | Value and consistency *assertions* over reported financial facts, with absolute and proportional acceptance radii; satisfied / unsatisfied with a severity. |

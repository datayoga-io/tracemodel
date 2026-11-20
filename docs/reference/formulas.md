# Formulas

## Formulas

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

## What a formula may use

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

# Model reference

## Model

```python
model = tm.Model()
```

**Definition**

| Call | Does |
| --- | --- |
| `add_dimension(name, coords, description=None, attrs=None)` | `coords` is a list of coord entries (see Coord entries), a `tm.Hierarchy`, or a time spine, `tm.period_range(...)` (see Time dimensions). Re-adding an identical dimension is a no-op; a different one under the same name raises `DefinitionError`. `description` is what people call the dimension ("Unit type"); the name stays what formulas use. `attrs` is a free-form dict about the dimension itself (a `"note"` for prose, anything else a reader or agent can use), as an item and a label have. |
| `update_dimension(name, *, description=..., attrs=...)` | Change a dimension's description or attrs (`None` clears); its labels don't change. Recorded as a `dimension.update` op, so it is versioned. |
| `add_coords(name, entries)` | Appends labels to a dimension, e.g. forecast periods (entries as in Coord entries; a hierarchy path needs its parent). Items get the new cells, not entered. Checks given `tm.ANY` or `tm.filter_by_attrs(...)` for that dimension are re-expanded to cover the new labels; checks with listed labels keep them. Labels already there with the same entry are a no-op. Labels can't be reordered; `remove_coords` removes them. A time dimension takes the periods that continue it: `add_coords("month", tm.period_range("2027-01", periods=12))`. |
| `remove_coords(name, labels)` | Removes labels, with the values entered at them. A time dimension loses periods from its end; a hierarchy path goes with its children. Checks over `tm.ANY` or `tm.filter_by_attrs(...)` shrink with the dimension. Refused, with nothing removed, while a formula reads a removed label by name or a check lists one in `coords`. |
| `remove_dimension(name)` | Removes a dimension that no item uses; raises `DefinitionError` naming the items that do. |
| `add_item(name, dims, dtype=float, attrs=None, formula=None, description=None)` | `dims`: tuple of declared dimension names. `dtype`: `int`, `float`, `str`, `bool`, `date` or `datetime` (as types or names; see Text, Yes/no and Dates). `attrs`: free-form dict; `units` and `scale` are shown in tables. By convention `period_type` is `"duration"` (a flow over the period) or `"instant"` (a balance at its end), for whoever reads the model; the library doesn't act on it. `formula`: a lambda of `x`, or its source text, that calculates the item's cells (see Formulas). `description`: the item's title as people read it, e.g. the caption the source prints ("Lease-related assets and liabilities"), as a dimension label has one; `name` stays the code formulas use. Re-adding an identical item is a no-op. |
| `set_formula(name, formula)` | Changes an item's formula; `None` removes it. Values entered over it stay. Checked as `add_item` checks a formula; a refused formula changes nothing. |
| `update_item(name, *, attrs=..., description=...)` | Changes an item's attrs or description; an argument not given is kept. Dims and dtype don't change: remove the item and add it again. |
| `remove_item(name)` | Removes an item with its values. Refused, with nothing removed, while a formula or a check reads it; in a transaction, checked at commit, so items that read each other can go together. |
| `add_check(id, expect, coords=None, description=None, severity="error")` | `expect`: a lambda of `x`, or its source text, that returns `True` or `False` (see Data quality). `coords`: `{dim: tm.ANY \| tm.filter_by_attrs(...) \| label \| [labels]}` — the check runs once per combination. Every dimension a cell it reads leaves open must appear here, and every dimension here must be left open by some cell (otherwise `DefinitionError`). When every cell fixes all its coords, omit `coords`: the check runs once. `severity`: `"error"` or `"warning"`; a failing warning is reported but doesn't make `report.ok` false. An existing `id` is replaced. |
| `remove_check(id)` | Removes a check. |
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
| `set_value(line_item, coords, value, source=None)` | `coords` must give exactly one declared label for each of the item's dims. `value` must match the item's `dtype` (`int` items reject non-integers; `str` items take text; date items take a date or an ISO string), or `tm.NIL` (see below), or a lambda (or its text, `"lambda x: ..."`, except on a `str` item, where text is the value): a formula for this one cell (see Formulas). `source`: optional, see Source. The last write wins: setting a cell again replaces its value and source, and the old ones are kept in the entry's `history`. |
| `clear_value(line_item, coords)` | Removes a cell's entered value, so it is not entered again (unlike `tm.NIL`, which says the source shows nothing). On a formula item, the item's formula calculates the cell again. |
| `get(line_item, **coords)` | The value at full coords: a number, or a date for a date item (0 for a nil cell; `tm.NIL` for a nil date). |
| `entry(line_item, **coords)` | With full coords: an `Entry` (`value`, `source`, `history`). With fewer coords: a `Table` of matching entries. |
| `cells(items=None, /, **coords)` | Many cells at once, one row each (`item`, `coords`, `value`, `kind`, `source`), as a `Table`. `coords` fixes some dims (an item's other dims run over all their labels); `items` names the items (default: every item that has all the dims in `coords`). `model.cells(use="offices")` reads every item at that label. `kind` is `entered`, `calculated` or `missing`; a cell with no value is `missing` (value `None`) and does not raise. |
| `table(line_item)` | The item as a grid: `period` across (if the item has it), other dims down. Cells not entered are blank; nil cells show `—`. |
| `evaluate(formula)` | Runs a lambda of `x` for every combination of the dims its cells leave open, e.g. `model.evaluate(lambda x: x["revenue"] - x["cost"])`: a `Table`, or a number when no dim is open. |
| `precedents(line_item, deep=False, **coords)` / `dependents(...)` | What a cell's formula can read, and what can read the cell; see Tracing. |
| `check()` | Runs every check; returns a `Report`. |
| `transaction()` | Groups changes; see Transactions. |
| `recording()` | Records every change as a `tm.Op`; see Recording changes. |
| `to_jsonl(path)` / `load_jsonl(path)` | Save / load values, one JSON object per value. Loading validates like `set_value`, in one transaction: a bad row loads nothing. |
| `to_excel(path, *, title="Model", sections=None, layout="sheet", columns=None, split_after=None, page_breaks=True)` | Writes the model as an Excel workbook; see Excel export. |

**Saving the definition**

| Call | Does |
| --- | --- |
| `save(path)` | Writes the definition (dimensions, items, formulas, checks) to a JSON file. Values are not included; save them with `to_jsonl`. |
| `tm.load(path, formulas="on")` | A model from a file written by `save`. Formulas are checked and compiled as when they were added; `formulas="off"` keeps them as text and runs none (see Saving a model). |
| `to_dict()` / `tm.Model.from_dict(data, formulas="on")` | The same, as plain data. |

**Source.** A value may carry a `source`: a dict saying where it came from, with any keys you
choose, e.g. `{"document": "10-K", "page": 157, "bbox": [72, 100, 300, 120], "sha256": "ab12"}`.
Values must be plain data (text, numbers, true/false, none, lists and dicts of those; write a date
as ISO text), so a source saves and reloads as it was. It is optional and the library checks no
key, so agree on yours; `entry()` and the Excel notes show `document`, `section` and `page`, and
the Values sheet has a column for every key. Defining which keys are required, and what a model
refuses (for example a manual entry with no source), is on the backlog.

## Coord entries

Every dimension, plain or hierarchy, lists its coords as entries of one of three forms:

| Entry | Example |
| --- | --- |
| `"code"` | `"Q1"`, `"/north"` |
| `(code, description)` | `("Q1", "First quarter")` |
| `(code, description, attrs)` | `("Q1", "First quarter", {"end": "2026-03-31"})` |

`attrs` is a free-form dict. Use `None` as the description to give attrs only.
`dim[label]` returns the label as an object (`tm.Label`): `dim["Q1"].description` is its description,
falling back to the code (for a hierarchy, the last path segment), and `dim["Q1"].attrs` its attrs,
read-only, or `{}`. A `Label` is a `str`, so it compares, hashes and prints as its code and goes
wherever a label string does; `dim.labels` lists them. (`dim.description(label)` and
`dim.attrs(label)` still work and warn: use the label.)

## Hierarchy

```python
tm.Hierarchy(["/", "/a", ("/a/x", "Description"), ("/b", "B", {"note": "..."})])
```

- Entries as above; each code is a path.
- Every path's parent must also be declared, except the root `/`: top-level paths such as
  `/a` and `/b` may stand alone.
- `h.parent(path)`, `h.children(path)`, `h.description(path)` (falls back to the last path segment).
- Reach it with `model.dimensions["region"].hierarchy`.

## Selecting and showing cells

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

## Results and display

- `Report`: `ok` (no failing error check), `filter(check=None, **coords)` (the runs of one check and/or on some labels: a label, a list, `tm.ANY` or `tm.filter_by_attrs(...)`; returns a `Report`), `results`, `failures` (every run that didn't pass), `errors` and `warnings` (failures by severity), `explain(check_id=None)` (each run with every cell it read), `table()`, `show()`.
- Each result: `check`, `at` (the coords of the run), `lhs`, `rhs`, `diff`, `passed`, `cells`, `missing`, `error`.
- `lhs`, `rhs` and `diff` are set for a check that is one comparison, else `None`. `error` holds the error text when the check raised or didn't return a bool.
- A run fails with `error` set, instead of `check()` raising, when a cell it reads can't be calculated: say a formula's `lag` before the first period, or an `int` formula that gives a fraction.
- A missing value fails the check that reads it; it is never treated as 0.
- Each run's result: `pass`, `FAIL`, `missing`, or for a warning check `warn` / `warn: missing`.
- `Table`: `columns`, `rows`, `title`, `to_records()`, `to_pandas()` (needs `pip install "pytracemodel[pandas]"`), `show()`. Renders as HTML in Jupyter and as aligned text elsewhere.

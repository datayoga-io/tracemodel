# Saving and loading

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

## Values file format

`to_jsonl` writes one object per value:

```json
{"line_item": "revenue", "coords": {"region": "/north", "period": "Q1"}, "value": 700, "source": {"document": "Q2 sales report", "url": "https://example.com/q2.pdf", "section": "Table 1"}}
```

A date is written as an ISO string (`"value": "2026-03-31"`). A nil cell is written with `"value": null`; a cell formula with `"formula": "lambda x: ..."` (and `"env"` when it uses values) instead of `"value"`. A value that overrides a formula adds `"overrides_formula": true`.

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

## Keeping many models in a store

A `Store` keeps any number of named models and gives one back as a `Repo`. The DuckDB backend keeps them in one file:

```python
from tracemodel.store.duck import DuckStore      # pip install "pytracemodel[duckdb]"

with DuckStore("models.duckdb", write=True) as store:
    store.import_model("retail", model.to_dict(), rows)   # rows: the lines of model.to_jsonl
    store.import_run("runs/2026-q3")                      # a folder with model.json and values.jsonl

with DuckStore("models.duckdb") as store:                 # read-only unless write=True
    store.models()                                        # name, title and size of each
    repo = store.repo("retail")                           # a Repo, with one commit for each commit stored
```

- **An update is an insert.** A cell's value is its row in the latest commit, so nothing is overwritten and a restated
  cell keeps its earlier values (`repo.history`).
- **One writer at a time**, and any number of readers. To back up, copy the file when no writer is open.
- **Another backend** is one class: subclass `tracemodel.store.Store`, fill in `models`, `definition`, `cells`,
  `commits` and `import_model`, and you get `repo` and `import_run` for free. Code that takes a `Store` does not
  change.

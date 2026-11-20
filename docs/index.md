# tracemodel

[![PyPI](https://img.shields.io/pypi/v/pytracemodel)](https://pypi.org/project/pytracemodel/)
[![Python](https://img.shields.io/pypi/pyversions/pytracemodel)](https://pypi.org/project/pytracemodel/)
[![License](https://img.shields.io/github/license/datayoga-io/tracemodel)](https://github.com/datayoga-io/tracemodel/blob/main/LICENSE)
[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/datayoga-io/tracemodel/blob/main/apple.ipynb)

tracemodel lets you build a financial model in Python in which every number has a fixed address,
a source you can open, and rules that check it.

```bash
pip install pytracemodel
```

**In 30 seconds.** tracemodel keeps the numbers of a financial model together with where each one came from.
You declare the axes of the model (dimensions), what you measure (items), and enter the numbers (values) with their
source. A formula calculates the rest.

```python
import tracemodel as tm

model = tm.Model()
model.add_dimension("period", ["Q1", "Q2"])
model.add_dimension("region", ["north", "south"])

# Items: what we measure, and over which dimensions. profit is calculated, not entered.
model.add_item("revenue", dims=("region", "period"), dtype=float)
model.add_item("cost", dims=("region", "period"), dtype=float)
model.add_item("profit", dims=("region", "period"), dtype=float, formula=lambda x: x["revenue"] - x["cost"])

# Facts: each number is entered with where it came from.
source = {"document": "Q2 sales report", "page": 4}
facts = {("north", "Q1"): (700, 450), ("north", "Q2"): (760, 480),
         ("south", "Q1"): (500, 320), ("south", "Q2"): (545, 350)}
for (region, period), (revenue, cost) in facts.items():
    model.set_value("revenue", {"region": region, "period": period}, revenue, source)
    model.set_value("cost", {"region": region, "period": period}, cost, source)

```

Now read the model:

```python
model.get("profit", region="north", period="Q2")   # 280, calculated from revenue and cost
model.table("profit")                              # regions down, periods across
model.entry("revenue", region="north", period="Q2").source   # where an entered number came from
```

```text
280

profit
region   Q1   Q2
north   250  280
south   180  195

{'document': 'Q2 sales report', 'page': 4}
```

Every number has an address (item, region, period), an entered number has a source, and a calculated number has the
formula that made it. You can also declare rules the numbers must obey, such as "the total equals North + South", and
the model checks them and shows which numbers broke a rule; see [Data quality](reference/checks.md). A complete model is built step by step in
[Example](example.md). To run a model of Apple's FY2024–FY2025 10-K, open
[apple.ipynb in Colab](https://colab.research.google.com/github/datayoga-io/tracemodel/blob/main/apple.ipynb).

**Compared with Excel and pandas.**

| | Excel | pandas / xarray | tracemodel |
|---|---|---|---|
| Source on each number | no | no | yes: document, page, any keys |
| Formulas | per cell, can be overwritten with no trace | code, not stored with the data | per item or per cell, saved as text and traced |
| Checks that run on every period | manual | manual | declared once, run per label |
| Trace a number to its inputs | Trace Precedents | no | `precedents()`, `dependents()`, `explain()` |
| Version control and review | no | no | commits, branches, accept or reject per value |
| AI agent writes values | free-form | free-form | validated against a fixed definition |
| Output to Excel | — | yes | yes: `to_excel()` |

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

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

**In 30 seconds.** tracemodel keeps the numbers of a financial model together with where each one came from, and
checks them against rules you declare. When a rule fails, it shows which numbers broke it and their sources, so a
person can find the mistake and fix it. Here is a small case: revenue from a sales report, for Q1 and Q2, as a total
and as North and South, with the rule that the total equals North + South. The code defines the model, enters the
numbers with their source, and runs the check.

```python
import tracemodel as tm

model = tm.Model()
model.add_dimension("period", ["Q1", "Q2"])
model.add_dimension("region", tm.Hierarchy(["/", "/north", "/south"]))
model.add_item("revenue", dims=("region", "period"), dtype=float)

# The rule: in every period, the total equals North + South, give or take 0.5 for rounding.
model.add_check(
    "DQ-1",
    coords={"period": tm.ANY},
    expect=lambda x: abs(x["revenue"]["/"] - (x["revenue"]["/north"] + x["revenue"]["/south"])) <= 0.5,
)

# The numbers from the sales report (Q1, Q2), each entered with where it came from.
source = {"document": "Q2 sales report", "page": 4}
for region, q1, q2 in [("/", 1200, 1350), ("/north", 700, 760), ("/south", 500, 545)]:
    model.set_value("revenue", {"region": region, "period": "Q1"}, q1, source)
    model.set_value("revenue", {"region": region, "period": "Q2"}, q2, source)

report = model.check()
report.show()
```

```text
1/2 check runs passed
check  coords     result  left  rule  right  diff
DQ-1   period=Q1  pass       0  <=      0.5  -0.5
DQ-1   period=Q2  FAIL      45  <=      0.5  44.5
```

The rule ran once for each period. Q1 passes. Q2 fails: the total is 1,350, but North + South is 760 + 545 = 1,305.
`left` is the gap between the total and the sum of its parts, and `right` is the gap the rule allows. To see why, ask
for an explanation:

```python
print(report.filter(period="Q2").explain())
```

```text
DQ-1  period=Q2  FAIL  left 45  <=  right 0.5  diff 44.5
    abs(x['revenue']['/'] - (x['revenue']['/north'] + x['revenue']['/south'])) <= 0.5
      revenue[region=/, period=Q2] = 1,350
      revenue[region=/north, period=Q2] = 760
      revenue[region=/south, period=Q2] = 545
```

`explain()` shows the rule and every cell it read. Each value carries its source, so a reviewer opens page 4 of the
sales report and fixes the number that is wrong. The full model is built step by step in [Example](example.md). To run a
complete model of Apple's FY2024–FY2025 10-K, open
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

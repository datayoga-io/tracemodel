# tracemodel

[![PyPI](https://img.shields.io/pypi/v/pytracemodel)](https://pypi.org/project/pytracemodel/)
[![Python](https://img.shields.io/pypi/pyversions/pytracemodel)](https://pypi.org/project/pytracemodel/)
[![License](https://img.shields.io/github/license/datayoga-io/tracemodel)](https://github.com/datayoga-io/tracemodel/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-datayoga--io.github.io%2Ftracemodel-blue)](https://datayoga-io.github.io/tracemodel/)
[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/datayoga-io/tracemodel/blob/main/apple.ipynb)

tracemodel lets you build a financial model in Python in which every number has a fixed address,
a source you can open, and rules that check it. It also gives AI agents a fixed definition to fill: the agent
writes values, and the model keeps them consistent and checks them.

```bash
pip install pytracemodel
pip install "pytracemodel[excel]"    # with model.to_excel
```

Python 3.12 or later, one dependency. [Install notes](https://datayoga-io.github.io/tracemodel/install/).

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
sales report and fixes the number that is wrong. The full model is built step by step in [Example](https://datayoga-io.github.io/tracemodel/example/). To run a
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

## Documentation

Full docs: **[datayoga-io.github.io/tracemodel/](https://datayoga-io.github.io/tracemodel/)**

| Read | To learn |
| --- | --- |
| [Concepts and workflow](https://datayoga-io.github.io/tracemodel/concepts/) | Dimensions, items, checks and values, and the order to build them in |
| [Example](https://datayoga-io.github.io/tracemodel/example/) | A retail chain's sales by region and quarter, start to finish |
| [Model reference](https://datayoga-io.github.io/tracemodel/reference/model/) | Every `Model` call, selecting and showing cells |
| [Data quality](https://datayoga-io.github.io/tracemodel/reference/checks/) | Checks, tolerance and `explain()` |
| [Formulas](https://datayoga-io.github.io/tracemodel/reference/formulas/) | Per-item and per-cell formulas, and what a formula may use |
| [Dates, text and missing values](https://datayoga-io.github.io/tracemodel/reference/values/) | Date and text items, time dimensions, nil vs not entered |
| [Tracing and errors](https://datayoga-io.github.io/tracemodel/reference/tracing/) | `precedents()`, `dependents()`, and the errors raised |
| [Saving and loading](https://datayoga-io.github.io/tracemodel/guide/saving/) | Model and values files, Excel export |
| [Versions, branches and review](https://datayoga-io.github.io/tracemodel/guide/versions/) | Commits, branches, `review`, `approve`, `diff` |
| [Working with AI agents](https://datayoga-io.github.io/tracemodel/agents/) | Two agents: one writes the definition, one fills in values |
| [Changelog](https://datayoga-io.github.io/tracemodel/changelog/) | What changed in each release |

## Contributing

See [CONTRIBUTING.md](https://github.com/datayoga-io/tracemodel/blob/main/CONTRIBUTING.md). Licensed under [Apache 2.0](https://github.com/datayoga-io/tracemodel/blob/main/LICENSE).

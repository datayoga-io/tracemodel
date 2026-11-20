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
pip install pytracemodel     # Python 3.12 or later; add "[excel]" for model.to_excel
```

**In 30 seconds.** You declare the axes of the model (dimensions), what you measure (items), and enter the numbers
(values) with their source. A formula calculates the rest.

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

# Read it back.
model.get("profit", region="north", period="Q2")             # 280, calculated from revenue and cost
model.entry("revenue", region="north", period="Q2").source   # {'document': 'Q2 sales report', 'page': 4}
model.table("profit")                                        # regions down, periods across:
# profit
# region   Q1   Q2
# north   250  280
# south   180  195
```

You can also declare rules the numbers must obey, such as "the total equals North + South". The model checks them and
shows which numbers broke a rule ([Data quality](https://datayoga-io.github.io/tracemodel/reference/checks/)). A complete model is built step by step in the
[Example](https://datayoga-io.github.io/tracemodel/example/), and Apple's FY2024–FY2025 10-K runs in
[Colab](https://colab.research.google.com/github/datayoga-io/tracemodel/blob/main/apple.ipynb).

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

**[datayoga-io.github.io/tracemodel](https://datayoga-io.github.io/tracemodel/)**: [Concepts](https://datayoga-io.github.io/tracemodel/concepts/), [Example](https://datayoga-io.github.io/tracemodel/example/),
[Model reference](https://datayoga-io.github.io/tracemodel/reference/model/), [Data quality](https://datayoga-io.github.io/tracemodel/reference/checks/), [Formulas](https://datayoga-io.github.io/tracemodel/reference/formulas/),
[Dates, text and missing values](https://datayoga-io.github.io/tracemodel/reference/values/), [Tracing and errors](https://datayoga-io.github.io/tracemodel/reference/tracing/),
[Saving and loading](https://datayoga-io.github.io/tracemodel/guide/saving/), [Versions, branches and review](https://datayoga-io.github.io/tracemodel/guide/versions/),
[Working with AI agents](https://datayoga-io.github.io/tracemodel/agents/), [Changelog](https://datayoga-io.github.io/tracemodel/changelog/).

Contributing: [CONTRIBUTING.md](https://github.com/datayoga-io/tracemodel/blob/main/CONTRIBUTING.md). Licensed under [Apache 2.0](https://github.com/datayoga-io/tracemodel/blob/main/LICENSE).

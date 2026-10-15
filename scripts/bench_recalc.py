"""Time a 1,000-line x 60-month model: build, full calculation, and one change then check().

    uv run python scripts/bench_recalc.py            # this checkout
    python scripts/bench_recalc.py --src OTHER/src   # another checkout's tracemodel, to compare

Runs with formulas as lambdas when tracemodel has tm.Formula, else with the expressions
(model["revenue"] * 0.6) older versions took.
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time

LINES, MONTHS = 1_000, 60
SRC = {"document": "Budget", "url": "https://example.com/budget.pdf", "section": "1"}


def build(tm):
    m = tm.Model()
    m.add_dimension("line", [f"L{i:04}" for i in range(LINES)])
    m.add_dimension("period", [f"M{i:02}" for i in range(MONTHS)])
    m.add_item("revenue", ("line", "period"))
    if hasattr(tm, "Formula"):
        m.add_item("cost", ("line", "period"), formula=lambda x: x["revenue"] * 0.6)
        m.add_item("margin", ("line", "period"), formula=lambda x: x["revenue"] - x["cost"])
        m.add_check("COST-BELOW-REVENUE", coords={"line": tm.ANY, "period": tm.ANY},
                    expect=lambda x: x["cost"] <= x["revenue"])
    else:
        m.add_item("cost", ("line", "period"), formula=m["revenue"] * 0.6)
        m.add_item("margin", ("line", "period"), formula=m["revenue"] - m["cost"])
        m.add_check("COST-BELOW-REVENUE", coords={"line": tm.ANY, "period": tm.ANY}, expect=m["cost"] <= m["revenue"])
    m.add_item("cum", ("line", "period"),
               formula=lambda x: x["margin"] + (x.lag("period") if x.coords["period"] != "M00" else 0))
    with m.transaction():
        for i, j in itertools.product(range(LINES), range(MONTHS)):
            m.set_value("revenue", {"line": f"L{i:04}", "period": f"M{j:02}"}, 100 + i + j, SRC)
    return m


def calculate_all(m):
    for item in ("cost", "margin", "cum"):
        for line, p in itertools.product(m.dimensions["line"].coords, m.dimensions["period"].coords):
            m.get(item, line=line, period=p)


def timed(label, fn):
    start = time.perf_counter()
    result = fn()
    print(f"{label:<34}{time.perf_counter() - start:8.2f} s")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", help="a tracemodel src directory to import instead of the installed one")
    args = parser.parse_args()
    if args.src:
        sys.path.insert(0, args.src)
    import tracemodel as tm

    print(f"tracemodel from {tm.__file__}")
    m = timed("build + enter 60,000 values", lambda: build(tm))
    timed("full calculation (180,000 cells)", lambda: calculate_all(m))
    timed("check() (60,000 runs)", m.check)
    m.set_value("revenue", {"line": "L0500", "period": "M30"}, 1, SRC)
    timed("one change, then check()", m.check)
    m.set_value("revenue", {"line": "L0500", "period": "M00"}, 2, SRC)
    timed("one change, then the last cum", lambda: m.get("cum", line="L0500", period="M59"))


if __name__ == "__main__":
    main()

# Data quality

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

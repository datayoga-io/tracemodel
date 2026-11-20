# Tracing and errors

## Tracing: precedents and dependents

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

## Errors

All derive from `tm.TraceModelError`.

| Error | Raised when |
| --- | --- |
| `DefinitionError` | A dimension, item or check is invalid or conflicts with an existing one. |
| `UnknownItem` | The item name is not declared. |
| `UnknownCoord` | A label or dimension name is not declared. |
| `DimMismatch` | Coords miss one of the item's dims or add one it doesn't have. |
| `MissingValue` | No value at these coords. |
| `InvalidValue` | Wrong type for the item's `dtype`, or a bad `source`. |

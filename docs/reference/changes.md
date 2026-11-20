# Transactions and recording

## Transactions

Filling in a model takes many calls, and some states in between aren't valid yet: a cell
formula can read a cell that, for now, reads it back. Group the calls in a transaction:

```python
with model.transaction():
    for year in ["FY2026", "FY2027"]:
        model.set_value("fee", coords={"contract": "C-101", "year": year},
                        value=lambda fee: fee.lag("year") * 1.05, source=MSA)
# committed here
```

- Checks that need the whole picture (circular formulas) run once, at commit.
- If the commit fails, or an exception leaves the block, every change made inside it is undone:
  values, items, labels and checks.
- Reads inside the transaction (`get`, `table`, `check`) see the changes so far; reading a cell
  that loops for now raises `DefinitionError`.
- Without `with`: `tx = model.transaction()`, then `tx.commit()` or `tx.rollback()`. One
  transaction at a time.

Calculated cells are cached, so a long chain of formulas (years of monthly periods, each
reading the one before) is calculated once per cell. As a spreadsheet does, the model records
which cells each formula read; a new value recalculates only the cells that read it, directly
or through other formulas, and `check()` re-runs only the runs that read those. Changing a
definition (an item, a dimension, its labels) recalculates everything. The values a formula
uses from where it was written are copied when it is added, so changing them later doesn't
change the formula.

## Recording changes

Every verb that changes a model records one `tm.Op` while the model is recording: what changed
(`kind`, e.g. `"value.set"` or `"item.remove"`), the object (`key`), and the object before and
after (`old`, `new`) as plain data. A verb made of others records theirs first: `remove_item`
records a `value.clear` per value, then `item.remove`. A refused verb, or a transaction that
rolls back, records nothing.

```python
with model.recording() as ops:
    model.set_value("price", {"term": "T1"}, 10, SRC)
tm.ops.replay(other, ops)                       # the same changes, checked by the same verbs
for op in reversed(ops):
    tm.ops.apply(model, tm.ops.invert(op))      # undone
json.dumps([op.to_data() for op in ops])        # ops are plain data; tm.Op.from_data reads one back
```

Versioning stores these ops in its log.

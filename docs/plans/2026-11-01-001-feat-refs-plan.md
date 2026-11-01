---
title: Pointers between dimensions in tracemodel
type: feat
date: 2026-11-01
origin: none (design discussion; prior art in Anaplan and Pigment)
---

# Pointers between dimensions in tracemodel

## Problem

Real models have entities that relate: a payment line belongs to an engagement, an engagement to a
project and to a contract. Today a relation can be a path hierarchy (`/project:A/engagement:E1`),
which gives one parent per label, bakes the parent into the label, and makes moving an entity under
another parent a remove-and-add that drops its values. It can't give an entity two parents (an
engagement has a project and a contract), and it can't make each entity a dimension with exactly its
own items.

The alternative is flat dimensions, one per entity, and an item on the child whose value is a label of
the parent. A formula needs to follow that pointer, and a view needs to group rows through it.

## Decision

- One flat dimension per entity: `project`, `contract`, `engagement`, `line`. Each has its own items.
- A relation is an item on the child dimension with type `tm.Ref("parent_dim")`. Its value is a label
  of the parent dimension, entered with a source and a history like any value. Changing one is a
  value change: it shows in `diff` and review, and it can vary over time by adding a dimension to the item.
- Grouping is a view concern. A table or grid nests rows by following pointers, so `project >
  engagement > line` and `contract > engagement > line` are two layouts of the same data.
- Many-to-many (an engagement shared by projects with a percentage) is an ordinary item over both
  dimensions with a check that the shares add to 100. It needs no library change. The model here
  doesn't use it: an engagement belongs to one project, and a contract covering two projects has an
  engagement for each.
- Not needed any more: typed path labels, a `kinds` schema, restricting an item to a level, and
  `x.ancestor`. Each entity has its own dimension.

## What the prior art does

- Anaplan: a list-formatted property or line item holds the pointer. `SUM(source, mapping)`
  aggregates through it, `LOOKUP` goes forward through it. Many-to-many is a mapping module keyed by
  both lists with a percentage line item and a sum-check module. Parent properties are reached by a
  lookup property on the child.
- Pigment: a dimension-type property (static) or metric (can change over time) is the pointer. The
  `SELECT` modifier aggregates through it. Splits by percentage use an allocation-rule metric.
- TM1: several hierarchies over the same leaves, and a hierarchy generated from an attribute.

## Tested before the design was settled

Run against the library at `main`, in scratch scripts:

| Works | Fails |
| --- | --- |
| A rollup with `x.children`, one level and recursive | `.sel(dim=<a value>)`: "the cells a formula reads must be fixed" |
| `x.parent` with a guard for top-level labels, and chains through intermediate items | `x.parent(...).parent(...)` in one step |
| A many-to-many allocation item with an explicit `0` or `tm.NIL` for "not allocated", and a sum-to-100 check | A sparse allocation matrix: a missing cell fails the formula that reads it |

## Steps

### 1. `tm.Ref(dim)` as an item type (prototyped)

- `add_item(name, dims, dtype=tm.Ref("project"))`, or `"ref:project"`. The item's `dtype` stays `str`;
  `Item.ref` holds the dimension.
- `set_value` checks the value is a label of that dimension; the error lists some labels.
- Saved in the definition as `"ref"`, replayed by `tm.ops.apply`, shown by `print(model)`.
- `remove_coords` refuses while a pointer names a removed label (restrict), and `remove_dimension`
  refuses while a pointer's target is the dimension.
- Not done: a formula that returns a label for a `Ref` item isn't checked against the dimension.

### 2. Follow a pointer in a formula (prototyped)

```python
model.add_item("amount", ("line",), formula=lambda x: x["rate"] * x["factor"].sel(
    project=x["engagement_project"].sel(engagement=x["line_engagement"])))
```

- `.sel(dim=<a cell holding a label>)` reads the cell, then selects with its value. Any `str` item
  works; a `Ref` item also validates.
- The walk records the pointer cell as read, and the target as "any label of that dim" (`Cell.by_value`),
  so the dependencies stay static and coarse: the target item, along that dim.
- A pointer cell with no value makes the formula's cell missing. A pointer item that isn't `str` is
  refused when the formula is defined.
- A formula that reads itself through a pointer is refused as circular, since the target is coarse.
- `precedents` and `dependents` show the coarse sets.

### 3. Reverse aggregation

`x.sum_by("line", via=("line_engagement", "engagement_project"), item="payment_amount")`: the sum of an
item over the members of a dimension whose pointer chain ends at this cell. Honors the item's
"adds up" setting, and never sums a percentage or a share.

### 4. Finer tracing

Evaluation already records the exact reads; `precedents`, `dependents` and the "why did this figure
move" view should prefer them to the coarse sets once a cell has been calculated.

### 5. Grouped tables and Excel export

`model.table("payment_amount", rows=["project", "engagement", "line"])` nests rows by following
pointers, with totals through the same aggregation as step 3. `to_excel` takes the same `rows`.

### 6. Diff and review

A changed pointer is a value change today. Make `diff(..., calculated=True)` and `repo.review` say
which figures moved because a pointer moved, using the traces from step 4.

## Open questions

1. Do contracts carry attributes of their own (a counterparty, a signing date, a document)? If so the
   contract is its own dimension with items, and engagements point at it. If it is only an identifier
   used to group, a text item is enough.
2. Should a formula that returns a label for a `Ref` item be validated?
3. Do we want a named reverse (`project.engagements`) as sugar on the pointer, or only the
   aggregation call?
4. Should a missing pointer show a row under "(none)" in grouped tables, or drop it?

## Risks

- Coarse dependencies make `dependents` over-report: any line may read any project's factor. A cell
  that reads itself through a pointer is refused even when the data would never loop.
- Tracing, review and Excel all learn about pointers: steps 4 to 6 touch each.
- A pointer's value is data, so an edit can change many calculated figures at once. That is the
  point, and review needs to show it.

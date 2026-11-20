# Concepts and workflow

## Concepts

| Concept | What it is |
| --- | --- |
| **Dimension** | A named axis with an ordered list of coordinate labels, e.g. `period`: `Q1`, `Q2`. |
| **Coords** | The labels along a dimension. A value is addressed by one label per dimension: `{"region": "/north", "period": "Q1"}`. |
| **Hierarchy** | A dimension whose labels are paths: `/`, `/north`, `/north/boston`. A path's parent is the path minus its last segment. |
| **Item** | A named measure (e.g. `revenue`) declared over some dimensions, with a data type and attributes such as units. Holds no numbers until values are set. |
| **Formula** | A Python lambda of `x`, the cell being calculated: `lambda x: x["revenue"] * 0.6`. The same lambda is checked, saved and loaded. |
| **Check** | A data quality rule: a lambda of `x` that returns `True` or `False`, run once for each label of the dimensions you choose. |
| **Value** | One number for one item at full coords, with a `source` saying where it came from. |

## Workflow

A model is built in four steps, in this order:

1. **Dimensions** — `model.add_dimension(...)`
2. **Items scaffold** — `model.add_item(...)`
3. **Data quality** — `model.add_check(...)`
4. **Values** — `model.set_value(...)`, then `model.check()`

Steps 1–3 are the *definition*: what can exist and what must hold. Step 4 fills it in.
They can be done by different people or agents: whoever writes values only calls
`set_value`, and every call is validated against the definition.

To run steps 1–3 and step 4 as two AI agents, see [the agents guide](agents.md).

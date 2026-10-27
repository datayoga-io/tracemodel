# Changelog

## Unreleased

- **Recording changes:** `with model.recording() as ops:` records every change as a `tm.Op`
  of plain data, which `tm.ops.replay` applies to another model through the same verbs and
  `tm.ops.invert` undoes.
- **Confidence and reasoning on a value:** `set_value(..., meta={"confidence": ..., "reasoning":
  ..., "origin": ...})`. `model.inputs(...)` lists the entered values a figure rests on and
  `model.confidence(...)` gives the weakest of theirs, so the assumptions behind a figure can be
  found. A source may cite a document's `sha256`.
- **Verbs that undo:** `remove_item`, `set_formula`, `update_item`, `remove_check`,
  `clear_value` and `remove_coords`. Each refuses, with nothing changed, while a formula or a
  check still reads what it would remove; in a transaction that is checked at commit.
- **Text items:** `add_item(..., dtype=str)` (or `"str"` / `"string"`) holds sourced text, such
  as a contract's status or vendor. Text compares and joins in formulas and checks, `.str` gives
  pandas-named methods (`contains`, `lower`, `len`, ...), empty text is rejected in favour of
  `tm.NIL`, and Excel writes it as text. Comparing text with a number now raises
  `DefinitionError` instead of being `False` or a `TypeError`.
- `to_excel` nests every dim down in the first column, as a pivot table's compact form.

## 0.2.0 — 2026-10-15

One kind of formula. Every formula and check is a lambda of `x`, checked against an allow-list
and compiled with [RestrictedPython](https://github.com/zopefoundation/RestrictedPython), the new
and only dependency.

**Breaking**

- Arithmetic and comparisons on `model["item"]` raise `DefinitionError`, showing the lambda to
  write: `formula=model["revenue"] * 0.6` becomes `formula=lambda x: x["revenue"] * 0.6`, and
  `expect=model["a"] >= 0` becomes `expect=lambda x: x["a"] >= 0`. `model["item"]` still selects
  cells and shows them as a table.
- Checks are lambdas of the run, `x`. A function whose parameters are dims (`lambda region: ...`)
  is refused; a rollup reads `x.children(dim)`.
- `and`, `or`, `not` and chained comparisons replace `&`, `|` and `~`.
- A `def` formula is refused: a formula is one lambda.
- `tracemodel.text`, `Condition` and the expression classes are removed, with `Check.params` and
  `Check.conditions`.
- Closure values must be data (numbers, strings, lists and dicts of them, dates); a closure over
  the model or a cell is refused when the formula is added. They are copied then, so changing
  them later doesn't change the formula.
- A formula may use only the allow-listed Python (see "What a formula may use" in the README).
  A 0.1 `{"python": ...}` formula outside it, or one that used the model, an expression or a
  module other than `math`, must be rewritten as a lambda of `x`.
- A lambda's source is read when it is added; one with no source file (typed at a plain
  `python` prompt, built with `exec`) raises: pass its text instead.
- `Check.condition()` is removed. A check over a dict of lambdas (`LINES[x.coords[...]](x)`)
  becomes one `if`/`else` lambda.
- Definition files are `"format": "tracemodel/2"`, with formulas saved as lambdas; 0.2 reads
  `tracemodel/1` files too, and 0.1 refuses `tracemodel/2`.

**New**

- `model.precedents(item, deep=False, **coords)` and `model.dependents(...)`, as Excel's Trace
  Precedents and Trace Dependents, found from the formulas' source.
- Every formula is checked when added: unknown items and labels, open dims and circular
  formulas, with both arms of a branch counted. Inside a transaction, at commit, so a formula may
  read an item added later.
- `x.children(dim)`, `x.parent(dim)` and `x.labels(dim)`; `int(x["n"])`.
- `tm.load(path, formulas="off")` loads a file without compiling or running its formulas.
- `tm.Formula` and `tm.Trace`.

**Files**

- Formulas and checks are saved as their lambda's source, `"formula": "lambda x: ..."`, with
  `"env"` for the values they use. Loading needs no `allow_code`; the argument is ignored with a
  `DeprecationWarning`.
- Files from 0.1 load: expression text (with `date('...')` constants), `expect_by_label` checks
  and `{"python": ...}` entries that keep to the allow-list are converted to lambdas.

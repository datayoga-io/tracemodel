# tracemodel studio

A grid over a tracemodel model: an API and a web page, kept apart from the library. It opens on a
sample model (a development's payment forecast) and shows it the way the library holds it:
every value with its source, who entered it and what it replaced; every calculated cell with its
formula and what it read; the checks, and what fails them. Changes made in the grid are
uncommitted changes in a tracemodel `Repo`, until you commit them.

```
cd studio
uv run tracemodel-studio            # http://127.0.0.1:8000
uv run tracemodel-studio --port 8765 --author "your name"
uv run pytest                       # the studio's tests
```

The studio needs the pointer work in the library (`tm.Ref`, and formulas that follow a pointer),
which the sample uses.

## What's here

| Path | What it is |
| --- | --- |
| `src/studio/sample.py` | The sample model, built in a repo as a short history of commits by three authors: the dimensions, items with their formulas, the checks, and the values with their sources. The grid's tables and saved views. |
| `src/studio/service.py` | What the grid asks of a model, as plain data. Everything goes through the library's public API, except one read of a calculated cell's exact reads (`Model._deps`), noted in the code. |
| `src/studio/app.py` | The FastAPI app: the API below, and the page. |
| `src/studio/static/` | The page: `index.html`, `app.css`, `app.js`. No build step, no framework. The grid is [Tabulator](https://tabulator.info) 6.4.0 (MIT), kept in `static/vendor/` so the page works offline. |

## The API

| Call | Does |
| --- | --- |
| `GET /api/model` | The definition: dimensions with their labels, items (dimensions, kind, type, pointer target, unit, formula), checks, and the tables and views the grid offers. |
| `GET /api/cells` | Every cell of every item: its value (`null` when missing, with `nil` for a cell entered as nil), and the cells with changes not yet committed. |
| `GET /api/cell?item=&coords=` | One cell: its source and history (author, time, commit, earlier values) and who approved it, or its formula and the cells it read. |
| `GET /api/checks` | Each check with its failing runs: the cells to mark, and why. |
| `PUT /api/value` | Change a cell: `{item, coords, value}`, `nil: true` to enter nil, `value: null` to clear. A refused value is a 422 with the model's reason. |
| `POST /api/discard` | Drop the uncommitted changes. |
| `POST /api/commit` | Commit them: `{message}`. |
| `GET /api/log` | The commits, newest first. |

## How the page works

- The model's dimensions are flat: project, contract, engagement, line. A relation is a pointer
  item: a line points at its engagement, an engagement at its project and its contract. The grid
  builds its tree by following those pointers, so Layout decides what groups under what.
- A **model table** holds the items that share their dimensions. A **view** is a saved layout over
  items from the tables and holds no data of its own.
- Layout and filters are the page's: it fetches every cell, works out which rows and columns there
  are (nesting, Items as a field, date parts, Defined only) and hands Tabulator a tree of rows and
  nested column groups. Tabulator draws it and does the editing: a double-click or Enter edits a
  cell, a pointer opens a list of the labels it can point at, and the edit goes to the model.
- The date parts (`payment_date.dt.year`, `.dt.fiscal_quarter` ...) are read from the date values in
  the page, with the fiscal year fixed to start in April.
- A calculated cell is never stored; the model works it out, and the page asks again after every
  change.

## Not yet

- Loading and saving a model: the sample is built when the server starts.
- Branches, review and approval in the page. The repo has them; the API doesn't expose them.
- The fiscal calendar is the page's own, not the library's.
- Collapsing a group of columns: Tabulator can't, so only row groups collapse.
- One user, and no authentication. The server is for working locally.

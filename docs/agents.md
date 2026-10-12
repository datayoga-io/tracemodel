# Tutorial: a modeling agent and a data-entry agent

This tutorial sets up two AI agents that build Apple's three-statement model from its FY2025
Form 10-K:

1. The **modeling agent** writes the definition: dimensions, items and checks. It saves
   `model.json` and enters no numbers.
2. The **data-entry agent** gets only `model.json`. It enters the filing's values with
   `set_value`, and it can't change the definition.

The split is the same as XBRL's taxonomy and instance. One side decides what can exist and what
must hold; the other side only fills it in. Every value the second agent enters is validated
against the first agent's definition, and the checks show where the two disagree.

In our run, the modeling agent produced 21 dimensions, 32 items and 74 checks. The data-entry
agent filled 634 cells (75 of them nil, "—" in the filing), and all 315 check runs passed.

The agents here are [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions run
headless with `claude -p`. Any agent that can run Python and read the web works the same way.

## What you need

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code), logged in.
- [uv](https://docs.astral.sh/uv/).
- This repository.

## 1. Give each agent a sandbox

Each agent works in its own directory. It holds the library and the README, and nothing else.
The directory decides what the agent sees, not what it can do: `uv run` runs any Python as your
user, and the file tools aren't limited to the directory. The agent reads the web, so a page can
try to steer it. Run it in a container or a VM, or turn on Claude Code's sandboxed Bash.

```bash
SANDBOX=~/tracemodel-sandbox
mkdir -p $SANDBOX/wheels
uv build --wheel --out-dir $SANDBOX/wheels            # run in this repository
cp README.md $SANDBOX/
cd $SANDBOX
uv init --app --no-readme --no-workspace --vcs none --name sandbox -q
rm -f main.py hello.py
uv add ./wheels/tracemodel-*.whl
```

- The library goes in as a built wheel, so the agent isn't pointed at the source, the tests or
  the examples.
- The README is the only documentation. If the agent gets something wrong, the README is what
  to fix.

## 2. Run the modeling agent

Start the agent in the sandbox with a fixed set of tools:

```bash
cd ~/tracemodel-sandbox
claude -p "$MODEL_TASK" \
  --setting-sources project,local \
  --strict-mcp-config \
  --permission-mode dontAsk \
  --allowedTools "Read" "Write" "Edit" "Glob" "Grep" "Bash(uv run:*)" "WebFetch" "WebSearch" \
  --disallowedTools "Agent"
```

| Flag | Why |
| --- | --- |
| `--setting-sources project,local` | Skips your user settings: plugins, hooks and preferences. |
| `--strict-mcp-config` | No MCP servers or connectors. |
| `--permission-mode dontAsk` | Any tool not in `--allowedTools` is denied, not prompted. |
| `--allowedTools ...` | File tools, any Python through `uv run`, and the web. |
| `--disallowedTools "Agent"` | No sub-agents. |

The task, `$MODEL_TASK`:

```text
Work in this directory only: do not read local files outside it. Use the web freely.

1. Read README.md. It documents the `tracemodel` library, which is installed here
   (run Python with `uv run python`). For the library, use only what the README documents.
2. Research Apple's actual filings on the web: the FY2025 Form 10-K (fiscal years ending
   September 2025 and September 2024) on SEC EDGAR, especially Item 8 (the consolidated
   statements of operations, balance sheets and cash flows) and the notes that break
   lines down (net sales by category and by segment, operating expenses, etc.).
3. Write model.py that defines Apple's three-statement model (income statement, balance
   sheet, cash flow statement) for FY2024 and FY2025, as faithfully to the filing as you can:
   - the dimensions and their coords (use hierarchies where the filing has breakdowns),
   - the items scaffold, covering every line the statements report, with units,
   - the data quality checks that should hold between them: subtotals, totals, statement
     tie-outs (e.g. assets = liabilities + equity, cash flow ties to the balance sheet),
     with sensible tolerances.
   Do NOT set any values: no set_value calls and no numbers in the model. Another agent
   will enter values later using only your definition, so make it complete enough that
   every number in those statements has an item and coords to go into.
4. Run `uv run python model.py`; it must run without errors, print the model, and save the
   definition with model.save("model.json"). Another agent will get only model.json (not
   model.py or your notes) and enter the values.
5. Write NOTES.md with:
   - the sources you used (URLs and sections),
   - the modeling choices you made and why,
   - every place where the README was unclear, missing something, or where you had to
     guess how the API behaves (quote the README line when relevant),
   - anything you wanted to express but the API could not.
6. When everything above is done, write GAPS.md: a summary for the library's authors to review
   requirements for the next revision. List each gap ranked by impact on modeling Apple
   faithfully. For each: what you needed, what you did instead (with a short excerpt from
   model.py), and your recommendation (the API you would want, with a short example). End with
   the README changes you recommend. Be concrete; skip anything that worked well.
```

What to look for in the task:

- **"Do NOT set any values."** The definition is the contract. Keeping numbers out of it keeps
  the two jobs apart.
- **"Another agent will get only model.json."** The agent has to put everything the next agent
  needs into the definition: descriptions, units, and attrs such as sign conventions.
- **NOTES.md and GAPS.md.** They are how you improve the README and the library. Read them
  after each run.

The agent writes `model.py`, `model.json`, `NOTES.md` and `GAPS.md`. Check the definition loads:

```bash
uv run python -c '
import tracemodel as tm
m = tm.load("model.json")
print(len(m.dimensions), "dims,", len(m.items), "items,", len(m.checks), "checks")'
```

## 3. Run the data-entry agent

Make a second sandbox the same way (step 1), and copy in `model.json` alone. Don't copy
`model.py` or the notes: the definition has to stand on its own.

```bash
cp ~/tracemodel-sandbox/model.json ~/tracemodel-sandbox-enter/
cp ~/tracemodel-sandbox-enter/model.json ~/tracemodel-sandbox-enter/.model.json.orig   # to verify later
```

Start it with the same flags, and this task:

```text
Work in this directory only: do not read local files outside it. Use the web freely.

This directory has model.json: a `tracemodel` definition of Apple's three-statement model for
FY2024 and FY2025 (dimensions, items, checks), with no values. Another agent wrote it; you
don't have its code or notes. README.md documents the library, which is installed here (run
Python with `uv run python`). For the library, use only what the README documents.

Your job: enter the values from Apple's FY2025 Form 10-K, as reported.

Rules:
- You may only enter values: the only method that changes the model is `set_value`
  (`transaction()` is fine for grouping them). Do not add, change or remove dimensions, coords,
  items, formulas or checks, and do not overwrite model.json. Reading is fine: print(model),
  get, entry, table, check, and so on.
- If a number in the filing has no item or coords to go into, or the definition doesn't fit
  the filing, do not work around it by changing the model: write it down in NOTES.md.
- Every value's source points to where it is in the filing (URL plus statement or note).

1. Read README.md, then load model.json and look at it (print(model)).
2. Research Apple's FY2025 Form 10-K on SEC EDGAR: Item 8 and the notes that break lines down.
3. Write enter.py that loads model.json, enters every value the definition has room for with
   set_value, prints model.check(), and saves the values to values.jsonl with to_jsonl.
4. Run `uv run python enter.py` without errors. Investigate every failing check: a typo in your
   values (fix it), a value that is right but the check or definition is wrong (leave it and
   write it down), or something else.
5. Write NOTES.md: sources; how you mapped the filing onto the definition; every place the
   definition was unclear, missing an item or coords, or didn't match the filing; every check
   that still fails and why; and where the README was unclear.
6. When everything above is done, write GAPS.md: a summary for the authors of the library and
   of the definition, ranked by impact on entering Apple's values faithfully. For each: what
   you needed, what you did instead (with a short excerpt from enter.py), and your
   recommendation. Be concrete; skip anything that worked well.
```

What to look for in the task:

- **"The only method that changes the model is `set_value`."** The prompt sets this rule;
  nothing in the library enforces it. Step 4 checks that the agent kept it.
- **"Do not work around it by changing the model: write it down."** A definition that doesn't
  fit the filing is a finding for the modeling agent, not something to patch.
- **"Investigate every failing check."** A failing check is either a typo in a value or a wrong
  rule in the definition. The agent should tell which, and fix only its own typos.

The agent writes `enter.py`, `values.jsonl`, `NOTES.md` and `GAPS.md`.

## 4. Verify that the data-entry agent only entered values

```bash
cd ~/tracemodel-sandbox-enter
cmp -s .model.json.orig model.json && echo "model.json unchanged"
grep -nE '\.(add_[a-z_]+|remove_[a-z_]+|save|from_dict)\(' enter.py || echo "no definition calls"
uv run python -c '
import json, tracemodel as tm
before = tm.load(".model.json.orig")
after = tm.load(".model.json.orig")
after.load_jsonl("values.jsonl")
print("definition unchanged:", json.dumps(before.to_dict()) == json.dumps(after.to_dict()))
r = after.check()
print(f"{sum(x.passed for x in r.results)}/{len(r.results)} check runs pass; ok={r.ok}")'
```

- Loading `values.jsonl` validates every value against the definition, in one transaction: a bad
  row loads nothing.
- If the definition or the values hold formulas written as Python functions, `tm.load` and
  `load_jsonl` refuse them until you pass `allow_code=True`, which runs that code. Do that inside
  the agent's sandbox, since an agent wrote it.
- `report.failures` lists the runs that didn't pass, and `report.explain()` shows the cells each
  one read.

## The short way

`scripts/modeler_agent.sh` does all of the above:

```bash
scripts/modeler_agent.sh define     # sandbox ~/tracemodel-sandbox
scripts/modeler_agent.sh enter      # sandbox ~/tracemodel-sandbox-enter; verifies at the end
scripts/modeler_agent.sh forecast   # optional: a 5-year forecast on top of the entered values
```

- It deletes and recreates each sandbox, and only a directory it created itself (it leaves a
  marker file there).
- `DRY_RUN=1` builds the sandbox without starting the agent.
- `INPUTS=<dir>` picks where `enter` and `forecast` take their files from.

## What came up in our run

These are the points both agents raised. They are worth knowing before you adapt the tasks.

- **Signs.** The filing prints the same number positive in one table and in parentheses in
  another, such as cost of sales. The modeling agent entered numbers as printed and flipped the
  sign in the checks that compare tables. Say which convention you want in the task, and put it
  in the item's attrs so the data-entry agent sees it.
- **Opening balances.** The cash flow and equity statements print each year's beginning balance.
  They are reported numbers: enter them, and check each one against the previous year's ending
  from the second year on. See "Opening balances" in the README.
- **Cells not entered.** A grid of dims has cells the filing doesn't fill, such as a third year
  of the income statement next to a two-year balance sheet. That is normal. Narrow a check's
  `coords` instead of adding lines or periods.
- **"—" versus 0.** A "—" is entered as `tm.NIL`, not 0.
- **Getting the filing.** SEC EDGAR asks for a contact in the User-Agent header. Fetch pages with
  the agent's web tools rather than `curl`, so it doesn't put your own address in a request.

## Adapting it

- **Another company or filing.** Change the company, the form and the fiscal years in both
  tasks. Keep the rules.
- **Another agent.** Keep the sandbox (wheel plus README), the read-only rule for the data-entry
  agent, and the checks in step 4.
- **Formulas from a file.** A definition with formulas written as Python functions loads only
  with `tm.load(path, allow_code=True)`, because loading runs the code. Load files you didn't
  write in a sandbox.

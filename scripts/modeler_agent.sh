#!/usr/bin/env bash
# Run a Claude Code agent that sees only README.md and the installed tracemodel library: one that
# defines a model, one that enters its values, or one that forecasts. See docs/agents.md.
# The sandbox directory limits what the agent sees, not what it can do: `uv run` runs any Python
# as you. Run this inside a container or VM.
#
# Usage: scripts/modeler_agent.sh [define|enter|forecast] [sandbox-dir]
#   define   (default) define Apple's model from the 10-K and save it; sandbox ~/tracemodel-sandbox
#   enter    load a saved definition and enter the 10-K values with set_value only;
#            sandbox ~/tracemodel-sandbox-enter. Starts from $INPUTS (default ~/tracemodel-sandbox):
#            model.json only. Afterwards checks that the definition was not changed.
#   forecast extend a saved model with a 5-year forecast; sandbox ~/tracemodel-sandbox-forecast.
#            Starts from $INPUTS (default ~/tracemodel-sandbox-enter): model.json and values.jsonl.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TASK_NAME="${1:-define}"
case "$TASK_NAME" in
  define) SANDBOX="${2:-$HOME/tracemodel-sandbox}" ;;
  enter) SANDBOX="${2:-$HOME/tracemodel-sandbox-enter}"
         INPUTS="${INPUTS:-$HOME/tracemodel-sandbox}" ;;
  forecast) SANDBOX="${2:-$HOME/tracemodel-sandbox-forecast}"
            INPUTS="${INPUTS:-$HOME/tracemodel-sandbox-enter}" ;;
  *) echo "unknown task $TASK_NAME: use define, enter or forecast" >&2; exit 1 ;;
esac
MARKER=".tracemodel-sandbox"

# Start from an empty sandbox; only ever delete a directory this script created.
if [ -e "$SANDBOX" ]; then
  if [ -f "$SANDBOX/$MARKER" ]; then
    rm -rf "$SANDBOX"
  else
    echo "$SANDBOX exists and is not a sandbox made by this script; pick another path." >&2
    exit 1
  fi
fi
mkdir -p "$SANDBOX/wheels"
touch "$SANDBOX/$MARKER"

# The library goes in as a built wheel, so the sandbox has no path back to the repo
# (and no examples, tests or notebook). The README is the only documentation.
(cd "$REPO" && uv build --wheel --out-dir "$SANDBOX/wheels" -q)
cp "$REPO/README.md" "$SANDBOX/README.md"
if [ "$TASK_NAME" = forecast ]; then
  cp "$INPUTS/model.json" "$INPUTS/values.jsonl" "$SANDBOX/"
fi
if [ "$TASK_NAME" = enter ]; then
  cp "$INPUTS/model.json" "$SANDBOX/"  # the definition only: not model.py, NOTES.md or GAPS.md
  cp "$SANDBOX/model.json" "$SANDBOX/$MARKER.model.json"  # to check afterwards it wasn't changed
fi

cd "$SANDBOX"
uv init --app --no-readme --no-workspace --vcs none --name sandbox -q
rm -f hello.py main.py
uv add -q --refresh-package pytracemodel ./wheels/pytracemodel-*.whl  # don't reuse a cached older build

read -r -d '' DEFINE_TASK <<'EOF' || true
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
EOF

read -r -d '' FORECAST_TASK <<'EOF' || true
Work in this directory only: do not read local files outside it. Use the web freely.

This directory has a saved `tracemodel` model of Apple's FY2024–FY2025 three-statement model, as
reported in the FY2025 Form 10-K: model.json (the definition: dimensions, items, checks) and
values.jsonl (the reported values). README.md documents the library, which is installed here
(run Python with `uv run python`). For the library, use only what the README documents.

Your job: extend it into a 5-year forecast, FY2026–FY2030, that a financial analyst would accept
as a working three-statement model.

1. Read README.md, then load the model and values and look at them (print(model), model.check()).
2. Research what you need on the web: Apple's filing, recent guidance and consensus, so the
   drivers are defensible.
3. Write forecast.py that starts from model.json and values.jsonl and produces the forecast:
   - Add FY2026–FY2030 as periods, marked as forecasts in their attrs (e.g. {"kind": "forecast"}).
     FY2024–FY2025 stay the reported actuals.
   - Drivers (growth rates, margins, DSO / DIO / DPO, capex, D&A, tax rate, buybacks, dividends,
     ...) are entered as values. Their source points to assumptions.md, which you write: one
     section per driver with the rationale and the web sources behind it.
   - Forecast lines are formulas: item formulas, or formulas for individual cells entered with
     set_value, as the README describes.
   - The three statements must link: net income flows to the cash flow statement and to equity;
     cash on the balance sheet comes from the cash flow statement; the balance sheet balances
     in every forecast year. Use cash as the balancing item, and compute interest from opening
     balances (lag) to avoid circular formulas.
   - Add checks for the forecast: the balance sheet balances, cash ties, subtotals add up,
     plus warning-severity sanity checks (e.g. margins within a range, no negative cash).
4. Save the result as forecast.model.json and forecast.values.jsonl (model.save / to_jsonl),
   run `uv run python forecast.py` without errors, and show model.check() passing for the
   forecast years, plus the forecast income statement, balance sheet and cash flow by year.
5. Write NOTES.md: the modeling choices and why, where the README was unclear or you had to
   guess, and anything you wanted to express but the library could not.
6. When everything above is done, write GAPS.md: a summary for the library's authors to review
   requirements for the next revision. List each gap ranked by impact on building the forecast.
   For each: what you needed, what you did instead (with a short excerpt from forecast.py), and
   your recommendation (the API you would want, with a short example). End with the README
   changes you recommend. Be concrete; skip anything that worked well.
EOF

read -r -d '' ENTER_TASK <<'EOF' || true
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
EOF

case "$TASK_NAME" in
  define) TASK="$DEFINE_TASK" ;;
  enter) TASK="$ENTER_TASK" ;;
  forecast) TASK="$FORECAST_TASK" ;;
esac

if [ "${DRY_RUN:-}" = 1 ]; then
  echo "Sandbox ready at $SANDBOX (DRY_RUN=1: agent not started)"
  exit 0
fi

# --setting-sources project,local : skip user settings (plugins, hooks, user preferences)
# --strict-mcp-config             : no MCP servers or connectors
# --permission-mode dontAsk       : anything not in --allowedTools is denied, not prompted
#                                   (web search/fetch allowed; sub-agents are not)
claude -p "$TASK" \
  --setting-sources project,local \
  --strict-mcp-config \
  --permission-mode dontAsk \
  --allowedTools "Read" "Write" "Edit" "Glob" "Grep" "Bash(uv run:*)" "WebFetch" "WebSearch" \
  --disallowedTools "Agent"

if [ "$TASK_NAME" = enter ]; then
  echo
  echo "== Did the agent only enter values? =="
  if cmp -s "$MARKER.model.json" model.json; then echo "model.json unchanged"; else echo "model.json WAS CHANGED"; fi
  grep -nE '\.(add_[a-z_]+|remove_[a-z_]+|save|from_dict)\(' enter.py || echo "enter.py calls no definition methods"
  uv run python -c '
import json, tracemodel as tm
before = tm.load(".tracemodel-sandbox.model.json")
after = tm.load(".tracemodel-sandbox.model.json")
after.load_jsonl("values.jsonl")
same = json.dumps(before.to_dict(), sort_keys=True) == json.dumps(after.to_dict(), sort_keys=True)
print("definition unchanged after loading values.jsonl:", same)
r = after.check()
print(f"checks: {sum(x.passed for x in r.results)}/{len(r.results)} runs pass; ok={r.ok}")
' || true
fi

echo
echo "Agent output is in $SANDBOX:"
ls -1 "$SANDBOX"/*.py "$SANDBOX"/*.md "$SANDBOX"/*.json "$SANDBOX"/*.jsonl 2>/dev/null

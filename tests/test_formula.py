"""One kind of formula: a lambda of the cell, checked and compiled, the same in the session and after load."""

import math
from datetime import date

import pytest

import tracemodel as tm
from tracemodel.formula import Formula

SRC = {"document": "Contract C-201", "url": "https://example.com/c201.pdf", "section": "Fees"}


@pytest.fixture
def m():
    m = tm.Model()
    m.add_dimension("month", tm.period_range("2026-01", "2026-06", freq="M"))
    m.add_dimension("line", tm.Hierarchy([("/", "Total"), "/a", "/b", "/b/x", "/b/y"]))
    m.add_item("fee", ())
    m.add_item("start", (), dtype="date")
    m.add_item("end", (), dtype="date")
    m.add_item("sales", ("line", "month"))
    with m.transaction():
        m.set_value("fee", {}, 31_000, SRC)
        m.set_value("start", {}, "2026-03-16", SRC)
        m.set_value("end", {}, "2026-05-31", SRC)
        for line, v in [("/", 100), ("/a", 40), ("/b", 60), ("/b/x", 25), ("/b/y", 35)]:
            for month in m.dimensions["month"].coords:
                m.set_value("sales", {"line": line, "month": month}, v, SRC)
    return m


# -- a function and its source text are the same formula ------------------------

def test_function_and_source_give_the_same_formula(m):
    from_fn = Formula.of(lambda x: x["fee"] * x.period.overlap(x["start"], x["end"]))
    from_text = Formula.of('lambda x: x["fee"] * x.period.overlap(x["start"], x["end"])')
    assert from_fn == from_text and from_fn.source == "lambda x: x['fee'] * x.period.overlap(x['start'], x['end'])"
    m.add_item("by_fn", ("month",), formula=lambda x: x["fee"] * x.period.overlap(x["start"], x["end"]))
    m.add_item("by_text", ("month",), formula='lambda x: x["fee"] * x.period.overlap(x["start"], x["end"])')
    assert m.get("by_fn", month="2026-03") == m.get("by_text", month="2026-03") == 16_000


def test_the_function_itself_is_never_called(m):
    calls = []

    def spy(x):
        calls.append(x)
        return 1

    with pytest.raises(tm.DefinitionError, match="a formula is one lambda"):
        m.add_item("spied", ("month",), formula=spy)
    assert calls == []


@pytest.mark.parametrize("formula, value", [
    (lambda x: 0 <= x["fee"] <= 1e9, True),                                         # chained
    (lambda x: x["fee"] if x["fee"] > 30_000 else 0, 31_000),                       # if / else
    (lambda x: max(0, 40_000 - x["fee"]), 9_000),
    (lambda x: round(x["fee"] / 7, 2), 4428.57),
    (lambda x: sum(x["sales"].sel(line=c, month="2026-01") for c in ["/a", "/b"]), 100),
    (lambda x: math.log(x["fee"]), math.log(31_000)),
    (lambda x: (x["end"] - x["start"]).days, 76),
    (lambda x: x["start"].dt.month, 3),
    (lambda x: x["end"] > date(2026, 5, 1) and x["start"] < "2026-04-01", True),
    (lambda x: (1 + 0.05) ** 12 * 100, 1.05 ** 12 * 100),
    (lambda x: x["fee"] * 1.03 ** 2, 31_000 * 1.03 ** 2),
    (lambda x: len([c for c in x.labels("line") if c.startswith("/b")]), 3),
    (lambda x: {"a": 1, "b": 2}.get("b") + [1, 2, 3][-1], 5),
])
def test_what_a_formula_can_say(m, formula, value):
    m.add_check("C", expect=formula) if isinstance(value, bool) else m.add_item("v", (), formula=formula)
    if isinstance(value, bool):
        assert m.check().ok is value
    else:
        assert m.get("v") == pytest.approx(value)


def test_comparisons_keep_float_slack_and_refuse_dates_against_numbers(m):
    m.add_item("a", ())
    m.set_value("a", {}, 391_035.2, SRC)
    m.add_check("SLACK", expect=lambda x: abs(x["a"] - 391_035) <= 0.2)      # 0.2000000000116
    m.add_check("EQ", expect=lambda x: 0.1 + 0.2 == 0.3)
    assert m.check().ok
    m.add_check("MIXED", expect=lambda x: x["start"] > 0)
    run = m.check().filter("MIXED").results[0]
    assert not run.passed and "can't compare a date with a number" in run.error


def test_a_formula_is_one_lambda_of_one_cell():
    for bad, match in [("x['a'] * 2", "must be a lambda"), ("lambda: 1", "exactly one argument"),
                       ("lambda x, y: 1", "exactly one argument"), ("lambda *a: 1", r"\*args"),
                       ("def f(x): return 1", "a formula is one lambda"), (42, "must be a lambda")]:
        with pytest.raises(tm.DefinitionError, match=match):
            Formula.of(bad)


def test_two_lambdas_on_one_line_keep_their_own_source():
    up, down = Formula.of(lambda x: x["a"] + 1), Formula.of(lambda x: x["a"] - 1)
    assert (up.body, down.body) == ("x['a'] + 1", "x['a'] - 1")


def test_closure_values_are_saved_as_data(m):
    rate, cutoff, names = 0.05, date(2026, 4, 1), ["/a", "/b"]
    f = Formula.of(lambda x: x["fee"] * (1 + rate) if x.period > cutoff and x.coords["line"] in names else 0)
    assert f.env == {"rate": 0.05, "cutoff": date(2026, 4, 1), "names": ["/a", "/b"]}
    assert f.to_data()["env"] == {"rate": 0.05, "cutoff": {"$date": "2026-04-01"}, "names": ["/a", "/b"]}


@pytest.mark.parametrize("value", [object(), print, tm.Model(), [object()]])
def test_closures_that_are_not_data_are_refused(value):
    with pytest.raises(tm.DefinitionError, match="uses 'value'"):
        Formula.of(lambda x: x["a"] if value else 0)


def test_a_closure_over_the_model_or_a_cell_says_to_use_x(m):
    cell = m["fee"]
    with pytest.raises(tm.DefinitionError, match="read cells through x"):
        Formula.of(lambda x: m.get("fee") + 1)
    with pytest.raises(tm.DefinitionError, match="read cells through x"):
        Formula.of(lambda x: x[cell.item.name])


@pytest.mark.parametrize("env, match", [
    ({"_getattr_": 1}, "can't be a name"),
    ({"max": 1}, "can't be a name"),
    ({"lambda": 1}, "can't be a name"),
    ({"k": print}, "uses 'k'"),
    ({"k": {"$model": True}}, None),  # a plain dict of data is fine when it is passed in directly
])
def test_closure_names_and_values_are_checked(env, match):
    if match is None:
        assert Formula("lambda x: k", env).env == env
    else:
        with pytest.raises(tm.DefinitionError, match=match):
            Formula("lambda x: k", env)


def test_a_lambda_without_source_says_to_pass_its_text():
    fn = eval("lambda x: x['a'] * 2")
    with pytest.raises(tm.DefinitionError, match="pass the source text instead"):
        Formula.of(fn)
    assert Formula.of("lambda x: x['a'] * 2").body == "x['a'] * 2"


@pytest.mark.parametrize("source", [
    "lambda x: [0] * 10**9",
    "lambda x: 'a' * 10**10",
    "lambda x: 2 ** 10**6",
    "lambda x: sum(range(10**12))",
    "lambda x: sum(1 for a in range(100_000) for b in range(100_000))",
])
def test_huge_values_raise_quickly_when_the_formula_is_added(m, source):
    m.add_item("big", ())
    with pytest.raises(tm.DefinitionError, match="at most|too large|takes more than"):
        m.set_value("big", {}, source, SRC)
    assert not any(e.item == "big" for e in m)


@pytest.mark.parametrize("source", [
    "lambda x: len([0] * int(x['n']))",
    "lambda x: len('a' * int(x['n']))",
    "lambda x: int(x['n']) ** int(x['n'])",
    "lambda x: sum(range(int(x['n'])))",
])
def test_huge_values_that_depend_on_a_value_raise_when_run(m, source):
    m.add_item("n", ())
    m.add_item("big", ())
    m.set_value("n", {}, 10**9, SRC)
    m.set_value("big", {}, source, SRC)
    with pytest.raises(tm.DefinitionError, match="at most|too large"):
        m.get("big")


# -- x: one cell object for formulas and checks ---------------------------------

def test_rollup_check_with_children_covers_labels_added_later(m):
    m.add_check("ROLLUP", coords={"line": ["/", "/b"], "month": tm.ANY},
                expect=lambda x: abs(x["sales"] - sum(x["sales"].sel(line=c) for c in x.children("line"))) <= 0.2)
    assert m.check().ok
    m.add_coords("line", ["/b/z"])
    report = m.check()
    assert not report.ok and {r.at["line"] for r in report.failures} == {"/b"}   # /b/z has no value yet
    for month in m.dimensions["month"].coords:
        m.set_value("sales", {"line": "/b/z", "month": month}, 0, SRC)
    assert m.check().ok
    explained = m.check().explain("ROLLUP")
    assert "sales[line=/b, month=2026-01]" in explained and "sales[line=/b/z, month=2026-01]" in explained


def test_children_parent_and_labels(m):
    m.add_item("kids", ("line",), formula=lambda x: len(x.children("line")))
    m.add_item("top", ("line",), dtype=int, formula=lambda x: int(x.parent("line") is None))
    assert [m.get("kids", line=c) for c in ["/", "/b", "/b/x"]] == [2, 2, 0]   # a leaf: sum is 0
    assert [m.get("top", line=c) for c in ["/", "/b/x"]] == [1, 0]
    with pytest.raises(tm.DefinitionError, match="'month' is not a hierarchy"):
        m.add_item("bad", ("month",), formula=lambda x: len(x.children("month")))
    with pytest.raises(tm.DefinitionError, match="x has no 'line' label"):
        m.add_item("worse", ("month",), formula=lambda x: len(x.children("line")))


def test_a_checks_x_is_the_run_not_a_cell(m):
    with pytest.raises(tm.DefinitionError, match="in a check, x is the run"):
        m.add_check("SELF", coords={"month": tm.ANY}, expect='lambda x: x["fee"] > 0 or x > 0')
    with pytest.raises(tm.DefinitionError, match="in a check, x is the run"):
        m.add_check("LAG", coords={"month": tm.ANY}, expect=lambda x: x.lag("month") > 0)


def test_set_value_with_lambda_text_is_a_cell_formula(m):
    m.add_item("bonus", ("month",), formula=lambda x: 0)
    m.set_value("bonus", {"month": "2026-06"}, "lambda x: x['fee'] * 0.1", SRC)
    assert m.get("bonus", month="2026-06") == 3_100
    assert "= x['fee'] * 0.1" in str(m.entry("bonus"))


def test_formulas_off_keeps_the_text_and_runs_nothing(m, tmp_path):
    m.add_item("bonus", ("month",), formula=lambda x: x["fee"] * 0.1)
    m.add_check("POS", coords={"month": tm.ANY}, expect=lambda x: x["bonus"] >= 0)
    m.set_value("fee", {}, "lambda x: 31_000", SRC)
    m.save(tmp_path / "m.json")
    m.to_jsonl(tmp_path / "v.jsonl")
    off = tm.load(tmp_path / "m.json", formulas="off")
    off.load_jsonl(tmp_path / "v.jsonl", formulas="off")
    assert "x['fee'] * 0.1" in repr(off) and "formulas='off'" in repr(off)
    with pytest.raises(tm.MissingValue):
        off.get("bonus", month="2026-01")
    assert all("formulas='off'" in r.error for r in off.check().filter("POS").results)
    assert off.to_dict() == m.to_dict()                                   # saved back unchanged
    with pytest.raises(tm.DefinitionError, match="'on' or 'off'"):
        tm.load(tmp_path / "m.json", formulas="maybe")


# -- review fixes -----------------------------------------------------------------

def test_a_deep_branched_chain_read_through_another_formula(m):
    m.add_dimension("p", [f"P{i:03}" for i in range(120)])
    m.add_item("flag", ())
    m.add_item("b", ("p",), formula=lambda x: x.lag("p") + 1 if x["flag"] > 0 and x.coords["p"] != "P000" else 0)
    m.add_item("r", ("p",), formula=lambda x: x["b"] * 2)
    m.add_check("POS", coords={"p": ["P119"]}, expect=lambda x: x["r"] > 0)
    m.set_value("flag", {}, 1, SRC)
    assert m.get("r", p="P119") == 238
    assert m.check().filter("POS").ok


def test_a_rejected_cell_formula_leaves_the_cell_as_it_was(m):
    m.add_item("bonus", ())
    m.set_value("bonus", {}, 5, SRC)
    with pytest.raises((tm.DefinitionError, tm.UnknownItem)):
        m.set_value("bonus", {}, "lambda x: int('abc')", SRC)
    assert m.get("bonus") == 5 and m.entry("bonus").value == 5


def test_floor_division(m):
    m.add_item("weeks", (), formula=lambda x: x["fee"] // 7)
    assert m.get("weeks") == 31_000 // 7


def test_a_comparison_with_a_side_that_is_none(m):
    m.add_check("NO-KIND", coords={"month": ["2026-01"]}, expect=lambda x: x.attrs("month").get("kind") != "forecast")
    assert m.check().filter("NO-KIND").ok


def test_a_formula_made_of_canonical_text_is_written_without_doubled_parentheses():
    assert Formula("lambda x: sum(c for c in [1, 2])").source == "lambda x: sum(c for c in [1, 2])"
    assert Formula("lambda x: max((c for c in [1]), default=0)").source == "lambda x: max((c for c in [1]), default=0)"


def test_walk_errors_are_definition_errors(m):
    with pytest.raises(tm.DefinitionError, match="ValueError"):
        m.add_item("bad", (), formula="lambda x: int('abc')")

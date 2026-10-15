"""Formulas from a file can't reach anything but the model's cells and labels.

Each escape is tried three ways: as a formula added in the session, in a definition file and in
a values file. All of them must raise a DefinitionError (or UnknownItem) before or while
running, and nothing outside the formula may happen.
"""

import json
import time

import pytest

import tracemodel as tm
from tracemodel.formula import Formula

SRC = {"document": "D", "url": "https://example.com/d.pdf", "section": "1"}

ESCAPES = [
    "lambda x: x.__class__",
    "lambda x: x._model",
    "lambda x: x._model.items",
    "lambda x: '{0._model}'.format(x)",
    "lambda x: '{0._model}'.format_map({'0': x})",
    "lambda x: x.period.overlap.__globals__",
    "lambda x: x.period.overlap.__func__",
    "lambda x: __import__('os')",
    "lambda x: getattr(x, '_model')",
    "lambda x: setattr(x, 'a', 1)",
    "lambda x: type(x)",
    "lambda x: vars(x)",
    "lambda x: globals()",
    "lambda x: open('/etc/passwd').read()",
    "lambda x: eval('1')",
    "lambda x: exec('1')",
    "lambda x: compile('1', 'f', 'eval')",
    "lambda x: [].__class__.__bases__[0].__subclasses__()",
    "lambda x: ().__class__",
    "lambda x: (a for a in [1]).gi_frame",
    "lambda x: (a for a in [1]).gi_code",
    "lambda x: x.coords.update({'period': 'FY2099'})",
    "lambda x: x.labels('period').append('FY2099')",
    "lambda x: ''.join(['a'] * 3)",
    "lambda x: x.period.label % 1",
    "lambda x: '%s' % 1",
    "lambda x: f'{x}'",
    "lambda x: (lambda: 1)()",
    "lambda x: (y := 1)",
    "lambda x: max(*[1, 2])",
    "lambda x: x['revenue'].sel(**{'period': 'FY2025'})",
    "lambda x: math.factorial(10**5)",
    "lambda x: math.__loader__",
    "lambda x: date.today()",
    "lambda x: datetime.now()",
    "lambda x: int.from_bytes(b'1', 'big')",
    "lambda x: x.dt.strftime.__self__",
    "lambda x: x['revenue'].dt._v",
    "lambda x: x[1:2]",
    "lambda x: x.period.__class__.__init__.__globals__",
    "lambda x: 9 ** 9 ** 9",
    "lambda x: [0] * 10 ** 9",
    "lambda x: 'a' * 10 ** 10",
    "lambda x: sum(range(10 ** 12))",
    "lambda x: [1 for a in range(10**5) for b in range(10**5)]",
    "lambda x: " + "+".join(["1"] * 6_000),
    "lambda x: " + "(" * 300 + "1" + ")" * 300,
    "lambda x: " + "[" * 150 + "1" + "]" * 150,
]


def _model():
    m = tm.Model()
    m.add_dimension("period", tm.period_range("2025", periods=2, freq="Y"))
    m.add_item("revenue", ("period",))
    m.add_item("start", ("period",), dtype="date")
    for p in m.dimensions["period"].coords:
        m.set_value("revenue", {"period": p}, 100, SRC)
        m.set_value("start", {"period": p}, f"{p}-03-01", SRC)
    return m


def _raises_safely(fn) -> None:
    started = time.perf_counter()
    with pytest.raises((tm.DefinitionError, tm.UnknownItem, tm.UnknownCoord)):
        fn()
    assert time.perf_counter() - started < 5, "an escape must be refused quickly"


@pytest.mark.parametrize("source", ESCAPES)
def test_escape_is_refused_in_the_session(source):
    m = _model()
    m.add_item("t", ("period",))

    def attempt():
        m.set_value("t", {"period": "2025"}, source, SRC)
        m.get("t", period="2025")
    _raises_safely(attempt)


@pytest.mark.parametrize("source", ESCAPES)
def test_escape_is_refused_in_a_definition_file(source):
    data = _model().to_dict()
    data["items"].append({"name": "t", "dims": ["period"], "dtype": "float", "formula": source})
    data["checks"].append({"id": "C", "coords": {"period": "ANY"}, "expect": source})

    def attempt():
        m = tm.Model.from_dict(data)
        m.get("t", period="2025")
    _raises_safely(attempt)


@pytest.mark.parametrize("source", ESCAPES)
def test_escape_is_refused_in_a_values_file(source, tmp_path):
    m = _model()
    m.add_item("t", ("period",))
    path = tmp_path / "v.jsonl"
    path.write_text(json.dumps({"line_item": "t", "coords": {"period": "2025"}, "formula": source,
                                "source": SRC}) + "\n")

    def attempt():
        m.load_jsonl(path)
        m.get("t", period="2025")
    _raises_safely(attempt)
    assert not any(e.item == "t" for e in m)                    # nothing half-loaded


@pytest.mark.parametrize("old", [
    {"python": "lambda x: x._model", "env": {}},
    {"python": "lambda x: model.get('revenue', period='2025')", "env": {"model": {"$model": True}}},
    {"python": "lambda x: base", "env": {"base": {"$expr": "revenue"}}},
    {"python": "lambda x: os.getcwd()", "env": {"os": {"$module": "os"}}},
    {"python": "lambda x: k", "env": {"_getattr_": 1, "k": 1}},
    {"python": "lambda x: max", "env": {"max": 1}},
    "__import__('os').system('true')",
    "revenue.__class__",
])
def test_old_saved_formulas_that_reach_outside_are_refused(old):
    data = _model().to_dict()
    data["items"].append({"name": "t", "dims": ["period"], "dtype": "float", "formula": old})
    _raises_safely(lambda: tm.Model.from_dict(data))


ALLOWED = {
    "a str": ("x.period.label", {"upper", "lower", "startswith"}, {"join", "format", "format_map", "center", "zfill", "encode", "translate"}),
    "a list": ("x.labels('period')", {"index", "count"}, {"append", "extend", "pop", "clear", "sort", "insert", "remove", "copy"}),
    "a dict": ("x.coords", {"get", "keys", "items"}, {"update", "pop", "setdefault", "clear", "copy", "fromkeys"}),
    "a Period": ("x.period", {"start_time", "overlap", "label"}, {"from_data"}),
    "a date": ("x.period.start_time", {"year", "isoformat"}, {"today", "fromtimestamp", "ctime", "strftime"}),
    "a DatetimeAccessor": ("x['start'].dt", {"month", "to_period"}, {"from_data"}),
}


@pytest.mark.parametrize("what", ALLOWED)
def test_only_listed_attributes_on_each_type(what):
    target, ok, denied = ALLOWED[what]
    m = _model()
    for i, name in enumerate(sorted(ok)):
        m.add_item(f"ok{i}", ("period",))
        m.set_value(f"ok{i}", {"period": "2025"}, f"lambda x: 1 if {target}.{name} else 0", SRC)
        assert m.get(f"ok{i}", period="2025") == 1
    for name in sorted(denied):
        f = Formula(f"lambda x: {target}.{name}")
        m.add_item("bad", ("period",))
        with pytest.raises(tm.DefinitionError, match=f"can't use .{name} on"):
            m.set_value("bad", {"period": "2025"}, f, SRC)
            m.get("bad", period="2025")


def test_closure_lists_are_copies_a_formula_cannot_change():
    names = ["a"]
    f = Formula.of(lambda x: names.index("a"))
    assert f.env["names"] == ["a"] and f.env["names"] is not names


@pytest.mark.parametrize("source", [
    "lambda x: len([sum(range(100000)) for i in range(5000)])",
    "lambda x: len([[0] * 100000 for i in range(300)])",
    "lambda x: len([sorted(range(100000)) for i in range(500)])",
    "lambda x: len([max(range(100000)) for i in range(500)])",
    "lambda x: len([99999 in list(range(100000)) for i in range(500)])",
    "lambda x: " + " + ".join(["sum(range(100000))"] * 900),
])
def test_work_inside_builtins_counts_against_the_budget(source):
    _raises_safely(lambda: Formula(source) and tm.Model.from_dict(
        {**_model().to_dict(), "items": _model().to_dict()["items"] + [
            {"name": "t", "dims": ["period"], "dtype": "float", "formula": source}]}).get("t", period="2025"))


@pytest.mark.parametrize("source", [
    "lambda x: len([sum(range(int(x['revenue']) * 1000)) for i in range(5000)])",
    "lambda x: len([[0] * (int(x['revenue']) * 1000) for i in range(300)])",
])
def test_value_dependent_builtin_work_counts_at_run_time(source):
    m = _model()
    m.add_item("t", ("period",))
    m.set_value("t", {"period": "2025"}, source, SRC)
    _raises_safely(lambda: m.get("t", period="2025"))

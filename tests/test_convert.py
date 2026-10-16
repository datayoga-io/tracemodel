"""Files saved before formulas were lambdas load: expression text, per-label checks, Python entries."""

import json
import warnings

import pytest

import tracemodel as tm
from tracemodel.convert import from_by_label, from_text

SRC = {"document": "D", "url": "https://example.com/d.pdf", "section": "1"}


@pytest.mark.parametrize("text, source", [
    ("revenue * 0.6", "lambda x: x['revenue'] * 0.6"),
    ("abs(a['/'] - (a['/x'] + a['/y'])) <= 0.2", "lambda x: abs(x['a']['/'] - (x['a']['/x'] + x['a']['/y'])) <= 0.2"),
    ("(a >= 0) & ~b.is_nil()", "lambda x: all([x['a'] >= 0, not x['b'].is_nil()])"),
    ("(a > 0) | (a == 0) | b.is_nil()", "lambda x: any([x['a'] > 0, x['a'] == 0, x['b'].is_nil()])"),
    ("rev.lag('period') * 1.03", "lambda x: x['rev'].lag('period') * 1.03"),
    ("v.sel(a='x', b='z')", "lambda x: x['v'].sel(a='x', b='z')"),
    ("d <= date('2026-03-31') + timedelta(days=30)", "lambda x: x['d'] <= date.fromisoformat('2026-03-31') + timedelta(days=30)"),
    ("lambda x: x['a'] + 1", "lambda x: x['a'] + 1"),
])
def test_expression_text_becomes_a_lambda(text, source):
    assert from_text(text) == source


@pytest.mark.parametrize("text", ["__import__('os')", "a.__class__", "(lambda: 1)()", "a ** 2", "[a]", "a if b else c"])
def test_text_outside_the_old_grammar_is_refused(text):
    with pytest.raises(tm.DefinitionError, match="not part of the expression grammar"):
        from_text(text)


def _old_model():
    """A definition file as tracemodel/1 wrote it before lambdas."""
    return {
        "format": "tracemodel/1",
        "dimensions": [{"name": "period", "coords": ["FY24", "FY25"]},
                       {"name": "seg", "hierarchy": True, "coords": [["/", "Total"], "/a", "/b"]}],
        "items": [
            {"name": "double", "dims": ["seg", "period"], "dtype": "float", "formula": "cost * 2"},  # before cost
            {"name": "cost", "dims": ["seg", "period"], "dtype": "float"},
            {"name": "capped", "dims": ["period"], "dtype": "float",
             "formula": {"python": 'lambda x: min(x["cost"]["/"], CAP)', "env": {"CAP": 5, "math": {"$module": "math"}}}},
        ],
        "checks": [
            {"id": "SUM", "coords": {"period": "ANY"}, "expect": "cost['/'] == cost['/a'] + cost['/b']"},
            {"id": "BOTH", "coords": {"period": "ANY"}, "expect": "(cost['/a'] >= 0) & (cost['/b'] >= 0)"},
            {"id": "PER", "coords": {"seg": ["/a", "/b"], "period": "ANY"}, "params": ["seg"],
             "expect_by_label": [{"labels": ["/a"], "expect": "cost['/a'] <= 3"},
                                 {"labels": ["/b"], "expect": "cost['/b'] <= 10"}]},
        ],
    }


def _values(m):
    for period in ("FY24", "FY25"):
        for seg, v in [("/", 7), ("/a", 3), ("/b", 4)]:
            m.set_value("cost", {"seg": seg, "period": period}, v, SRC)


def test_an_old_definition_file_loads_and_checks_without_allow_code():
    m = tm.Model.from_dict(_old_model())
    _values(m)
    assert m.get("double", seg="/a", period="FY24") == 6           # a formula saved before the item it reads
    assert m.get("capped", period="FY25") == 5
    assert m.check().ok
    m.set_value("cost", {"seg": "/a", "period": "FY25"}, 4, SRC)
    assert {(r.check.id, r.at.get("seg")) for r in m.check().failures} == {("SUM", None), ("PER", "/a")}
    saved = m.to_dict()
    assert {c["id"]: c["expect"] for c in saved["checks"]}["BOTH"] == (
        "lambda x: all([x['cost']['/a'] >= 0, x['cost']['/b'] >= 0])")
    assert "expect_by_label" not in json.dumps(saved)
    assert tm.Model.from_dict(saved).to_dict() == saved             # converted once; stable after


def test_by_label_conversion_keys_on_the_runs_labels():
    source = from_by_label(["seg"], [{"labels": ["/a"], "expect": "c['/a'] <= 3"}, {"labels": ["/b"], "expect": "c['/b'] <= 10"}])
    assert source == ("lambda x: x.coords['seg'] == '/a' and x['c']['/a'] <= 3 or "
                      "(x.coords['seg'] == '/b' and x['c']['/b'] <= 10)")


def test_a_by_label_check_keeps_running_over_its_labels():
    m = tm.Model.from_dict(_old_model())
    _values(m)
    m.add_coords("seg", ["/c"])
    assert len(m.check().filter("PER").results) == 4                # saved over /a and /b, not tm.ANY
    assert m.check().filter("PER").ok


def test_any_or_all_still_reads_every_part():
    data = _old_model()
    m = tm.Model.from_dict(data)
    m.set_value("cost", {"seg": "/a", "period": "FY24"}, 3, SRC)    # /b missing
    run = m.check().filter("BOTH", period="FY24").results[0]
    assert not run.passed and [c.coords["seg"] for c in run.missing] == ["/b"]


def test_an_old_values_file_loads(tmp_path):
    m = tm.Model.from_dict(_old_model())
    path = tmp_path / "v.jsonl"
    rows = [{"line_item": "cost", "coords": {"seg": "/", "period": "FY24"}, "formula": "cost['/a'] + cost['/b']",
             "source": SRC},
            {"line_item": "cost", "coords": {"seg": "/a", "period": "FY24"}, "value": 1, "source": SRC},
            {"line_item": "cost", "coords": {"seg": "/b", "period": "FY24"}, "value": 2, "source": SRC},
            {"line_item": "cost", "coords": {"seg": "/", "period": "FY25"},
             "formula": {"python": "lambda x: x.lag('period') * 2", "env": {}}, "source": SRC}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    m.load_jsonl(path)
    assert m.get("cost", seg="/", period="FY24") == 3 and m.get("cost", seg="/", period="FY25") == 6


def test_allow_code_is_accepted_ignored_and_warned(tmp_path):
    (tmp_path / "m.json").write_text(json.dumps(_old_model()))
    with pytest.warns(DeprecationWarning, match="allow_code is ignored"):
        m = tm.load(tmp_path / "m.json", allow_code=True)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        tm.load(tmp_path / "m.json")                                # no warning without it
    with pytest.warns(DeprecationWarning):
        tm.Model.from_dict(_old_model(), allow_code=False)
    assert m.check().results


def test_old_date_constants_load_and_run():
    data = _old_model()
    data["items"].append({"name": "due", "dims": ["period"], "dtype": "date",
                          "formula": "date('2026-03-31') + timedelta(days=30)"})
    data["checks"].append({"id": "LATE", "coords": {"period": "ANY"},
                           "expect": "due <= datetime('2026-12-31T00:00:00')"})
    m = tm.Model.from_dict(data)
    from datetime import date
    assert m.get("due", period="FY24") == date(2026, 4, 30)


def test_a_per_label_check_with_many_labels_loads():
    labels = [f"/l{i}" for i in range(300)]
    data = {"format": "tracemodel/1",
            "dimensions": [{"name": "seg", "coords": labels}],
            "items": [{"name": "v", "dims": ["seg"], "dtype": "float"}],
            "checks": [{"id": "PER", "coords": {"seg": labels}, "params": ["seg"],
                        "expect_by_label": [{"labels": [s], "expect": f"v['{s}'] >= 0"} for s in labels]}]}
    m = tm.Model.from_dict(data)
    for s in labels:
        m.set_value("v", {"seg": s}, 1, SRC)
    assert m.check().ok
    m.set_value("v", {"seg": "/l150"}, -1, SRC)
    assert [r.at["seg"] for r in m.check().failures] == ["/l150"]


@pytest.mark.parametrize("bad", [[], [{"labels": ["/a", "/b"], "expect": "c >= 0"}], 3])
def test_bad_per_label_checks_are_refused(bad):
    with pytest.raises(tm.DefinitionError):
        from_by_label(["seg"], bad if isinstance(bad, list) else [{"labels": ["/a"], "expect": bad}])

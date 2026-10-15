"""A changed value recalculates only the cells and check runs that read it, and those are right."""

import random

import pytest

import tracemodel as tm

SRC = {"document": "Budget", "url": "https://example.com/budget.pdf", "section": "1"}
PERIODS = ["P1", "P2", "P3", "P4"]
LINES = ["a", "b", "c"]


def build():
    m = tm.Model()
    m.add_dimension("period", PERIODS)
    m.add_dimension("line", LINES)
    m.add_item("revenue", ("line", "period"))
    m.add_item("rate", ())
    m.add_item("use_rate", (), dtype=int)
    m.add_item("cost", ("line", "period"), formula=lambda x: x["revenue"] * 0.6)
    m.add_item("margin", ("line", "period"),                                      # which cells it reads depends on use_rate
               formula=lambda x: x["revenue"] * x["rate"] if x["use_rate"] == 1 else x["revenue"] - x["cost"])
    m.add_item("cum", ("line", "period"),                                         # a chain along period
               formula=lambda x: x["margin"] + (x.lag("period") if x.coords["period"] != "P1" else 0))
    m.add_check("COST-BELOW-REVENUE", coords={"line": tm.ANY, "period": tm.ANY},
                expect=lambda x: x["cost"] <= x["revenue"])
    m.add_check("CUM-POSITIVE", coords={"line": tm.ANY}, expect=lambda x: x["cum"].sel(period="P4") > 0)
    with m.transaction():
        for i, line in enumerate(LINES):
            for j, p in enumerate(PERIODS):
                m.set_value("revenue", {"line": line, "period": p}, 100 + 10 * i + j, SRC)
        m.set_value("rate", {}, 0.5, SRC)
        m.set_value("use_rate", {}, 0, SRC)
    return m


def values(m):
    return {(item, line, p): m._value(m[item], {"line": line, "period": p})
            for item in ("cost", "margin", "cum") for line in LINES for p in PERIODS}


def fresh(m):
    """The same values, calculated from nothing."""
    m._changed()
    return values(m)


def test_a_change_recalculates_its_dependents():
    m = build()
    assert m.get("cum", line="a", period="P4") == pytest.approx(40 + 40.4 + 40.8 + 41.2)
    m.set_value("revenue", {"line": "a", "period": "P2"}, 200, SRC)
    assert m.get("cost", line="a", period="P2") == 120
    assert m.get("margin", line="a", period="P2") == 80
    assert m.get("cum", line="a", period="P4") == pytest.approx(40 + 80 + 40.8 + 41.2)


def test_other_cells_stay_calculated(monkeypatch):
    m = build()
    values(m)
    calls, run = [], tm.model.run

    def counting(formula, model, item, full, reads):
        if item.name == "margin":
            calls.append((full["line"], full["period"]))
        return run(formula, model, item, full, reads)

    monkeypatch.setattr(tm.model, "run", counting)
    m.set_value("revenue", {"line": "b", "period": "P3"}, 500, SRC)
    values(m)
    assert calls == [("b", "P3")]  # only the margin that read it


def test_a_function_formula_follows_its_new_reads():
    m = build()
    values(m)
    m.set_value("use_rate", {}, 1, SRC)  # margin now reads rate, not cost
    assert m.get("margin", line="a", period="P1") == 50
    m.set_value("rate", {}, 0.25, SRC)
    assert m.get("margin", line="a", period="P1") == 25
    m.set_value("use_rate", {}, 0, SRC)
    m.set_value("rate", {}, 0.75, SRC)  # read before, not now: margin stays revenue - cost
    assert m.get("margin", line="a", period="P1") == 40


def test_a_missing_input_entered_later():
    m = tm.Model()
    m.add_dimension("period", PERIODS)
    m.add_item("price", ("period",))
    m.add_item("units", ("period",))
    m.add_item("sales", ("period",), formula=lambda x: x["price"] * x["units"])
    m.add_item("double", ("period",), formula=lambda x: x["sales"] * 2)
    m.set_value("price", {"period": "P1"}, 3, SRC)
    with pytest.raises(tm.MissingValue):
        m.get("double", period="P1")
    m.set_value("units", {"period": "P1"}, 5, SRC)
    assert m.get("double", period="P1") == 30


def test_overrides_and_cell_formulas():
    m = build()
    values(m)
    m.set_value("cost", {"line": "a", "period": "P1"}, 10, SRC)  # over the formula
    assert m.get("margin", line="a", period="P1") == 90
    m.set_value("cost", {"line": "a", "period": "P1"}, lambda x: x["revenue"] * 0.9, SRC)  # this cell's own formula
    assert m.get("margin", line="a", period="P1") == pytest.approx(10)
    m.set_value("revenue", {"line": "a", "period": "P1"}, 200, SRC)
    assert m.get("margin", line="a", period="P1") == pytest.approx(20)


def test_check_runs_follow_their_reads():
    m = build()
    assert m.check().ok
    m.set_value("revenue", {"line": "c", "period": "P2"}, -50, SRC)  # cost -30 > revenue -50; cum stays > 0
    (fail,) = m.check().failures
    assert fail.check.id == "COST-BELOW-REVENUE" and fail.at == {"line": "c", "period": "P2"}
    m.set_value("revenue", {"line": "c", "period": "P2"}, -1000, SRC)  # cum at P4 goes negative too
    assert {r.check.id for r in m.check().failures} == {"COST-BELOW-REVENUE", "CUM-POSITIVE"}
    m.set_value("revenue", {"line": "c", "period": "P2"}, 112, SRC)
    assert m.check().ok


def test_a_replaced_check_runs_again():
    m = build()
    assert m.check().ok
    m.add_check("COST-BELOW-REVENUE", coords={"line": tm.ANY, "period": tm.ANY}, expect=lambda x: x["cost"] <= 0)
    assert len(m.check().failures) == len(LINES) * len(PERIODS)


def test_rollback_restores_calculated_values():
    m = build()
    before = values(m)
    with pytest.raises(RuntimeError), m.transaction():
        m.set_value("revenue", {"line": "a", "period": "P1"}, 999, SRC)
        assert m.get("cum", line="a", period="P4") != before[("cum", "a", "P4")]
        raise RuntimeError
    assert values(m) == before


def test_random_edits_match_a_full_recalculation():
    rng = random.Random(7)
    m = build()
    for _ in range(300):
        line, p = rng.choice(LINES), rng.choice(PERIODS)
        roll = rng.random()
        if roll < 0.6:
            m.set_value("revenue", {"line": line, "period": p}, rng.randint(-50, 300), SRC)
        elif roll < 0.7:
            m.set_value("use_rate", {}, rng.randint(0, 1), SRC)
        elif roll < 0.8:
            m.set_value("rate", {}, rng.choice([0.1, 0.5, 2.0]), SRC)
        elif roll < 0.9:
            m.set_value("cost", {"line": line, "period": p}, rng.choice([tm.NIL, 5, lambda x: x["revenue"] * 0.3]), SRC)
        else:
            m.set_value("margin", {"line": line, "period": p}, rng.randint(0, 50), SRC)
        if rng.random() < 0.5:
            incremental = values(m)
            report = [(r.check.id, tuple(r.at.items()), r.passed) for r in m.check().results]
            assert incremental == fresh(m)
            assert report == [(r.check.id, tuple(r.at.items()), r.passed) for r in m.check().results]

"""Diff: what differs between two models or two commits, and why calculated cells moved."""

import pytest

import tracemodel as tm

SRC = {"document": "Architect agreement", "url": "https://example.com/a.pdf", "section": "4.2"}


@pytest.fixture
def repo():
    r = tm.Repo.memory("oren")
    m = r.model
    m.add_dimension("term", ["T1", "T2"])
    m.add_item("rate", ("term",))
    m.add_item("units", ("term",))
    m.add_item("fee", ("term",), formula=lambda x: x["rate"] * x["units"])
    m.add_item("total", (), formula=lambda x: x["fee"]["T1"] + x["fee"]["T2"])
    with m.transaction():
        for t in ("T1", "T2"):
            m.set_value("rate", {"term": t}, 6_000.0, SRC)
            m.set_value("units", {"term": t}, 100.0, SRC)
    r.commit("terms")
    r.new_branch("what-if")
    r.checkout("what-if")
    return r


def test_no_differences(repo):
    d = repo.diff("main")
    assert not d and len(d) == 0 and "no differences" in str(d)


def test_entered_values_and_definitions(repo):
    m = repo.model
    m.set_value("units", {"term": "T1"}, 470.0, SRC, meta={"confidence": "assumed"})
    m.clear_value("units", {"term": "T2"})
    m.update_item("rate", attrs={"units": "ILS"})
    m.add_check("POS", coords={"term": tm.ANY}, expect=lambda x: x["rate"] > 0)
    m.add_coords("term", ["T3"])
    repo.commit("what if")
    d = repo.diff("main", "what-if")
    assert [(c.target, c.change) for c in d] == [
        ("dimension:term", "changed"), ("item:rate", "changed"), ("check:POS", "added"),
        ("value:units[term=T1]", "changed"), ("value:units[term=T2]", "removed")]
    units = d.of("value")[0]
    assert units.before["value"] == 100 and units.after["meta"] == {"confidence": "assumed"}
    assert "470 (assumed)" in str(d)


def test_calculated_cells_that_moved_and_why(repo):
    repo.model.set_value("units", {"term": "T1"}, 470.0, SRC)
    repo.model.update_item("rate", attrs={"units": "ILS"})  # moves nothing
    d = repo.diff("main", calculated=True)  # against the working model
    calc = {c.target: c for c in d.of("calc")}
    assert set(calc) == {"calc:fee[term=T1]", "calc:total[]"}
    assert (calc["calc:fee[term=T1]"].before, calc["calc:fee[term=T1]"].after) == (600_000, 2_820_000)
    assert calc["calc:total[]"].because == ("value:units[term=T1]",)


def test_a_formula_change_is_why(repo):
    repo.model.set_formula("fee", lambda x: x["rate"] * x["units"] * 1.17)
    d = repo.diff("main", calculated=True)
    assert [c.target for c in d.of("calc")] == ["calc:fee[term=T1]", "calc:fee[term=T2]", "calc:total[]"]
    assert all(c.because == ("item:fee",) for c in d.of("calc"))


def test_an_entered_value_over_a_formula(repo):
    repo.model.set_value("fee", {"term": "T2"}, 1.0, SRC)
    d = repo.diff("main", calculated=True)
    assert [(c.target, c.change) for c in d] == [("value:fee[term=T2]", "added"), ("calc:fee[term=T2]", "removed"),
                                                 ("calc:total[]", "changed")]


def test_diff_two_models():
    a, b = tm.Model(), tm.Model()
    for m in (a, b):
        m.add_dimension("term", ["T1"])
        m.add_item("x", ("term",))
    b.set_value("x", {"term": "T1"}, 1.0, SRC)
    assert [str(c) for c in tm.diff(a, b)] == ["added    value:x[term=T1]: None -> 1.0"]
    assert tm.diff(a, b).table().columns == ["object", "change", "before", "after", "because"]

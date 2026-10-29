"""Diff: what differs between two models or two commits, and why calculated cells moved."""

import pytest

import tracemodel as tm

SRC = {"document": "Source doc", "url": "https://example.com/doc.pdf", "section": "2"}


@pytest.fixture
def repo():
    r = tm.Repo.memory("oren")
    m = r.model
    m.add_dimension("term", ["T1", "T2"])
    m.add_item("price", ("term",))
    m.add_item("qty", ("term",))
    m.add_item("revenue", ("term",), formula=lambda x: x["price"] * x["qty"])
    m.add_item("total", (), formula=lambda x: x["revenue"]["T1"] + x["revenue"]["T2"])
    with m.transaction():
        for t in ("T1", "T2"):
            m.set_value("price", {"term": t}, 10.0, SRC)
            m.set_value("qty", {"term": t}, 20.0, SRC)
    r.commit("terms")
    r.new_branch("what-if")
    r.checkout("what-if")
    return r


def test_no_differences(repo):
    d = repo.diff("main")
    assert not d and len(d) == 0 and "no differences" in str(d)


def test_entered_values_and_definitions(repo):
    m = repo.model
    m.set_value("qty", {"term": "T1"}, 40.0, SRC)
    m.clear_value("qty", {"term": "T2"})
    m.update_item("price", attrs={"scale": "thousands"})
    m.add_check("POS", coords={"term": tm.ANY}, expect=lambda x: x["price"] > 0)
    m.add_coords("term", ["T3"])
    repo.commit("what if")
    d = repo.diff("main", "what-if")
    assert [(c.target, c.change) for c in d] == [
        ("dimension:term", "changed"), ("item:price", "changed"), ("check:POS", "added"),
        ("value:qty[term=T1]", "changed"), ("value:qty[term=T2]", "removed")]
    qty = d.of("value")[0]
    assert qty.before["value"] == 20 and qty.after["value"] == 40
    assert "value:qty[term=T1]" in str(d)


def test_calculated_cells_that_moved_and_why(repo):
    repo.model.set_value("qty", {"term": "T1"}, 40.0, SRC)
    repo.model.update_item("price", attrs={"scale": "thousands"})  # moves nothing
    d = repo.diff("main", calculated=True)  # against the working model
    calc = {c.target: c for c in d.of("calc")}
    assert set(calc) == {"calc:revenue[term=T1]", "calc:total[]"}
    assert (calc["calc:revenue[term=T1]"].before, calc["calc:revenue[term=T1]"].after) == (200, 400)
    assert calc["calc:total[]"].because == ("value:qty[term=T1]",)


def test_a_formula_change_is_why(repo):
    repo.model.set_formula("revenue", lambda x: x["price"] * x["qty"] * 1.17)
    d = repo.diff("main", calculated=True)
    assert [c.target for c in d.of("calc")] == ["calc:revenue[term=T1]", "calc:revenue[term=T2]", "calc:total[]"]
    assert all(c.because == ("item:revenue",) for c in d.of("calc"))


def test_an_entered_value_over_a_formula(repo):
    repo.model.set_value("revenue", {"term": "T2"}, 1.0, SRC)
    d = repo.diff("main", calculated=True)
    assert [(c.target, c.change) for c in d] == [("value:revenue[term=T2]", "added"), ("calc:revenue[term=T2]", "removed"),
                                                 ("calc:total[]", "changed")]


def test_diff_two_models():
    a, b = tm.Model(), tm.Model()
    for m in (a, b):
        m.add_dimension("term", ["T1"])
        m.add_item("x", ("term",))
    b.set_value("x", {"term": "T1"}, 1.0, SRC)
    assert [str(c) for c in tm.diff(a, b)] == ["added    value:x[term=T1]: None -> 1.0"]
    assert tm.diff(a, b).table().columns == ["object", "change", "before", "after", "because"]

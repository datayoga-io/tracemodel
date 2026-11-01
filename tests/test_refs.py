"""Pointers: an item whose value is a label of another dimension, and formulas that follow one."""

import pytest

import tracemodel as tm

SRC = {"document": "Plan", "url": "https://example.com/plan.pdf", "section": "1"}


@pytest.fixture
def m():
    """Lines belong to engagements, and engagements to projects. Each project has a factor."""
    m = tm.Model()
    m.add_dimension("project", ["PA", "PB"])
    m.add_dimension("engagement", ["E1", "E2", "E3"])
    m.add_dimension("line", ["L1", "L2", "L3", "L4"])
    m.add_item("engagement_project", ("engagement",), dtype=tm.Ref("project"))
    m.add_item("line_engagement", ("line",), dtype=tm.Ref("engagement"))
    m.add_item("factor", ("project",))
    m.add_item("rate", ("line",))
    for e, p in [("E1", "PA"), ("E2", "PA"), ("E3", "PB")]:
        m.set_value("engagement_project", {"engagement": e}, p, SRC)
    for line, e, r in [("L1", "E1", 10), ("L2", "E1", 20), ("L3", "E2", 5), ("L4", "E3", 7)]:
        m.set_value("line_engagement", {"line": line}, e, SRC)
        m.set_value("rate", {"line": line}, r, SRC)
    m.set_value("factor", {"project": "PA"}, 2, SRC)
    m.set_value("factor", {"project": "PB"}, 100, SRC)
    return m


def add_amount(m):
    """A line's amount: its rate times the factor of its project, reached through two pointers."""
    m.add_item("amount", ("line",), formula=lambda x: x["rate"] * x["factor"].sel(
        project=x["engagement_project"].sel(engagement=x["line_engagement"])))


# == the type ==
def test_a_pointer_is_text_that_names_a_label(m):
    item = m.item("engagement_project")
    assert item.dtype is str and item.ref == "project"
    assert m.get("engagement_project", engagement="E3") == "PB"


def test_a_pointer_must_name_a_label_of_its_dimension(m):
    with pytest.raises(tm.InvalidValue, match=r"points at project: 'PZ' is not one of its labels \(labels: PA, PB\)"):
        m.set_value("engagement_project", {"engagement": "E1"}, "PZ", SRC)
    assert m.get("engagement_project", engagement="E1") == "PA"  # nothing changed


def test_a_pointer_takes_text_not_a_number(m):
    with pytest.raises(tm.InvalidValue, match="value must be text"):
        m.set_value("engagement_project", {"engagement": "E1"}, 1, SRC)


def test_a_pointer_to_an_unknown_dimension_is_refused():
    with pytest.raises(tm.DefinitionError, match=r"tm.Ref\('nowhere'\): no dimension"):
        tm.Model().add_item("p", (), dtype=tm.Ref("nowhere"))


def test_re_adding_the_same_pointer_is_a_no_op_and_a_different_one_is_refused(m):
    m.add_item("engagement_project", ("engagement",), dtype=tm.Ref("project"))
    with pytest.raises(tm.DefinitionError, match="already exists with a different definition"):
        m.add_item("engagement_project", ("engagement",), dtype=str)


def test_the_definition_round_trips_with_its_pointers(m):
    data = m.to_dict()
    assert next(i for i in data["items"] if i["name"] == "line_engagement")["ref"] == "engagement"
    again = tm.Model.from_dict(data)
    assert again.item("line_engagement").ref == "engagement"
    with pytest.raises(tm.InvalidValue, match="not one of its labels"):
        again.set_value("line_engagement", {"line": "L1"}, "E9", SRC)


def test_a_recorded_definition_replays_with_its_pointers():
    source = tm.Model()
    source.add_dimension("project", ["PA"])
    source.add_dimension("engagement", ["E1"])
    with source.recording() as ops:
        source.add_item("engagement_project", ("engagement",), dtype=tm.Ref("project"))
    target = tm.Model()
    target.add_dimension("project", ["PA"])
    target.add_dimension("engagement", ["E1"])
    for op in ops:
        tm.ops.apply(target, op)
    assert target.item("engagement_project").ref == "project"


def test_print_shows_the_pointer(m):
    assert "ref project" in str(m)


# == removing what a pointer names ==
def test_a_label_a_pointer_names_cannot_be_removed(m):
    with pytest.raises(tm.DefinitionError, match="still pointed at by engagement_project"):
        m.remove_coords("project", ["PB"])
    assert "PB" in m.dimensions["project"].coords
    m.set_value("engagement_project", {"engagement": "E3"}, "PA", SRC)
    m.remove_coords("project", ["PB"])
    assert m.dimensions["project"].coords == ("PA",)


def test_a_dimension_a_pointer_names_cannot_be_removed(m):
    with pytest.raises(tm.DefinitionError, match="items engagement_project, factor use it"):
        m.remove_dimension("project")


# == following a pointer ==
def test_a_formula_follows_pointers_to_the_project(m):
    add_amount(m)
    assert [m.get("amount", line=line) for line in ("L1", "L2", "L3", "L4")] == [20, 40, 10, 700]


def test_changing_a_pointer_moves_the_line_and_recalculates(m):
    add_amount(m)
    assert m.get("amount", line="L4") == 700
    m.set_value("engagement_project", {"engagement": "E3"}, "PA", SRC)  # E3 moves to project PA
    assert m.get("amount", line="L4") == 14
    m.set_value("line_engagement", {"line": "L4"}, "E1", SRC)
    assert m.get("amount", line="L4") == 14
    m.set_value("factor", {"project": "PA"}, 3, SRC)  # the target changes too
    assert m.get("amount", line="L4") == 21


def test_a_missing_pointer_makes_the_cell_missing(m):
    add_amount(m)
    m.clear_value("line_engagement", {"line": "L2"})
    with pytest.raises(tm.MissingValue):
        m.get("amount", line="L2")
    assert m.get("amount", line="L1") == 20


def test_a_pointer_can_be_followed_by_a_str_item_too(m):
    """Any str item holding a label works; tm.Ref also checks it."""
    m.add_item("which", ("line",), dtype=str)
    m.set_value("which", {"line": "L1"}, "PB", SRC)
    m.add_item("picked", ("line",), formula=lambda x: x["factor"].sel(project=x["which"]))
    assert m.get("picked", line="L1") == 100


def test_a_value_that_is_not_a_label_is_an_error_when_run(m):
    m.add_item("which", ("line",), dtype=str)
    m.set_value("which", {"line": "L1"}, "nowhere", SRC)
    m.add_item("picked", ("line",), formula=lambda x: x["factor"].sel(project=x["which"]))
    with pytest.raises(tm.UnknownCoord):
        m.get("picked", line="L1")


# == tracing ==
def test_precedents_name_the_pointer_and_every_cell_it_could_reach(m):
    add_amount(m)
    names = {(t.item if hasattr(t, "item") else t.name, tuple(t.coords.values())) for t in m.precedents("amount", line="L1")}
    assert ("line_engagement", ("L1",)) in names and ("rate", ("L1",)) in names
    assert {("factor", ("PA",)), ("factor", ("PB",))} <= names  # the whole target: which one is known only when run
    assert {("engagement_project", (e,)) for e in ("E1", "E2", "E3")} <= names


def test_dependents_of_a_target_include_the_lines_that_read_it_through_a_pointer(m):
    add_amount(m)
    deps = {tuple(t.coords.values()) for t in m.dependents("factor", project="PA") if t.name == "amount"}
    assert deps == {("L1",), ("L2",), ("L3",), ("L4",)}  # coarse: any line could point at PA


def test_a_formula_that_reads_itself_through_a_pointer_is_circular(m):
    m.add_item("next_line", ("line",), dtype=tm.Ref("line"))
    with pytest.raises(tm.DefinitionError, match="circular formula"):
        m.add_item("running", ("line",), formula=lambda x: x["rate"] + x["running"].sel(line=x["next_line"]))

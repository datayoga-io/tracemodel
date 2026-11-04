"""A dimension's title: what people call it, saved with the model and versioned."""

import pytest

import tracemodel as tm


@pytest.fixture
def m():
    m = tm.Model()
    m.add_dimension("unit_type", ["Studio", "2-bed"], title="Unit type")
    m.add_dimension("project", ["PA"])
    return m


def test_a_dimension_may_have_a_title(m):
    assert m.dimensions["unit_type"].title == "Unit type"
    assert m.dimensions["project"].title is None


def test_a_title_must_be_a_string():
    with pytest.raises(tm.DefinitionError, match="title must be a string"):
        tm.Model().add_dimension("x", ["a"], title=3)  # type: ignore[arg-type]


def test_the_title_is_saved_and_loaded(m, tmp_path):
    m.save(tmp_path / "m.json")
    assert tm.load(tmp_path / "m.json").dimensions["unit_type"].title == "Unit type"
    assert "title" not in next(d for d in m.to_dict()["dimensions"] if d["name"] == "project")


def test_adding_and_removing_labels_keeps_the_title(m):
    m.add_coords("unit_type", ["Retail"])
    assert m.dimensions["unit_type"].title == "Unit type"
    m.remove_coords("unit_type", ["Retail"])
    assert m.dimensions["unit_type"].title == "Unit type"


def test_re_adding_with_a_different_title_is_refused(m):
    m.add_dimension("unit_type", ["Studio", "2-bed"], title="Unit type")  # the same: a no-op
    with pytest.raises(tm.DefinitionError, match="different"):
        m.add_dimension("unit_type", ["Studio", "2-bed"], title="Kind of unit")


def test_set_dimension_title_changes_and_clears_it(m):
    m.set_dimension_title("project", "Project")
    assert m.dimensions["project"].title == "Project"
    m.set_dimension_title("project", None)
    assert m.dimensions["project"].title is None
    with pytest.raises(tm.UnknownCoord):
        m.set_dimension_title("nope", "x")


def test_a_title_change_is_a_versioned_change_and_can_be_discarded():
    repo = tm.Repo.memory("modeler")
    repo.model.add_dimension("unit_type", ["Studio"])
    repo.commit("dimensions")
    repo.model.set_dimension_title("unit_type", "Unit type")
    assert [op.kind for op in repo.status()] == ["dimension.update"]
    repo.discard()
    assert repo.model.dimensions["unit_type"].title is None
    repo.model.set_dimension_title("unit_type", "Unit type")
    repo.commit("name it")
    assert repo.model.dimensions["unit_type"].title == "Unit type"


def test_a_title_shows_in_a_diff(m):
    other = tm.Model.from_dict(m.to_dict()) if hasattr(tm.Model, "from_dict") else None
    if other is None:
        pytest.skip("no from_dict")
    other.set_dimension_title("unit_type", "Kind of unit")
    changes = tm.diff(m, other).changes
    assert [c.target for c in changes] == ["dimension:unit_type"]

"""A dimension's description and attrs: what people call it and what is known about it, saved with the model and versioned."""

import pytest

import tracemodel as tm


@pytest.fixture
def m():
    m = tm.Model()
    m.add_dimension("unit_type", ["Studio", "2-bed"], description="Unit type")
    m.add_dimension("project", ["PA"])
    return m


def test_a_dimension_may_have_a_title(m):
    assert m.dimensions["unit_type"].description == "Unit type"
    assert m.dimensions["project"].description is None


def test_a_title_must_be_a_string():
    with pytest.raises(tm.DefinitionError, match="description must be a string"):
        tm.Model().add_dimension("x", ["a"], description=3)  # type: ignore[arg-type]


def test_the_title_is_saved_and_loaded(m, tmp_path):
    m.save(tmp_path / "m.json")
    assert tm.load(tmp_path / "m.json").dimensions["unit_type"].description == "Unit type"
    assert "description" not in next(d for d in m.to_dict()["dimensions"] if d["name"] == "project")


def test_adding_and_removing_labels_keeps_the_title(m):
    m.add_coords("unit_type", ["Retail"])
    assert m.dimensions["unit_type"].description == "Unit type"
    m.remove_coords("unit_type", ["Retail"])
    assert m.dimensions["unit_type"].description == "Unit type"


def test_re_adding_with_a_different_title_is_refused(m):
    m.add_dimension("unit_type", ["Studio", "2-bed"], description="Unit type")  # the same: a no-op
    with pytest.raises(tm.DefinitionError, match="different"):
        m.add_dimension("unit_type", ["Studio", "2-bed"], description="Kind of unit")


def test_update_dimension_changes_and_clears_the_title(m):
    m.update_dimension("project", description="Project")
    assert m.dimensions["project"].description == "Project"
    m.update_dimension("project", description=None)
    assert m.dimensions["project"].description is None
    with pytest.raises(tm.UnknownCoord):
        m.update_dimension("nope", description="x")


def test_a_title_change_is_a_versioned_change_and_can_be_discarded():
    repo = tm.Repo.memory("modeler")
    repo.model.add_dimension("unit_type", ["Studio"])
    repo.commit("dimensions")
    repo.model.update_dimension("unit_type", description="Unit type")
    assert [op.kind for op in repo.status()] == ["dimension.update"]
    repo.discard()
    assert repo.model.dimensions["unit_type"].description is None
    repo.model.update_dimension("unit_type", description="Unit type")
    repo.commit("name it")
    assert repo.model.dimensions["unit_type"].description == "Unit type"


def test_a_title_shows_in_a_diff(m):
    other = tm.Model.from_dict(m.to_dict()) if hasattr(tm.Model, "from_dict") else None
    if other is None:
        pytest.skip("no from_dict")
    other.update_dimension("unit_type", description="Kind of unit")
    changes = tm.diff(m, other).changes
    assert [c.target for c in changes] == ["dimension:unit_type"]


def test_a_dimension_may_have_meta():
    m = tm.Model()
    m.add_dimension("unit_type", ["Studio"], attrs={"note": "Follows the sales brief", "grain": "one per type"})
    assert m.dimensions["unit_type"].attrs == {"note": "Follows the sales brief", "grain": "one per type"}
    assert m.dimensions["unit_type"]["Studio"].attrs == {}   # a label's attrs are apart
    with pytest.raises(tm.DefinitionError, match="attrs must be a dict"):
        tm.Model().add_dimension("x", ["a"], attrs="note")  # type: ignore[arg-type]


def test_meta_is_saved_kept_and_changed_alone(tmp_path):
    m = tm.Model()
    m.add_dimension("unit_type", ["Studio"], description="Unit type", attrs={"note": "n"})
    m.save(tmp_path / "m.json")
    loaded = tm.load(tmp_path / "m.json")
    assert loaded.dimensions["unit_type"].attrs == {"note": "n"}
    loaded.add_coords("unit_type", ["Retail"])
    assert loaded.dimensions["unit_type"].attrs == {"note": "n"}
    loaded.update_dimension("unit_type", attrs={"note": "changed"})   # the description is left alone
    assert loaded.dimensions["unit_type"].description == "Unit type" and loaded.dimensions["unit_type"].attrs == {"note": "changed"}
    loaded.update_dimension("unit_type", attrs=None)
    assert loaded.dimensions["unit_type"].attrs == {}


def test_the_text_view_shows_the_title_and_meta():
    m = tm.Model()
    m.add_dimension("unit_type", ["Studio"], description="Unit type", attrs={"note": "Follows the sales brief"})
    assert "unit_type  Unit type" in repr(m) and "Follows the sales brief" in repr(m)


def test_meta_change_is_a_versioned_change():
    repo = tm.Repo.memory("modeler")
    repo.model.add_dimension("unit_type", ["Studio"])
    repo.commit("dimensions")
    repo.model.update_dimension("unit_type", attrs={"note": "n"})
    assert [op.kind for op in repo.status()] == ["dimension.update"]
    repo.discard()
    assert repo.model.dimensions["unit_type"].attrs == {}

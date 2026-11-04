"""A label is an object with a description and attrs that still works as its plain text."""

import copy
import json
import pickle
import warnings

import pytest

import tracemodel as tm

SRC = {"document": "Plan", "url": "https://example.com/plan.pdf", "section": "1"}


@pytest.fixture
def dim():
    return tm.Model().add_dimension(
        "unit_type", [("Studio", "Studio apartment", {"beds": 0}), ("2-bed", None, {"beds": 2}), "Retail"])


def test_a_label_has_its_description_attrs_and_dimension(dim):
    studio = dim["Studio"]
    assert isinstance(studio, tm.Label)
    assert studio.description == "Studio apartment"
    assert studio.attrs == {"beds": 0}
    assert studio.dimension == "unit_type"


def test_a_label_with_no_description_says_its_code_and_none_has_no_attrs(dim):
    assert dim["Retail"].description == "Retail" and dim["Retail"].attrs == {}
    assert dim["2-bed"].description == "2-bed"


def test_a_hierarchy_label_falls_back_to_its_last_segment():
    h = tm.Model().add_dimension("dept", tm.Hierarchy(["/", "/rnd", ("/rnd/web", "Web team")]))
    assert h["/rnd"].description == "rnd" and h["/rnd/web"].description == "Web team"


def test_an_unknown_label_is_refused(dim):
    with pytest.raises(tm.UnknownCoord, match="not a coord of 'unit_type'"):
        dim["Penthouse"]


def test_attrs_are_read_only(dim):
    with pytest.raises(TypeError):
        dim["Studio"].attrs["beds"] = 9  # type: ignore[index]


def test_dim_labels_lists_them_in_order(dim):
    assert [str(x) for x in dim.labels] == ["Studio", "2-bed", "Retail"]
    assert all(isinstance(x, tm.Label) for x in dim.labels)
    assert dim.coords == ("Studio", "2-bed", "Retail") and all(type(c) is str for c in dim.coords)


def test_a_label_is_its_text(dim):
    studio = dim["Studio"]
    assert studio == "Studio" and "Studio" == studio
    assert hash(studio) == hash("Studio")
    assert {"Studio": 1}[studio] == 1 and {studio: 1}["Studio"] == 1
    assert isinstance(studio, str) and f"{studio}!" == "Studio!" and studio + "?" == "Studio?"
    assert repr(studio) == "'Studio'" and str(studio) == "Studio" and type(str(studio)) is str
    assert sorted([dim["Retail"], dim["Studio"], dim["2-bed"]]) == ["2-bed", "Retail", "Studio"]
    assert json.dumps({"label": studio}) == '{"label": "Studio"}'


def test_a_label_survives_copy_and_pickle(dim):
    for clone in (copy.deepcopy(dim["Studio"]), pickle.loads(pickle.dumps(dim["Studio"]))):
        assert clone == "Studio" and clone.description == "Studio apartment" and clone.attrs == {"beds": 0}


def test_labels_follow_the_dimension_when_labels_are_added():
    m = tm.Model()
    m.add_dimension("unit_type", ["Studio"])
    m.add_coords("unit_type", [("Retail", "Shops", {"floor": 0})])
    assert m.dimensions["unit_type"]["Retail"].description == "Shops"
    assert m.dimensions["unit_type"]["Retail"].attrs == {"floor": 0}


def test_a_label_goes_wherever_a_label_string_does(dim):
    m = tm.Model()
    m.add_dimension("unit_type", ["Studio", "2-bed"])
    m.add_item("count", ("unit_type",))
    m.add_item("double", ("unit_type",), formula=lambda x: x["count"] * 2)
    label = m.dimensions["unit_type"]["Studio"]
    m.set_value("count", {"unit_type": label}, 4, SRC)
    assert m.get("count", unit_type="Studio") == 4
    assert m.get("double", unit_type=label) == 8
    stored = next(iter(m._entries))[1]
    assert type(stored[0]) is str   # kept as plain text, so formulas see what they always saw


def test_a_formula_may_use_text_methods_on_a_label_it_reads():
    m = tm.Model()
    m.add_dimension("unit_type", ["Studio"])
    m.add_item("shout", ("unit_type",), dtype=str, formula=lambda x: x.coords["unit_type"].upper())
    assert m.get("shout", unit_type="Studio") == "STUDIO"


def test_a_dimension_has_its_own_description_and_attrs_apart_from_its_labels():
    d = tm.Dimension("unit_type", [("Studio", "Studio apartment", {"beds": 0})], "Unit type", {"note": "n"})
    assert (d.description, dict(d.attrs)) == ("Unit type", {"note": "n"})
    assert (d["Studio"].description, dict(d["Studio"].attrs)) == ("Studio apartment", {"beds": 0})


def test_the_library_raises_no_deprecation_warnings(dim, tmp_path):
    m = tm.Model()
    m.add_dimension("unit_type", [("Studio", "Studio apartment", {"beds": 0}), "Retail"])
    m.add_item("count", ("unit_type",), formula=lambda x: x.attrs("unit_type").get("beds", 1))
    m.add_check("beds", lambda x: x["count"] >= 0, {"unit_type": tm.filter_by_attrs(beds=0)})
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        repr(m)
        m.get("count", unit_type="Studio")
        m.to_dict()
        m.save(tmp_path / "m.json")
        tm.load(tmp_path / "m.json")

"""Tests for tracemodel, driven by the Apple FY2024-FY2025 example."""

import json
import sys
from pathlib import Path

import pytest

import tracemodel as tm
from tracemodel.expr import Condition

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from apple import SO, USD_M, build_model, enter_values

TOTAL_FY24 = {"sales_category": "/", "period": "FY2024"}


@pytest.fixture
def empty():
    return build_model()


@pytest.fixture
def model():
    return enter_values(build_model())


# -- 1. dimensions -----------------------------------------------------------

def test_dimensions(empty):
    assert empty.dimensions["period"].coords == ("FY2024", "FY2025")
    sales = empty.dimensions["sales_category"].hierarchy
    assert sales.children("/") == ["/products", "/services"]
    assert sales.children("/products") == [
        "/products/iphone", "/products/mac", "/products/ipad", "/products/wearables",
    ]
    assert sales.parent("/products/iphone") == "/products"
    assert empty.dimensions["expense_category"].hierarchy.parent("/rnd") is None


def test_coord_entries_string_or_tuple(empty):
    sales = empty.dimensions["sales_category"]
    assert sales.description("/") == "Total"                                          # (code, description)
    assert sales.description("/products/iphone") == "iphone"                          # plain path
    period = empty.dimensions["period"]                                               # (code, description, attrs)
    assert period.description("FY2025") == "Fiscal 2025"
    assert period.attrs("FY2025") == {"end": "2025-09-27"}
    assert sales.attrs("/services") == {}
    assert tm.Dimension("d", ["a", ("b", "Bee")]).description("a") == "a"


@pytest.mark.parametrize("entry", [
    {"id": "/x", "description": "X"},      # dicts are not entries
    ("/x",),                               # 1-tuple
    ("/x", "X", {}, "extra"),              # 4-tuple
    ("/x", "X", "not attrs"),
    ["/x", "X"],                           # a list, not a tuple
])
def test_bad_coord_entries_rejected(entry):
    with pytest.raises(tm.DefinitionError, match="must be a string"):
        tm.Hierarchy(["/", entry])
    with pytest.raises(tm.DefinitionError, match="must be a string"):
        tm.Dimension("d", ["a", entry])


def test_hierarchy_requires_declared_parent():
    with pytest.raises(tm.DefinitionError, match="parent '/products'"):
        tm.Hierarchy(["/", "/products/iphone"])


def test_re_adding_same_dimension_is_a_no_op(empty):
    empty.add_dimension("period", [
        ("FY2024", "Fiscal 2024", {"end": "2024-09-28"}),
        ("FY2025", "Fiscal 2025", {"end": "2025-09-27"}),
    ])
    with pytest.raises(tm.DefinitionError, match="different coords"):
        empty.add_dimension("period", ["FY2024", "FY2025"])
    with pytest.raises(tm.DefinitionError, match="different coords"):
        empty.add_dimension("period", ["FY2026"])


# -- 2. items scaffold -------------------------------------------------------

def test_items_have_dtype_and_attrs(empty):
    assert set(empty.items) == {
        "net_sales", "cost_of_sales", "gross_margin",
        "operating_expense", "shares_basic", "earnings_per_share",
    }
    assert empty.items["net_sales"].attrs == USD_M
    assert empty.items["shares_basic"].dtype is int
    assert empty.items["earnings_per_share"].dtype is float
    assert empty.items["shares_basic"].unit_label == "shares thousands"


def test_item_needs_declared_dims():
    with pytest.raises(tm.DefinitionError, match="add_dimension first"):
        tm.Model().add_item("net_sales", dims=("period",))


def test_item_dtype_must_be_int_or_float(empty):
    with pytest.raises(tm.DefinitionError, match="dtype"):
        empty.add_item("x", dims=("period",), dtype=str)


def test_re_adding_same_item_is_a_no_op(empty):
    empty.add_item("net_sales", dims=("sales_category", "period"), dtype=float, attrs=USD_M)
    with pytest.raises(tm.DefinitionError, match="different definition"):
        empty.add_item("net_sales", dims=("period",))


# -- 3. data quality ---------------------------------------------------------

def test_check_definition(empty):
    check = empty.checks["DQ-1"]
    assert repr(check.expect) == (
        "abs(net_sales['/'] - (net_sales['/products'] + net_sales['/services'])) <= 0.2"
    )
    assert check.runs() == [{"period": "FY2024"}, {"period": "FY2025"}]


def test_add_check_replaces_same_id(empty):
    ns = empty["net_sales"]
    empty.add_check("DQ-1", coords={"period": "FY2025"},
                    expect=ns["/"] == ns["/products"] + ns["/services"])
    assert len(empty.checks) == 1
    assert empty.checks["DQ-1"].runs() == [{"period": "FY2025"}]


def test_check_with_unresolved_dim_rejected(empty):
    ns = empty["net_sales"]
    with pytest.raises(tm.DefinitionError, match="period unresolved"):
        empty.add_check("bad", expect=ns["/"] == ns["/services"])


def test_check_coords_must_be_open_in_some_cell(empty):
    ns = empty["net_sales"]
    fixed = ns.sel(sales_category="/", period="FY2025")
    with pytest.raises(tm.DefinitionError, match="remove it from coords"):
        empty.add_check("bad", coords={"period": tm.ANY}, expect=fixed == fixed)  # noqa: PLR0124 - builds a condition
    assert empty.add_check("ok", expect=fixed == fixed).runs()  # noqa: PLR0124 == [{}]


def test_sum_and_negation_display(empty):
    ns = empty["net_sales"]
    total = sum([ns["/products"], ns["/services"]])
    assert repr(total) == "net_sales['/products'] + net_sales['/services']"
    assert repr(-ns["/services"]) == "-net_sales['/services']"
    assert repr(-total) == "-(net_sales['/products'] + net_sales['/services'])"


def test_unknown_label_rejected(empty):
    with pytest.raises(tm.UnknownCoord):
        empty["net_sales"]["/products/vision_pro"]


# -- 4. values ---------------------------------------------------------------

def test_all_values_entered(model):
    assert len(model) == 34
    assert model.get("net_sales", sales_category="/products/iphone", period="FY2025") == 209_586
    assert model.get("earnings_per_share", period="FY2024") == 6.11
    assert model.entry("net_sales", sales_category="/", period="FY2025").source == SO


def test_dq1_passes(model):
    report = model.check()
    assert report.ok
    assert len(report.results) == 2
    fy25 = report.results[1]
    assert (fy25.lhs, fy25.rhs) == (0, 0.2)   # abs(total - parts) <= 0.2
    assert [c.value for c in fy25.cells] == [416_161, 307_003, 109_158]


def test_dq1_tolerance_is_inclusive(model):
    model.set_value("net_sales", TOTAL_FY24, 391_035.2, source=SO)
    assert model.check().ok
    model.set_value("net_sales", TOTAL_FY24, 391_035.3, source=SO)
    (fail,) = model.check().failures
    assert fail.at == {"period": "FY2024"}
    assert (fail.lhs, fail.rhs) == (pytest.approx(0.3), 0.2)


def test_missing_value_fails_check():
    m = build_model()
    m.set_value("net_sales", TOTAL_FY24, 391_035, source=SO)
    m.set_value("net_sales", {"sales_category": "/products", "period": "FY2024"}, 294_866, source=SO)
    (r24, r25) = m.check().results
    assert [c.coords["sales_category"] for c in r24.missing] == ["/services"]
    assert len(r25.missing) == 3


@pytest.mark.parametrize("item, coords, error", [
    ("revenue", {"period": "FY2025"}, tm.UnknownItem),
    ("net_sales", {"sales_category": "/", "period": "FY2023"}, tm.UnknownCoord),
    ("net_sales", {"sales_category": "/products/vision_pro", "period": "FY2025"}, tm.UnknownCoord),
    ("net_sales", {"period": "FY2025"}, tm.DimMismatch),
    ("operating_expense", {"sales_category": "/", "expense_category": "/rnd", "period": "FY2025"}, tm.DimMismatch),
])
def test_set_value_rejects_bad_coords(empty, item, coords, error):
    with pytest.raises(error):
        empty.set_value(item, coords, 1, source=SO)


def test_set_value_last_write_wins(model):
    coords = {"sales_category": "/", "period": "FY2025"}
    model.set_value("net_sales", coords, 1, source=SO)
    e = model.entry("net_sales", **coords)
    assert e.value == 1 and e.history == [(416_161, SO)]


@pytest.mark.parametrize("item, value, source", [
    ("earnings_per_share", "7.49", SO),
    ("earnings_per_share", True, SO),
    ("shares_basic", 14_948_500.5, SO),                       # int item
    ("earnings_per_share", 7.49, "10-K"),                     # source not a dict
    ("earnings_per_share", 7.49, {"document": "10-K"}),       # missing url, section
    ("earnings_per_share", 7.49, SO | {"author": "x"}),       # unknown key
])
def test_set_value_rejects_bad_value_or_source(empty, item, value, source):
    with pytest.raises(tm.InvalidValue):
        empty.set_value(item, {"period": "FY2025"}, value, source)


def test_source_accepts_page_and_quote(empty):
    empty.set_value("earnings_per_share", {"period": "FY2025"}, 7.49, SO | {"page": 28, "quote": "Basic $ 7.49"})


def test_entry_without_coords_is_a_table(model):
    t = model.entry("earnings_per_share")
    assert isinstance(t, tm.Table)
    assert t.columns == ["period", "value", "source", "replaced"]
    assert [r[:2] for r in t.rows] == [["FY2024", 6.11], ["FY2025", 7.49]]
    assert len(model.entry("net_sales", period="FY2025")) == 7


# -- display and arithmetic --------------------------------------------------

def test_table_grid_and_html(model):
    t = model.table("net_sales")
    assert t.title == "net_sales (USD millions)"
    assert t.columns == ["sales_category", "FY2024", "FY2025"]
    assert t.rows[0] == ["/", 391_035, 416_161]
    assert "<table>" in t._repr_html_() and "391,035" in t._repr_html_()
    assert "391,035" in str(t)


def test_item_arithmetic_broadcasts(model):
    t = model.evaluate(model["net_sales"]["/"] / model["shares_basic"])
    assert t.columns == ["", "FY2024", "FY2025"]
    assert t.rows[0][2] == pytest.approx(416_161 / 14_948_500)

    by_cat = model.evaluate(model["net_sales"] / model["shares_basic"])   # (sales_category, period)
    assert len(by_cat) == 7
    assert by_cat.rows[2][0] == "/products/iphone"


def test_scalar_expression(model):
    assert model.evaluate(model["net_sales"].sel(sales_category="/", period="FY2025")) == 416_161


def test_comparison_shows_true_false_per_period(model):
    t = model.evaluate(model["net_sales"]["/"] == model["net_sales"]["/products"] + model["net_sales"]["/services"])
    assert [r[0] for r in t.rows] == ["FY2024", "FY2025"]
    assert [r[-1] for r in t.rows] == [True, True]
    with pytest.raises(tm.DefinitionError):
        bool(model["net_sales"]["/"] == 1)


def test_missing_cells_show_blank(model):
    t = model.table("cost_of_sales")
    iphone = next(r for r in t.rows if r[0] == "/products/iphone")
    assert iphone[1:] == [None, None]


def test_jsonl_round_trip(model, tmp_path):
    path = tmp_path / "income_statement.jsonl"
    model.to_jsonl(path)
    assert len(path.read_text().splitlines()) == 34
    loaded = build_model()
    loaded.load_jsonl(path)
    assert len(loaded) == 34
    assert loaded.get("shares_basic", period="FY2024") == 15_343_783
    assert loaded.entry("earnings_per_share", period="FY2025").source == SO
    assert loaded.check().ok


# -- conditions: any comparison, joined with & | ~ ----------------------------

def test_inequality_check_runs_per_coord(model):
    ns = model["net_sales"]
    model.add_check("SIGN-1", coords={"sales_category": tm.ANY, "period": tm.ANY}, expect=ns >= 0)
    sign = [r for r in model.check().results if r.check.id == "SIGN-1"]
    assert len(sign) == 14 and all(r.passed for r in sign)
    model.set_value("net_sales", {"sales_category": "/products/mac", "period": "FY2025"}, -1, source=SO)
    (fail,) = model.check().failures
    assert fail.at == {"sales_category": "/products/mac", "period": "FY2025"}
    assert (fail.lhs, fail.check.expect.rule, fail.rhs) == (-1, ">=", 0)


def test_and_or_not(model):
    gm, ns = model["gross_margin"], model["net_sales"]
    band = (gm["/"] / ns["/"] >= 0.30) & (gm["/"] / ns["/"] <= 0.60)
    assert repr(band) == "(gross_margin['/'] / net_sales['/'] >= 0.3) & (gross_margin['/'] / net_sales['/'] <= 0.6)"
    model.add_check("RANGE-1", coords={"period": tm.ANY}, expect=band)
    model.add_check("NOT-1", coords={"period": tm.ANY}, expect=~(ns["/services"] > ns["/products"]))
    model.add_check("OR-1", coords={"period": tm.ANY},
                    expect=(model["operating_expense"]["/rnd"] > 0) | (model["operating_expense"]["/rnd"] == 0))
    assert model.check().ok
    t = model.evaluate(band)
    assert t.columns[-1] == "result" and [r[-1] for r in t.rows] == [True, True]


@pytest.mark.parametrize("build", [
    lambda ns: 0 <= ns["/"] <= 1,                    # chained comparison calls bool()
    lambda ns: (ns["/"] > 0) and (ns["/"] < 1),      # `and` calls bool()
    lambda ns: ns["/"] >= 0 & ns["/"] >= 0,          # & binds before >=
    lambda ns: (ns["/"] > 0) & 1,
])
def test_python_pitfalls_explain_themselves(empty, build):
    with pytest.raises(tm.DefinitionError, match="parentheses"):
        build(empty["net_sales"])


def test_expect_must_be_a_condition(empty):
    with pytest.raises(tm.DefinitionError, match="`expect` must be a condition"):
        empty.add_check("bad", coords={"period": tm.ANY}, expect=empty["net_sales"]["/"])


def test_print_model_shows_descriptions_and_attrs(empty):
    text = repr(empty)
    assert "    FY2025   Fiscal 2025   end=2025-09-27" in text
    assert "  sales_category (hierarchy)" in text
    assert "/products/wearables   Wearables, Home and Accessories" in text
    assert "net_sales            (sales_category, period)  float  units=USD scale=millions" in text


# -- function checks: expect(dim, ...) builds the condition for each label ----

def test_function_check_builds_one_condition_per_label(model):
    ns = model["net_sales"]
    sales = model.dimensions["sales_category"].hierarchy
    check = model.add_check("NS-SUM",
        coords={"sales_category": ["/", "/products"], "period": tm.ANY},
        expect=lambda sales_category: abs(ns[sales_category] - sum(ns[c] for c in sales.children(sales_category))) <= 0.2)
    assert check.params == ("sales_category",)
    assert repr(check.condition({"sales_category": "/products", "period": "FY2024"})) == (
        "abs(net_sales['/products'] - (net_sales['/products/iphone'] + net_sales['/products/mac']"
        " + net_sales['/products/ipad'] + net_sales['/products/wearables'])) <= 0.2")
    runs = [r for r in model.check().results if r.check.id == "NS-SUM"]
    assert [r.at for r in runs] == [
        {"sales_category": "/", "period": "FY2024"}, {"sales_category": "/", "period": "FY2025"},
        {"sales_category": "/products", "period": "FY2024"}, {"sales_category": "/products", "period": "FY2025"},
    ]
    assert all(r.passed for r in runs)
    assert len(runs[2].cells) == 5          # /products and its four children, FY2024
    model.set_value("net_sales", {"sales_category": "/products/mac", "period": "FY2025"}, 1, source=SO)
    (fail,) = model.check().failures
    assert fail.at == {"sales_category": "/products", "period": "FY2025"}
    assert "NS-SUM" in repr(model) and "expect(sales_category)" in repr(model)


def test_function_check_with_two_dims(model):
    ns = model["net_sales"]
    model.add_check("ORD-1",
        coords={"sales_category": ["/products", "/services"], "period": tm.ANY},
        expect=lambda sales_category, period: ns.sel(sales_category=sales_category, period=period)
                                              <= ns.sel(sales_category="/", period=period))
    assert model.check().ok
    assert len(model.checks["ORD-1"].conditions) == 4


@pytest.mark.parametrize("expect, match", [
    (lambda region: None, "not a dim in coords"),
    (lambda *dims: None, r"\*args"),
    (lambda: None, "at least one dim"),
    (lambda sales_category: 1.0, "must return a condition"),
])
def test_function_check_errors(empty, expect, match):
    with pytest.raises(tm.DefinitionError, match=match):
        empty.add_check("bad", coords={"sales_category": ["/"], "period": tm.ANY}, expect=expect)


# -- lag / lead / shift: the same item at an earlier or later label ------------

def test_lag_lead_shift_display(empty):
    ns = empty["net_sales"]
    assert repr(ns["/"].lag("period")) == "net_sales['/'].lag('period')"
    assert repr(ns["/"].shift(period=1)) == "net_sales['/'].lag('period')"
    assert repr(ns["/"].lead("period", 2)) == "net_sales['/'].lead('period', 2)"
    assert repr(ns["/"].lag("period").lead("period")) == "net_sales['/']"


def test_lag_check_reads_the_prior_period(model):
    ns = model["net_sales"]
    check = model.add_check("GROWTH-1",
        coords={"period": model.dimensions["period"].coords[1:]},
        expect=ns["/"] > ns["/"].lag("period"))
    assert check.runs() == [{"period": "FY2025"}]
    (run,) = [r for r in model.check().results if r.check.id == "GROWTH-1"]
    assert run.passed and (run.lhs, run.rhs) == (416_161, 391_035)
    assert [c.coords["period"] for c in run.cells] == ["FY2025", "FY2024"]


def test_lag_past_the_first_label_is_a_definition_error(empty):
    ns = empty["net_sales"]
    with pytest.raises(tm.DefinitionError, match=r"no period before 'FY2024'.*coords\[1:\]"):
        empty.add_check("bad", coords={"period": tm.ANY}, expect=ns["/"] > ns["/"].lag("period"))
    with pytest.raises(tm.DefinitionError, match=r"no period after 'FY2025'.*coords\[:-1\]"):
        empty.add_check("bad", coords={"period": tm.ANY}, expect=ns["/"] < ns["/"].lead("period"))
    empty.add_check("ok", coords={"period": empty.dimensions["period"].coords[:-1]},
                    expect=ns["/"] < ns["/"].lead("period"))


def test_lag_displays_blank_for_the_first_period(model):
    t = model.evaluate(model["net_sales"]["/"].lag("period"))
    assert t.rows[0][1:] == [None, 391_035]


def test_shift_rejects_hierarchies_and_fractions(empty):
    with pytest.raises(tm.DefinitionError, match="hierarchy"):
        empty["net_sales"].shift(sales_category=1)
    with pytest.raises(tm.DefinitionError, match="whole number"):
        empty["net_sales"].shift(period=0.5)


def _nil_model():
    m = tm.Model()
    m.add_dimension("segment", tm.Hierarchy([("/", "Total"), "/a", "/corp"]))
    m.add_dimension("period", ["FY24"])
    m.add_item("cost", ("segment", "period"), dtype=float)
    m.add_check("SUM", coords={"period": tm.ANY},
                expect=m["cost"]["/"] == m["cost"]["/a"] + m["cost"]["/corp"])
    for seg, v in [("/", 5), ("/a", 5)]:
        m.set_value("cost", {"segment": seg, "period": "FY24"}, v, SO)
    return m


def test_not_entered_is_missing_nil_counts_as_zero():
    m = _nil_model()
    assert m.check().table().rows[0][2] == "missing"         # /corp not entered yet
    m.set_value("cost", {"segment": "/corp", "period": "FY24"}, tm.NIL, SO)
    assert m.check().ok                                       # "—" counts as 0
    assert m.get("cost", segment="/corp", period="FY24") == 0
    assert m.entry("cost", segment="/corp", period="FY24").value is tm.NIL


def test_nil_shows_as_dash_and_round_trips(tmp_path):
    m = _nil_model()
    m.set_value("cost", {"segment": "/corp", "period": "FY24"}, tm.NIL, SO)
    assert str(m.table("cost")).splitlines()[-1].split() == ["/corp", "—"]
    assert "cost[segment=/corp, period=FY24] = —" in m.check().explain()
    path = tmp_path / "v.jsonl"
    m.to_jsonl(path)
    assert '"value": null' in path.read_text()
    fresh = tm.Model()
    fresh.add_dimension("segment", tm.Hierarchy([("/", "Total"), "/a", "/corp"]))
    fresh.add_dimension("period", ["FY24"])
    fresh.add_item("cost", ("segment", "period"), dtype=float)
    fresh.load_jsonl(path)
    assert fresh.entry("cost", segment="/corp", period="FY24").value is tm.NIL


def test_nil_then_a_number():
    m = _nil_model()
    m.set_value("cost", {"segment": "/corp", "period": "FY24"}, tm.NIL, SO)
    m.set_value("cost", {"segment": "/corp", "period": "FY24"}, 2.0, SO)
    assert m.get("cost", segment="/corp", period="FY24") == 2.0


def _formula_model():
    m = tm.Model()
    m.add_dimension("period", ["FY24", "FY25"])
    m.add_item("sales", ("period",))
    m.add_item("cogs", ("period",))
    m.add_item("gm_pct", ("period",), formula=(m["sales"] - m["cogs"]) / m["sales"])
    m.add_check("GM", coords={"period": tm.ANY}, expect=m["gm_pct"] >= 0.3)
    for p, a, b in [("FY24", 100, 60), ("FY25", 200, 100)]:
        m.set_value("sales", {"period": p}, a, SO)
        m.set_value("cogs", {"period": p}, b, SO)
    return m


def test_formula_item_is_calculated_and_traced():
    m = _formula_model()
    assert m.get("gm_pct", period="FY25") == 0.5
    assert m.table("gm_pct").rows[0][1:] == [0.4, 0.5]
    r = m.check()
    assert r.ok
    assert [c.item for c in r.results[0].cells] == ["gm_pct", "sales", "cogs"]   # the formula's inputs
    assert "= (sales - cogs) / sales" in repr(m)


def test_formula_missing_input_is_missing():
    m = tm.Model()
    m.add_dimension("period", ["FY24"])
    m.add_item("sales", ("period",))
    m.add_item("double", ("period",), formula=m["sales"] * 2)
    with pytest.raises(tm.MissingValue):
        m.get("double", period="FY24")
    m.add_check("D", coords={"period": tm.ANY}, expect=m["double"] > 0)
    assert m.check().table().rows[0][2] == "missing"


def test_formula_override_with_set_value(tmp_path):
    m = _formula_model()
    m.set_value("gm_pct", {"period": "FY24"}, 0.45, SO)
    assert m.get("gm_pct", period="FY24") == 0.45
    assert [c.item for c in m.check().results[0].cells] == ["gm_pct"]          # entered: no inputs
    path = tmp_path / "v.jsonl"
    m.to_jsonl(path)
    m2 = tm.Model()
    m2.add_dimension("period", ["FY24", "FY25"])
    m2.add_item("sales", ("period",))
    m2.add_item("cogs", ("period",))
    m2.add_item("gm_pct", ("period",), formula=(m2["sales"] - m2["cogs"]) / m2["sales"])
    m2.load_jsonl(path)
    assert m2.entry("gm_pct", period="FY24").overrides_formula


@pytest.mark.parametrize("formula, match", [
    (lambda m: m["sales"] > 1, "must be an expression"),
    (lambda m: 3, "must be an expression"),
    (lambda m: m["sales"], "open in period"),
])
def test_formula_errors(formula, match):
    m = tm.Model()
    m.add_dimension("period", ["FY24"])
    m.add_item("sales", ("period",))
    with pytest.raises(tm.DefinitionError, match=match):
        m.add_item("x", (), formula=formula(m))


def test_formula_redefinition():
    m = _formula_model()
    m.add_item("gm_pct", ("period",), formula=(m["sales"] - m["cogs"]) / m["sales"])   # same: no-op
    with pytest.raises(tm.DefinitionError):
        m.add_item("gm_pct", ("period",), formula=m["sales"] / m["cogs"])


def test_is_nil_tells_nil_from_zero():
    m = _nil_model()
    m.add_check("NIL-1", coords={"period": tm.ANY}, expect=m["cost"]["/corp"].is_nil())
    assert m.check().table().rows[1][2] == "missing"                    # not entered isn't nil
    m.set_value("cost", {"segment": "/corp", "period": "FY24"}, 0.0, SO)
    assert m.check().table().rows[1][2] == "FAIL"                       # a typed 0 isn't nil
    m.set_value("cost", {"segment": "/corp", "period": "FY24"}, tm.NIL, SO)
    assert m.check().ok
    assert repr(m.checks["NIL-1"].expect) == "cost['/corp'].is_nil()"
    assert m.evaluate(~m["cost"].is_nil()).rows[-1][-1] is False       # /corp, negated


def test_warning_checks_dont_fail_the_report():
    m = _formula_model()
    m.add_check("GM-HIGH", coords={"period": tm.ANY}, expect=m["gm_pct"] >= 0.45, severity="warning")
    r = m.check()
    assert r.ok and len(r.failures) == 1 and len(r.warnings) == 1 and r.errors == []
    assert "warn" in [row[2] for row in r.table().rows]
    assert r.table().title.endswith("1 warning")
    assert "(warning)" in repr(m)
    m.add_check("GM-LOW", coords={"period": tm.ANY}, expect=m["gm_pct"] >= 0.45)
    assert not m.check().ok
    with pytest.raises(tm.DefinitionError, match="severity"):
        m.add_check("X", coords={"period": tm.ANY}, expect=m["gm_pct"] >= 0, severity="info")


def test_save_and_load_round_trip(tmp_path, model):
    rev = model["net_sales"]
    cats = model.dimensions["sales_category"].hierarchy
    model.add_check("ROLLUP", coords={"sales_category": ["/", "/products"], "period": tm.ANY},
                    expect=lambda sales_category: abs(rev[sales_category] - sum(
                        rev[c] for c in cats.children(sales_category))) <= 1)
    model.add_check("GROWTH", coords={"period": model.dimensions["period"].coords[1:]},
                    expect=rev["/"] >= rev["/"].lag("period"), severity="warning", description="grows")
    model.add_item("growth", ("period",), formula=rev["/"] / rev["/"].lag("period") - 1)
    model.save(tmp_path / "m.json")
    model.to_jsonl(tmp_path / "v.jsonl")
    loaded = tm.load(tmp_path / "m.json")
    loaded.load_jsonl(tmp_path / "v.jsonl")
    assert repr(loaded) == repr(model)
    assert loaded.to_dict() == model.to_dict()
    assert str(loaded.check().table()) == str(model.check().table())
    assert loaded.check().explain() == model.check().explain()
    assert str(loaded.table("growth")) == str(model.table("growth"))
    saved = loaded.to_dict()
    assert {c["id"]: c["coords"] for c in saved["checks"]}["ROLLUP"]["period"] == "ANY"
    assert loaded.checks["ROLLUP"].expect(sales_category="/products") is loaded.checks["ROLLUP"].conditions[("/products",)]


@pytest.mark.parametrize("text, match", [
    ("__import__('os').system('true')", "not part of the expression grammar"),
    ("net_sales.__class__", "not part of the expression grammar"),
    ("(lambda: 1)()", "not part of the expression grammar"),
    ("net_sales['/'].evaluate_table()", "not part of the expression grammar"),
    ("open_file", "not a declared item"),
    ("abs(1) <", "can't read expression"),
    ("net_sales['/'].lag(period)", "must be a label or a whole number"),
])
def test_load_reads_only_the_expression_grammar(model, text, match):
    from tracemodel.text import parse
    with pytest.raises(tm.DefinitionError, match=match):
        parse(text, model)


def test_load_rejects_other_files_and_bad_attrs(tmp_path, model):
    with pytest.raises(tm.DefinitionError, match="not a tracemodel definition"):
        tm.Model.from_dict({"format": "other"})
    model.add_item("odd", ("period",), attrs={"when": object()})
    with pytest.raises(tm.DefinitionError, match="JSON values"):
        model.save(tmp_path / "m.json")


def test_repr_uses_sel_when_a_label_is_in_two_dims():
    m = tm.Model()
    m.add_dimension("a", ["x", "y"])
    m.add_dimension("b", ["x", "z"])
    m.add_item("v", ("a", "b"))
    cell = m["v"].sel(a="x")
    assert repr(cell) == "v.sel(a='x')"
    from tracemodel.text import parse
    assert parse(repr(cell), m).coords == {"a": "x"}


def _projection_model():
    m = tm.Model()
    m.add_dimension("period", ["FY2024", "FY2025", "FY2026", "FY2027"])
    m.add_dimension("contract", ["C-101", "C-104"])
    m.add_item("revenue", ("period",), formula=lambda revenue: revenue.lag("period") * 1.03)
    m.set_value("revenue", {"period": "FY2024"}, 391_035, SO)
    m.set_value("revenue", {"period": "FY2025"}, 416_161, SO)
    m.add_item("fee", ("contract", "period"), formula=lambda fee: fee.lag("period") * 1.02)
    for c in ("C-101", "C-104"):
        m.set_value("fee", {"contract": c, "period": "FY2024"}, 100, SO)
    return m


def test_formula_reads_its_own_earlier_cells():
    m = _projection_model()
    assert m.get("revenue", period="FY2027") == pytest.approx(416_161 * 1.03 ** 2)
    assert repr(m.items["revenue"].formula) == "revenue.lag('period') * 1.03"
    m.add_check("G", coords={"period": m.dimensions["period"].coords[1:]},
                expect=m["revenue"] >= m["revenue"].lag("period"))
    run = next(r for r in m.check().results if r.at == {"period": "FY2027"})
    assert [c.coords["period"] for c in run.cells] == ["FY2027", "FY2026", "FY2025"]   # back to the actuals


def test_first_period_not_entered_is_missing():
    m = tm.Model()
    m.add_dimension("period", ["FY2024", "FY2025"])
    m.add_item("revenue", ("period",), formula=lambda revenue: revenue.lag("period") * 1.03)
    with pytest.raises(tm.MissingValue):
        m.get("revenue", period="FY2025")


def test_cell_formula_with_set_value():
    m = _projection_model()
    msa = {"document": "MSA C-104", "url": "https://example.com/msa", "section": "4.2"}
    m.set_value("fee", {"contract": "C-104", "period": "FY2026"}, lambda fee: fee.lag("period") * 1.10, msa)
    assert m.get("fee", contract="C-104", period="FY2026") == pytest.approx(112.2)
    assert m.get("fee", contract="C-104", period="FY2027") == pytest.approx(112.2 * 1.02)  # item formula after
    assert m.get("fee", contract="C-101", period="FY2026") == pytest.approx(104.04)
    m.set_value("fee", {"contract": "C-101", "period": "FY2025"}, m["fee"]["C-104"] * 2, SO)  # an expression works too
    assert m.get("fee", contract="C-101", period="FY2025") == pytest.approx(204)
    assert "= fee.lag('period') * 1.1" in str(m.entry("fee"))


@pytest.mark.parametrize("make", [
    lambda m: m.add_item("x", ("period",), formula=lambda x: x.lag("period") + x.lead("period")),
    lambda m: m.add_item("y", ("period",), formula=lambda y: y * 2),
    lambda m: m.set_value("revenue", {"period": "FY2026"}, m["revenue"].lead("period"), SO),
])
def test_circular_formulas_are_rejected(make):
    m = _projection_model()
    before = (set(m.items), len(m))
    with pytest.raises(tm.DefinitionError, match="circular formula"):
        make(m)
    assert (set(m.items), len(m)) == before                      # nothing half-added


def test_projection_save_and_load(tmp_path):
    m = _projection_model()
    msa = {"document": "MSA C-104", "url": "https://example.com/msa", "section": "4.2"}
    m.set_value("fee", {"contract": "C-104", "period": "FY2026"}, lambda fee: fee.lag("period") * 1.10, msa)
    m.save(tmp_path / "m.json")
    m.to_jsonl(tmp_path / "v.jsonl")
    assert '"formula": "fee.lag(\'period\') * 1.1"' in (tmp_path / "v.jsonl").read_text()
    loaded = tm.load(tmp_path / "m.json")
    loaded.load_jsonl(tmp_path / "v.jsonl")
    assert repr(loaded) == repr(m)
    assert str(loaded.table("fee")) == str(m.table("fee"))
    assert str(loaded.table("revenue")) == str(m.table("revenue"))


def test_add_coords_appends_and_reexpands_any_checks(tmp_path):
    m = _projection_model()
    rev = m["revenue"]
    m.add_check("POS", coords={"period": tm.ANY}, expect=rev >= 0)
    m.add_check("FIRST2", coords={"period": ["FY2024", "FY2025"]}, expect=rev >= 0)
    m.add_check("FEE", coords={"contract": tm.ANY, "period": tm.ANY},
                expect=lambda contract: m["fee"][contract] >= 0)
    m.add_coords("period", [("FY2028", "Fiscal 2028", {"kind": "forecast"}), "FY2029"])
    assert m.dimensions["period"].coords[-2:] == ("FY2028", "FY2029")
    assert m.dimensions["period"].attrs("FY2028") == {"kind": "forecast"}
    assert len(m.checks["POS"].runs()) == 6 and len(m.checks["FIRST2"].runs()) == 2
    assert len(m.checks["FEE"].runs()) == 12
    assert m.get("revenue", period="FY2029") == pytest.approx(416_161 * 1.03 ** 4)
    m.add_coords("period", [("FY2028", "Fiscal 2028", {"kind": "forecast"})])      # same again: no-op
    with pytest.raises(tm.DefinitionError, match="different description or attrs"):
        m.add_coords("period", [("FY2028", "FY 28")])


def test_add_coords_to_a_hierarchy_needs_the_parent():
    m = tm.Model()
    m.add_dimension("line", tm.Hierarchy(["/a", "/a/x"]))
    m.add_coords("line", ["/a/y", ("/b", "B")])
    assert m.dimensions["line"].hierarchy.children("/a") == ["/a/x", "/a/y"]
    with pytest.raises(tm.DefinitionError, match="parent"):
        m.add_coords("line", ["/c/z"])
    assert "/c/z" not in m.dimensions["line"]


def test_add_coords_refuses_a_loaded_function_check_and_changes_nothing(tmp_path):
    m = _projection_model()
    m.add_check("FEE", coords={"contract": tm.ANY, "period": tm.ANY},
                expect=lambda contract: m["fee"][contract] >= 0)
    m.add_check("POS", coords={"period": tm.ANY}, expect=m["revenue"] >= 0)
    m.save(tmp_path / "m.json")
    loaded = tm.load(tmp_path / "m.json")
    loaded.add_coords("period", ["FY2028"])                       # FEE's function dim is contract: fine
    assert len(loaded.checks["FEE"].runs()) == 10 and len(loaded.checks["POS"].runs()) == 5
    with pytest.raises(tm.DefinitionError, match="add it again with its function"):
        loaded.add_coords("contract", ["C-200"])
    assert "C-200" not in loaded.dimensions["contract"]
    assert len(loaded.checks["FEE"].runs()) == 10


def test_remove_dimension_only_when_no_item_uses_it():
    m = _projection_model()
    m.add_dimension("scenario", ["base", "bull"])
    m.remove_dimension("scenario")
    assert "scenario" not in m.dimensions
    with pytest.raises(tm.DefinitionError, match="items fee use it"):
        m.remove_dimension("contract")


def test_calculated_cells_are_cached_until_a_change():
    m = _projection_model()
    assert m.get("revenue", period="FY2027") == pytest.approx(416_161 * 1.03 ** 2)
    assert ("revenue", ("FY2027",)) in m._cache
    m.set_value("revenue", {"period": "FY2025"}, 400_000, SO)       # a change clears the cache
    assert m.get("revenue", period="FY2027") == pytest.approx(400_000 * 1.03 ** 2)


def test_long_chains_are_linear():
    m = tm.Model()
    m.add_dimension("period", [f"P{i:03}" for i in range(300)])
    m.add_item("a", ("period",), formula=lambda a: a.lag("period") * 1.001)
    m.add_item("b", ("period",), formula=lambda b: b.lag("period") + m["a"] + m["a"].lag("period"))
    m.set_value("a", {"period": "P000"}, 1, SO)
    m.set_value("b", {"period": "P000"}, 0, SO)
    assert m.get("b", period="P299") > 0                            # would take ~2^300 steps uncached


def test_transaction_defers_cycle_checks_to_commit():
    m = tm.Model()
    m.add_dimension("period", ["FY2025", "FY2026"])
    m.add_item("a", ("period",), formula=lambda a: m["b"] if "b" in m.items else a.lag("period"))
    m.add_item("b", ("period",), formula=m["a"] * 2)
    m.set_value("a", {"period": "FY2025"}, 1, SO)
    # outside a transaction, a formula that loops for now is rejected at once
    with pytest.raises(tm.DefinitionError, match="circular"):
        m.set_value("a", {"period": "FY2026"}, m["b"] + 1, SO)
    # inside one, the loop only has to be gone by commit
    with m.transaction():
        m.set_value("a", {"period": "FY2026"}, m["b"] + 1, SO)     # a -> b -> a, for now
        m.set_value("b", {"period": "FY2026"}, 10, SO)             # b entered: the loop is gone
    assert m.get("a", period="FY2026") == 11


def test_transaction_rolls_back_on_failed_commit_or_exception():
    m = _projection_model()
    before = (repr(m), len(m), m.get("revenue", period="FY2027"))
    with pytest.raises(tm.DefinitionError, match="circular"), m.transaction():
        m.add_coords("period", ["FY2028"])
        m.set_value("revenue", {"period": "FY2024"}, 1, SO)
        m.set_value("revenue", {"period": "FY2026"}, m["revenue"].lead("period"), SO)
    assert (repr(m), len(m), m.get("revenue", period="FY2027")) == before
    with pytest.raises(ZeroDivisionError), m.transaction():
        m.set_value("revenue", {"period": "FY2024"}, 1, SO)
        raise ZeroDivisionError
    assert (repr(m), len(m), m.get("revenue", period="FY2027")) == before
    tx = m.transaction()
    m.set_value("revenue", {"period": "FY2024"}, 1, SO)
    tx.rollback()
    assert len(m) == before[1] and m._tx is None
    with pytest.raises(tm.DefinitionError, match="already committed or rolled back"):
        tx.commit()


def test_one_transaction_at_a_time_and_reads_inside_see_changes():
    m = _projection_model()
    with m.transaction():
        with pytest.raises(tm.DefinitionError, match="already open"):
            m.transaction()
        m.set_value("revenue", {"period": "FY2025"}, 500_000, SO)
        assert m.get("revenue", period="FY2026") == pytest.approx(515_000)
        m.set_value("revenue", {"period": "FY2026"}, m["revenue"].lead("period"), SO)
        with pytest.raises(tm.DefinitionError, match="circular formula at"):  # read while looping
            m.get("revenue", period="FY2026")
        m.set_value("revenue", {"period": "FY2026"}, 1, SO)


def test_load_jsonl_is_all_or_nothing(tmp_path):
    m = _projection_model()
    m.to_jsonl(tmp_path / "v.jsonl")
    with open(tmp_path / "v.jsonl", "a") as f:
        row = {"line_item": "revenue", "coords": {"period": "FY2099"}, "value": 1, "source": SO}
        f.write(json.dumps(row) + "\n")
    fresh = tm.Model()
    fresh.add_dimension("period", ["FY2024", "FY2025", "FY2026", "FY2027"])
    fresh.add_dimension("contract", ["C-101", "C-104"])
    fresh.add_item("revenue", ("period",))
    fresh.add_item("fee", ("contract", "period"))
    with pytest.raises(tm.UnknownCoord):
        fresh.load_jsonl(tmp_path / "v.jsonl")
    assert len(fresh) == 0


FLOOR = 25_000


def _cash_model():
    m = tm.Model()
    m.add_dimension("period", ["FY2026", "FY2027", "FY2028"])
    m.add_item("cash", ("period",))
    for p, v in [("FY2026", 40_000), ("FY2027", 18_000), ("FY2028", 26_000)]:
        m.set_value("cash", {"period": p}, v, SO)
    return m


def test_function_formula_runs_per_cell_with_plain_python():
    m = _cash_model()
    m.add_item("revolver", ("period",), formula=lambda x: max(0, FLOOR - x["cash"]))
    m.add_item("tax", ("period",), formula=lambda x: (x["cash"] - 30_000) * 0.2 if x["cash"] > 30_000 else 0)
    m.add_item("label", ("period",), formula=lambda x: 1 if x.coords["period"] == "FY2027" else 0)
    assert m.table("revolver").rows[0][1:] == [0, 7_000, 0]
    assert m.table("tax").rows[0][1:] == [2_000, 0, 0]
    assert m.table("label").rows[0][1:] == [0, 1, 0]
    assert 'lambda x: max(0, FLOOR - x["cash"])' in repr(m)
    m.add_check("RV", coords={"period": tm.ANY}, expect=m["revolver"] >= 0)
    run = m.check().results[1]
    assert [(c.item, c.value) for c in run.cells] == [("revolver", 7_000), ("cash", 18_000)]


def test_function_in_the_expression_grammar_becomes_an_expression():
    m = _cash_model()
    m.add_item("spare", ("period",), formula=lambda x: x["cash"] - FLOOR)
    m.add_item("grow", ("period",), formula=lambda g: g.lag("period") * 1.03)
    assert repr(m.items["spare"].formula) == "cash - 25000"
    assert repr(m.items["grow"].formula) == "grow.lag('period') * 1.03"
    with pytest.raises(tm.DefinitionError, match="circular"):           # expressions keep add-time checks
        m.add_item("bad", ("period",), formula=lambda b: b * 2)


def test_function_formula_missing_input_and_cell_formula():
    m = _cash_model()
    m.add_item("debt", ("period",))
    m.add_item("net", ("period",), formula=lambda x: max(0, x["cash"] - x["debt"]))
    assert m.table("net").rows[0][1:] == [None, None, None]                   # debt not entered
    m.set_value("debt", {"period": "FY2027"}, 20_000, SO)
    m.set_value("net", {"period": "FY2028"}, lambda x: min(x["cash"], 1_000), SO)   # a cell formula
    assert m.table("net").rows[0][1:] == [None, 0, 1_000]
    assert "= lambda x: min(x[\"cash\"], 1_000)" in str(m.entry("net"))


def test_function_formula_long_chain_and_loop():
    m = tm.Model()
    m.add_dimension("period", [f"P{i:04}" for i in range(2_000)])
    m.add_item("a", ("period",), formula=lambda x: max(0, x.lag("period") + 1))
    m.set_value("a", {"period": "P0000"}, 0, SO)
    assert m.get("a", period="P1999") == 1_999
    m.add_item("b", ("period",), formula=lambda x: max(0, x["b"] + 1))     # reads itself
    with pytest.raises(tm.DefinitionError, match="circular formula"):
        m.get("b", period="P0001")


def test_function_formulas_save_and_load_with_allow_code(tmp_path):
    m = _cash_model()
    m.add_item("revolver", ("period",), formula=lambda x: max(0, FLOOR - x["cash"]))
    m.add_item("log_cash", ("period",), formula=lambda x: __import__("math").log(x["cash"]) if x["cash"] > 0 else 0)
    m.set_value("revolver", {"period": "FY2028"}, lambda x: min(FLOOR, x["cash"]), SO)
    m.save(tmp_path / "m.json")
    m.to_jsonl(tmp_path / "v.jsonl")
    saved = (tmp_path / "m.json").read_text()
    assert '"python": "lambda x: max(0, FLOOR - x[\\"cash\\"])"' in saved and '"FLOOR": 25000' in saved
    with pytest.raises(tm.DefinitionError, match="allow_code=True"):
        tm.load(tmp_path / "m.json")
    loaded = tm.load(tmp_path / "m.json", allow_code=True)
    with pytest.raises(tm.DefinitionError, match="allow_code=True"):
        loaded.load_jsonl(tmp_path / "v.jsonl")
    assert len(loaded) == 0                                                # nothing half-loaded
    loaded.load_jsonl(tmp_path / "v.jsonl", allow_code=True)
    assert str(loaded.table("revolver")) == str(m.table("revolver"))
    assert str(loaded.table("log_cash")) == str(m.table("log_cash"))


def test_function_formula_that_cannot_be_saved():
    m = _cash_model()
    helper = object()
    m.add_item("odd", ("period",), formula=lambda x: max(0, x["cash"]) if helper else 0)
    with pytest.raises(tm.DefinitionError, match="it uses 'helper'"):
        m.to_dict()
    m2 = _cash_model()
    m2.add_item("f", ("period",), formula=eval('lambda x: max(0, x["cash"])'))   # no source file
    assert m2.get("f", period="FY2026") == 40_000
    with pytest.raises(tm.DefinitionError, match="source isn't available"):
        m2.to_dict()


def _actuals_and_forecast():
    m = tm.Model()
    m.add_dimension("period", [("FY2024", None, {"kind": "actual"}), ("FY2025", None, {"kind": "actual"}),
                               ("FY2026", None, {"kind": "forecast"})])
    m.add_item("revenue", ("period",), formula=lambda r: r.lag("period") * 1.03)
    m.add_item("notes", ("period",))                           # reported detail, never forecast
    for p, v in [("FY2024", 391_035), ("FY2025", 416_161)]:
        m.set_value("revenue", {"period": p}, v, SO)
    return m


def test_check_coords_filter_by_attrs():
    m = _actuals_and_forecast()
    FORECAST = tm.filter_by_attrs(kind="forecast")
    m.add_check("FC-POS", coords={"period": FORECAST}, expect=m["revenue"] > 0)
    m.add_check("NOTES", coords={"period": tm.filter_by_attrs(kind="actual")}, expect=m["notes"] >= 0)
    assert [r["period"] for r in m.checks["FC-POS"].runs()] == ["FY2026"]
    assert "per period (kind=forecast)" in repr(m)
    m.add_coords("period", [("FY2027", None, {"kind": "forecast"}), ("FY2028", None, {"kind": "plan"})])
    assert [r["period"] for r in m.checks["FC-POS"].runs()] == ["FY2026", "FY2027"]   # follows new labels
    assert [r["period"] for r in m.checks["NOTES"].runs()] == ["FY2024", "FY2025"]
    with pytest.raises(tm.DefinitionError, match="at least one attr"):
        tm.filter_by_attrs()


def test_report_filter():
    m = _actuals_and_forecast()
    m.add_check("POS", coords={"period": tm.ANY}, expect=m["revenue"] > 0)
    m.add_check("NOTES", coords={"period": tm.ANY}, expect=m["notes"] >= 0)    # missing in every year
    report = m.check()
    assert not report.ok
    forecast = report.filter(check="POS", period=tm.filter_by_attrs(kind="forecast"))
    assert forecast.ok and [r.at for r in forecast.results] == [{"period": "FY2026"}]
    assert len(report.filter(period=["FY2024", "FY2025"]).results) == 4
    assert len(report.filter(check="NOTES").results) == 3
    with pytest.raises(tm.UnknownCoord):
        report.filter(year="FY2026")


def test_filter_by_attrs_saves_and_loads(tmp_path):
    m = _actuals_and_forecast()
    m.add_check("FC-POS", coords={"period": tm.filter_by_attrs(kind="forecast")}, expect=m["revenue"] > 0)
    m.save(tmp_path / "m.json")
    assert '"coords": {"period": {"filter_by_attrs": {"kind": "forecast"}}}' in (tmp_path / "m.json").read_text()
    loaded = tm.load(tmp_path / "m.json")
    loaded.add_coords("period", [("FY2027", None, {"kind": "forecast"})])
    assert [r["period"] for r in loaded.checks["FC-POS"].runs()] == ["FY2026", "FY2027"]


def test_x_attrs_lets_one_item_formula_cover_forecast_lines():
    m = tm.Model()
    m.add_dimension("period", [("FY2025", None, {"kind": "actual"}), ("FY2026", None, {"kind": "forecast"})])
    m.add_dimension("bs_line", tm.Hierarchy(["/current", "/current/receivables", "/current/inventory", "/other"]))
    m.add_item("sales", ("period",))
    m.add_item("cogs", ("period",))
    for p, s_, c in [("FY2025", 365, 200), ("FY2026", 730, 400)]:
        m.set_value("sales", {"period": p}, s_, SO)
        m.set_value("cogs", {"period": p}, c, SO)
    lines = {
        "/current/receivables": lambda x: x["sales"] * 30 / 365,
        "/current/inventory": lambda x: x["cogs"] * 73 / 365,
        "/current": lambda x: x["/current/receivables"] + x["/current/inventory"],
    }
    m.add_item("bs", ("bs_line", "period"),
               formula=lambda x: lines[x.coords["bs_line"]](x)
               if x.attrs("period")["kind"] == "forecast" and x.coords["bs_line"] in lines else None)
    m.set_value("bs", {"bs_line": "/current/receivables", "period": "FY2025"}, 31, SO)
    t = {row[0]: row[1:] for row in m.table("bs").rows}
    assert t["/current/receivables"] == [31, 60]                 # entered actual; forecast calculated
    assert t["/current/inventory"] == [None, 80]                 # no actual entered: missing, not forecast
    assert t["/current"] == [None, 140]
    assert t["/other"] == [None, None]
    with pytest.raises(tm.DefinitionError, match="has no 'year' label"):
        m.add_item("bad", ("period",), formula=lambda x: 1 if x.attrs("year") else 0)
        m.get("bad", period="FY2025")


def test_int_item_formula_results_must_be_whole():
    m = tm.Model()
    m.add_dimension("period", ["FY2025", "FY2026"])
    m.add_item("buyback", ("period",))
    m.add_item("price", ("period",))
    m.set_value("buyback", {"period": "FY2026"}, 10_000.0, SO)
    m.set_value("price", {"period": "FY2026"}, 3.0, SO)
    m.add_item("shares", ("period",), dtype=int, formula=m["buyback"] / m["price"])
    with pytest.raises(tm.DefinitionError, match=r"shares\[period=FY2026\] is int, .* gave 3333.33"):
        m.get("shares", period="FY2026")
    m2 = tm.Model()
    m2.add_dimension("period", ["FY2026"])
    m2.add_item("a", ("period",))
    m2.set_value("a", {"period": "FY2026"}, 0.1, SO)
    m2.add_item("tenths", ("period",), dtype=int, formula=(m2["a"] * 3 + 0.7) * 10)   # 9.999999999999998
    assert m2.get("tenths", period="FY2026") == 10 and isinstance(m2.get("tenths", period="FY2026"), int)
    m2.add_item("rounded", ("period",), dtype=int, formula=lambda x: round(x["a"] * 12_345))
    assert m2.get("rounded", period="FY2026") == 1_234


# == value checks: expect=lambda x: ... ======================================

def _margins():
    m = tm.Model()
    m.add_dimension("period", [("FY2024", None, {"kind": "actual"}), ("FY2025", None, {"kind": "actual"}),
                               ("FY2026", None, {"kind": "forecast"})])
    m.add_item("margin", ("period",))
    m.add_item("cash", ("period",))
    for p, v in [("FY2024", 0.46), ("FY2025", 1.2)]:
        m.set_value("margin", {"period": p}, v, SO)
    return m


def test_value_check_pass_and_fail_with_plain_python():
    m = _margins()
    m.add_check("MARGIN", coords={"period": ["FY2024", "FY2025"]}, expect=lambda x: 0 <= x["margin"] <= 1)
    check = m.checks["MARGIN"]
    assert not isinstance(check.expect, Condition)               # chained comparison: kept as code
    by_period = {r.at["period"]: r for r in m.check().results}
    assert by_period["FY2024"].passed and not by_period["FY2025"].passed
    r = by_period["FY2025"]
    assert (r.lhs, r.rhs, r.diff) == (None, None, None)
    assert [(c.item, c.coords, c.value) for c in r.cells] == [("margin", {"period": "FY2025"}, 1.2)]
    explained = m.check().explain("MARGIN")
    assert 'lambda x: 0 <= x["margin"] <= 1' in explained and "margin[period=FY2025] = 1.2" in explained
    assert 'lambda x: 0 <= x["margin"] <= 1' in repr(m)


def test_value_check_and_or_max_if_else_and_coords():
    m = _margins()
    m.set_value("cash", {"period": "FY2024"}, 10, SO)
    m.add_check("MIX", coords={"period": ["FY2024"]},
                expect=lambda x: (x["margin"] > 0 and x["cash"] > 0) or not x["cash"] >= 0)
    m.add_check("BRANCH", coords={"period": tm.ANY},
                expect=lambda x: True if x.attrs("period")["kind"] == "forecast"
                else max(0, x["margin"]) <= 1 and x.coords["period"].startswith("FY"))
    report = m.check()
    assert report.filter(check="MIX").ok
    assert [r.passed for r in report.filter(check="BRANCH").results] == [True, False, True]


def test_value_check_in_the_expression_grammar_stays_an_expression():
    m = _margins()
    m.add_check("POS", coords={"period": ["FY2024"]}, expect=lambda x: x["margin"] >= 0)
    assert isinstance(m.checks["POS"].expect, Condition)        # keeps left, right and diff
    r = m.check().results[0]
    assert r.passed and (r.lhs, r.rhs) == (0.46, 0)


def test_value_check_missing_cell_fails_and_is_listed():
    m = _margins()
    m.add_check("CASH", coords={"period": tm.ANY}, expect=lambda x: x["cash"] >= 0 or x["margin"] > 0)
    r = m.check().results[0]
    assert not r.passed and [(c.item, c.value) for c in r.missing] == [("cash", None)]
    assert "missing" in str(m.check())


def test_value_check_non_bool_or_exception_fails_with_the_error():
    m = _margins()
    m.add_check("NUM", coords={"period": ["FY2024"]}, expect=lambda x: x["margin"] * 2 if x.coords else 0)
    m.add_check("BOOM", coords={"period": ["FY2024"]}, expect=lambda x: x["margin"] / 0 > 1 or x.coords)
    m.add_check("TYPO", coords={"period": ["FY2024"]}, expect=lambda x: x["margn"] > 0 or x.coords)
    by_id = {r.check.id: r for r in m.check().results}
    assert not by_id["NUM"].passed and "must return True or False" in by_id["NUM"].error
    assert not by_id["BOOM"].passed and by_id["BOOM"].error.startswith("ZeroDivisionError")
    assert not by_id["TYPO"].passed and "no item 'margn'" in by_id["TYPO"].error
    assert "error: ZeroDivisionError" in m.check().explain("BOOM")


def test_value_check_rejects_a_dim_named_x():
    m = tm.Model()
    m.add_dimension("x", ["a", "b"])
    m.add_item("v", ("x",))
    with pytest.raises(tm.DefinitionError, match="x is also a dim in coords"):
        m.add_check("X", coords={"x": tm.ANY}, expect=lambda x: m["v"][x] >= 0)


def test_value_check_follows_add_coords_and_filter_by_attrs():
    m = _margins()
    m.add_check("FC", coords={"period": tm.filter_by_attrs(kind="forecast")},
                expect=lambda x: not x["margin"] >= 1)
    m.add_coords("period", [("FY2027", None, {"kind": "forecast"})])
    assert [r["period"] for r in m.checks["FC"].runs()] == ["FY2026", "FY2027"]
    m.set_value("margin", {"period": "FY2027"}, 0.5, SO)
    assert [r.passed for r in m.check().results] == [False, True]   # FY2026 not entered: missing


def test_value_check_warning_severity():
    m = _margins()
    m.add_check("SANE", coords={"period": ["FY2024", "FY2025"]}, severity="warning",
                expect=lambda x: 0 <= x["margin"] <= 1)
    report = m.check()
    assert report.ok and [r.at["period"] for r in report.warnings] == ["FY2025"]
    assert "warn" in str(report)


def test_value_check_save_and_load_needs_allow_code(tmp_path):
    m = _margins()
    LIMIT = 1
    m.add_check("MARGIN", coords={"period": tm.ANY}, expect=lambda x: 0 <= x["margin"] <= LIMIT)
    m.save(tmp_path / "m.json")
    data = json.loads((tmp_path / "m.json").read_text())
    saved = data["checks"][0]["expect"]
    assert saved == {"python": 'lambda x: 0 <= x["margin"] <= LIMIT', "env": {"LIMIT": 1}}
    with pytest.raises(tm.DefinitionError, match="allow_code=True"):
        tm.load(tmp_path / "m.json")
    loaded = tm.load(tmp_path / "m.json", allow_code=True)
    for p, v in [("FY2024", 0.46), ("FY2025", 1.2), ("FY2026", 0.3)]:
        loaded.set_value("margin", {"period": p}, v, SO)
    assert [r.passed for r in loaded.check().results] == [True, False, True]
    loaded.add_coords("period", [("FY2027", None, {"kind": "forecast"})])   # re-expands without the code
    assert len(loaded.checks["MARGIN"].runs()) == 4

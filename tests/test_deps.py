"""What a formula can read, found from its source, and model.precedents / model.dependents."""

import itertools

import pytest

import tracemodel as tm
from tracemodel.deps import NeedsLabels, precedents
from tracemodel.formula import Formula

SRC = {"document": "Plan", "url": "https://example.com/plan.pdf", "section": "1"}


@pytest.fixture
def m():
    m = tm.Model()
    m.add_dimension("period", tm.period_range("2026-01", periods=3, freq="M"))
    m.add_dimension("region", ["east", "west"])
    m.add_dimension("line", tm.Hierarchy(["/", "/a", "/b", "/b/x"]))
    for name, dims in [("revenue", ("region", "period")), ("flag", ("region", "period")), ("a", ("region", "period")),
                       ("b", ("region", "period")), ("which", ()), ("n", ()), ("fee", ()), ("start", ()),
                       ("end", ()), ("sales", ("line",)), ("on", ("line",))]:
        m.add_item(name, dims, dtype="date" if name in ("start", "end") else float)
    return m


def reads(m, source, item=None, **at):
    """The cells `source` reads for a cell of `item` at `at`, as item[labels] text."""
    f = Formula(source)
    it = m.items[item] if item else None
    found = precedents(f, m, it, at or None)
    out = []
    for c in found.cells:
        try:
            full = c.resolve(at)
        except tm.DefinitionError:
            out.append(f"{c!r} (out of range)")
            continue
        out.append(f"{c.item.name}[{','.join(full[d] for d in c.item.dims)}]")
    return out


AT = {"region": "east", "period": "2026-03"}


@pytest.mark.parametrize("source, expected", [
    ("lambda x: x['revenue'] * 0.6", ["revenue[east,2026-03]"]),
    ("lambda x: x['revenue'].lag('period')", ["revenue[east,2026-02]"]),
    ("lambda x: x['a'] if x['flag'] else x['b']", ["flag[east,2026-03]", "a[east,2026-03]", "b[east,2026-03]"]),
    ("lambda x: x['a'] if x.coords['region'] == 'east' else x['b']", ["a[east,2026-03]"]),
    ("lambda x: x['fee'] * x.period.overlap(x['start'], x['end'])", ["fee[]", "start[]", "end[]"]),
    ("lambda x: x['revenue'].sel(region='west') - x['revenue']", ["revenue[west,2026-03]", "revenue[east,2026-03]"]),
    ("lambda x: max(0, x['a'] - x['b']) or x['revenue']", ["a[east,2026-03]", "b[east,2026-03]", "revenue[east,2026-03]"]),
])
def test_static_reads(m, source, expected):
    assert reads(m, source, "revenue", **AT) == expected


def test_a_template_needs_no_labels_until_the_formula_uses_them(m):
    assert [repr(c) for c in precedents(Formula("lambda x: x['revenue'].lag('period')"), m, None, None).cells] == [
        "revenue.lag('period')"]
    with pytest.raises(NeedsLabels):
        precedents(Formula("lambda x: x['a'] if x.coords['region'] == 'east' else x['b']"), m, None, None)


def test_the_labels_a_walk_used_are_recorded(m):
    f = Formula("lambda x: x['a'] if x.coords['region'] == 'east' else x['b']")
    assert precedents(f, m, m.items["revenue"], AT).labels == {"region"}
    assert precedents(Formula("lambda x: x.period.days"), m, None, AT).labels == {"period"}


def test_first_period_lag_is_out_of_range_not_a_read(m):
    assert reads(m, "lambda x: x['revenue'].lag('period')", "revenue", region="east", period="2026-01") == [
        "revenue.lag('period') (out of range)"]


def test_rollup_reads_the_label_and_its_children(m):
    source = "lambda x: abs(x['sales'] - sum(x['sales'].sel(line=c) for c in x.children('line'))) <= 0.5"
    assert reads(m, source, None, line="/") == ["sales[/]", "sales[/a]", "sales[/b]"]
    assert reads(m, source, None, line="/b/x") == ["sales[/b/x]"]


def test_a_filter_on_a_value_counts_as_a_branch(m):
    f = Formula("lambda x: sum(x['sales'].sel(line=c) for c in x.labels('line') if x['on'].sel(line=c))")
    found = precedents(f, m, None, None)
    assert found.branched
    assert {c.item.name for c in found.cells} == {"sales", "on"} and len(found.cells) == 8


@pytest.mark.parametrize("source", [
    "lambda x: x[x['which']]",
    "lambda x: x['revenue'].lag('period', x['n'])",
    "lambda x: x['revenue'].sel(region=x['which'])",
    "lambda x: sum(1 for c in x['revenue'])",
])
def test_reads_that_depend_on_a_value_are_refused(m, source):
    with pytest.raises(tm.DefinitionError, match="depends on a value|can't loop over a cell"):
        m.add_item("t", ("region", "period"), formula=source)


def test_every_static_read_is_a_superset_of_the_run(m):
    m.add_item("mixed", ("region", "period"),
               formula=lambda x: x["a"] if x["flag"] else x["b"] + (x.lag("period") if x.coords["period"] != "2026-01" else 0))
    with m.transaction():
        for r, p in itertools.product(m.dimensions["region"].coords, m.dimensions["period"].coords):
            for name, v in [("a", 1), ("b", 2), ("flag", p != "2026-02")]:
                m.set_value(name, {"region": r, "period": p}, float(v), SRC)
    for r, p in itertools.product(m.dimensions["region"].coords, m.dimensions["period"].coords):
        m.get("mixed", region=r, period=p)
        ran = {(c.item.name, tuple(c.resolve(at).values())) for c, at in m._deps[("mixed", (r, p))]}
        static = {(t.name, tuple(t.coords.values())) for t in m.precedents("mixed", region=r, period=p)}
        assert ran <= static


# -- model.precedents and model.dependents ---------------------------------------

@pytest.fixture
def statements():
    m = tm.Model()
    m.add_dimension("period", ["FY2025", "FY2026"])
    for name in ("revenue", "cogs", "opex", "tax_rate", "opening_cash"):
        m.add_item(name, ("period",))
    m.add_item("gross_profit", ("period",), formula=lambda x: x["revenue"] - x["cogs"])
    m.add_item("ebit", ("period",), formula=lambda x: x["gross_profit"] - x["opex"])
    m.add_item("net_income", ("period",), formula=lambda x: x["ebit"] * (1 - x["tax_rate"]))
    m.add_item("cash", ("period",), formula=lambda x: x["opening_cash"] + x["net_income"])
    m.add_check("GP-POS", coords={"period": tm.ANY}, expect=lambda x: x["gross_profit"] >= 0)
    return m


def names(traces):
    return [str(t) for t in traces]


def test_one_level_and_deep_precedents(statements):
    assert names(statements.precedents("net_income", period="FY2025")) == [
        "ebit[period=FY2025]", "tax_rate[period=FY2025]"]
    (ebit, tax) = statements.precedents("net_income", deep=True, period="FY2025")
    assert [t.name for t in ebit.flatten()] == ["gross_profit", "opex", "revenue", "cogs"]
    assert tax.children == []                                    # entered: the trace ends there
    assert str(ebit).splitlines() == [
        "ebit[period=FY2025]", "  gross_profit[period=FY2025]", "    revenue[period=FY2025]",
        "    cogs[period=FY2025]", "  opex[period=FY2025]"]


def test_dependents_include_checks_and_reach_cash(statements):
    found = statements.dependents("revenue", period="FY2025")
    assert names(found) == ["gross_profit[period=FY2025]"]
    deep = statements.dependents("revenue", deep=True, period="FY2025")
    everything = {(t.kind, t.name) for d in deep for t in [d, *d.flatten()]}
    assert {("cell", "cash"), ("check", "GP-POS")} <= everything


def test_entered_and_unread_cells_have_no_trace(statements):
    assert statements.precedents("revenue", period="FY2025") == []
    assert statements.dependents("cash", period="FY2026") == []


def test_an_override_removes_the_formulas_precedents(statements):
    statements.set_value("gross_profit", {"period": "FY2025"}, 10, SRC)
    assert statements.precedents("gross_profit", period="FY2025") == []
    assert statements.dependents("revenue", period="FY2025") == []
    statements.set_value("gross_profit", {"period": "FY2025"}, lambda x: x["revenue"] * 0.5, SRC)
    assert names(statements.precedents("gross_profit", period="FY2025")) == ["revenue[period=FY2025]"]
    assert names(statements.dependents("revenue", period="FY2025")) == ["gross_profit[period=FY2025]"]


def test_dependents_are_the_reverse_of_precedents(statements):
    cells = [(name, {"period": p}) for name in statements.items for p in ("FY2025", "FY2026")]
    forward = {(n, c["period"], t.name, t.coords["period"]) for n, c in cells for t in statements.precedents(n, **c)}
    backward = {(t.name, t.coords["period"], n, c["period"]) for n, c in cells
                for t in statements.dependents(n, **c) if t.kind == "cell"}
    assert forward == backward and forward


def test_a_cell_chosen_by_a_value_then_moved_is_refused(m):
    for source in ["lambda x: (x['a'] if x['flag'] else x['b']).lag('period')",
                   "lambda x: [x['a']][0].lag('period')",
                   "lambda x: [x['a'].lag][0]('period')",
                   "lambda x: sorted([1], key=x['a'].lag)"]:
        with pytest.raises(tm.DefinitionError, match="chosen by a value|called directly"):
            m.add_item("t", ("region", "period"), formula=source)


def test_dependents_follow_new_items_checks_and_labels(statements):
    before = {t.name for t in statements.dependents("net_income", period="FY2025")}
    statements.add_item("payout", ("period",), formula=lambda x: x["net_income"] * 0.3)
    statements.add_check("NI-POS", coords={"period": tm.ANY}, expect=lambda x: x["net_income"] > 0)
    after = {t.name for t in statements.dependents("net_income", period="FY2025")}
    assert after - before == {"payout", "NI-POS"}
    statements.add_coords("period", ["FY2027"])
    assert {t.name for t in statements.dependents("revenue", period="FY2027")} == {"gross_profit"}
    assert "GP-POS" in {t.name for t in statements.dependents("gross_profit", period="FY2027")}


SUPERSET_FORMULAS = [
    "lambda x: x['a'] if x['flag'] else x['b']",
    "lambda x: x['a'] + (x['revenue'].lag('period') if x.coords['period'] != '2026-01' else 0)",
    "lambda x: max(x['a'], x['b']) * 2",
    "lambda x: sum(x['revenue'].sel(region=r) for r in x.labels('region') if x['flag'].sel(region=r))",
    "lambda x: x['a'] if x.coords['region'] == 'east' else x['b'].sel(region='east')",
    "lambda x, k='a': x[k] - 1",
    "lambda x: x['a'] or x['b']",
]


@pytest.mark.parametrize("source", SUPERSET_FORMULAS)
def test_static_reads_cover_the_run(m, source):
    with m.transaction():
        for r, p in itertools.product(m.dimensions["region"].coords, m.dimensions["period"].coords):
            for name, v in [("a", 0 if p == "2026-02" else 1), ("b", 2), ("flag", r == "east"), ("revenue", 3)]:
                m.set_value(name, {"region": r, "period": p}, float(v), SRC)
    m.add_item("t", ("region", "period"), formula=source)
    for r, p in itertools.product(m.dimensions["region"].coords, m.dimensions["period"].coords):
        m.get("t", region=r, period=p)
        ran = {(c.item.name, tuple(c.resolve(at).values())) for c, at in m._deps[("t", (r, p))]}
        static = {(t.name, tuple(t.coords.values())) for t in m.precedents("t", region=r, period=p)}
        assert ran <= static, (r, p)


@pytest.mark.parametrize("source, expected", [
    ("lambda x: sum(a * b for a, b in zip([x['a'], x['b']], [1, 2]))", ["a[east,2026-03]", "b[east,2026-03]"]),
    ("lambda x: sum(i * v for i, v in enumerate([x['a'], x['b']], 1))", ["a[east,2026-03]", "b[east,2026-03]"]),
    ("lambda x: list(reversed([x['a'], x['b']]))[0] + 0", ["b[east,2026-03]", "a[east,2026-03]"]),
    ("lambda x: sum(v for v, w in [(x['a'], 1), (x['b'], 2)])", ["a[east,2026-03]", "b[east,2026-03]"]),
])
def test_zip_enumerate_and_unpacking_over_cells(m, source, expected):
    assert sorted(reads(m, source, "revenue", **AT)) == sorted(expected)

"""Ops: every change recorded as plain data, replayed into another model through the same verb,
and undone by its inverse."""

import json
import random

import pytest

import tracemodel as tm
from tracemodel.ops import Op, apply, invert, replay

SRC = {"document": "d", "url": "u", "section": "s"}


def state(m):
    """A model as plain data: its definition, and every value with its source and meta."""
    from tracemodel.model import _value_data

    return json.loads(json.dumps({"definition": m.to_dict(),
                                  "values": [[e.item, e.coords, _value_data(e)] for e in m]}, default=str))


def test_each_verb_records_one_op():
    m = tm.Model()
    with m.recording() as ops:
        m.add_dimension("term", ["T1", "T2"])
        m.add_item("rate", ("term",))
        m.set_value("rate", {"term": "T1"}, 5.0, SRC, meta={"confidence": "assumed"})
        m.set_value("rate", {"term": "T1"}, 6.0, SRC)
        m.add_check("POS", coords={"term": tm.ANY}, expect=lambda x: x["rate"] > 0)
        m.add_coords("term", ["T3"])  # re-expands POS: a detail, not an op of its own
        m.set_formula("rate", lambda x: 1.0)
        m.remove_check("POS")
        m.remove_item("rate")  # clears its value first: one op per value, then the item
    assert [(o.kind, o.target) for o in ops] == [
        ("dimension.add", "dimension:term"), ("item.add", "item:rate"),
        ("value.set", "value:rate[term=T1]"), ("value.set", "value:rate[term=T1]"),
        ("check.set", "check:POS"), ("coords.add", "dimension:term"), ("item.update", "item:rate"),
        ("check.remove", "check:POS"), ("value.clear", "value:rate[term=T1]"), ("item.remove", "item:rate"),
    ]
    first, second = ops[2], ops[3]
    assert first.old is None and first.new["meta"] == {"confidence": "assumed"}
    assert second.old == first.new and second.new["value"] == 6.0


def test_a_refused_verb_records_nothing():
    m = tm.Model()
    m.add_dimension("term", ["T1"])
    m.add_item("a", ("term",))
    m.add_item("b", ("term",), formula=lambda x: x["a"])
    m.set_value("a", {"term": "T1"}, 1.0, SRC)
    with m.recording() as ops:
        with pytest.raises(tm.DefinitionError):
            m.remove_item("a")  # b reads it; its value.clear is taken back too
        with pytest.raises(tm.DefinitionError), m.transaction():
            m.set_value("a", {"term": "T1"}, 2.0, SRC)
            m.remove_item("a")
    assert ops == [] and m.get("a", term="T1") == 1


def test_ops_are_plain_data():
    m = tm.Model()
    with m.recording() as ops:
        m.add_dimension("month", tm.period_range("2026-01", periods=2, freq="M"))
        m.add_item("start", (), dtype="date")
        m.set_value("start", {}, "2026-01-16", SRC)
        m.add_item("fee", ("month",), formula=lambda x: 12_000 * x.period.overlap(x["start"], None))
        m.set_value("fee", {"month": "2026-02"}, lambda x: 11_000.0, SRC)
    again = [Op.from_data(json.loads(json.dumps(o.to_data()))) for o in ops]
    assert again == ops
    assert replay(tm.Model(), again).get("fee", month="2026-01") == pytest.approx(12_000 * 16 / 31)


def test_invert_undoes():
    m = tm.Model()
    m.add_dimension("term", ["T1", "T2"])
    m.add_item("rate", ("term",))
    before = state(m)
    with m.recording() as ops:
        m.set_value("rate", {"term": "T1"}, 5.0, SRC)
        m.add_coords("term", [("T3", "Third term")])
        m.add_check("POS", coords={"term": tm.ANY}, expect=lambda x: x["rate"] > 0)
        m.remove_coords("term", ["T2"])
    for op in reversed(ops):
        apply(m, invert(op))
    assert state(m) == before


def test_apply_checks_as_the_verb_does():
    m = tm.Model()
    m.add_dimension("term", ["T1"])
    with pytest.raises(tm.UnknownItem):
        apply(m, Op("value.set", {"item": "rate", "coords": {"term": "T1"}}, None, {"value": 1.0, "source": SRC}))
    with pytest.raises(ValueError, match="not an op"):
        Op.from_data({"kind": "value.burn", "key": "x"})


def test_replay_is_all_or_nothing():
    m = tm.Model()
    ops = [Op("dimension.add", "term", None, {"name": "term", "coords": ["T1"]}),
           Op("item.add", "rate", None, {"name": "rate", "dims": ["term"], "dtype": "float"}),
           Op("value.set", {"item": "rate", "coords": {"term": "T9"}}, None, {"value": 1.0, "source": SRC})]
    with pytest.raises(tm.UnknownCoord):
        replay(m, ops)
    assert m.dimensions == {} and m.items == {}


# == random sessions ============================================================
NAMES = ["a", "b", "c", "d"]


def _session(rng: random.Random, m: tm.Model, steps: int) -> None:
    """Random verbs; a refused one is fine (it records nothing)."""
    m.add_dimension("contract", ["C1", "C2"])
    m.add_dimension("month", tm.period_range("2026-01", periods=2, freq="M"))
    m.add_dimension("region", tm.Hierarchy(["/", "/north"]))
    added = 0
    for _ in range(steps):
        verb = rng.randrange(13)
        name = rng.choice(NAMES)
        dims = rng.choice([("contract",), ("contract", "month"), ("region",), ()])
        try:
            if verb == 0:
                other = rng.choice(NAMES)
                formula = rng.choice([None, f"lambda x: x['{other}'] * 2", "lambda x: 1.5"])
                m.add_item(name, dims, formula=formula, attrs=rng.choice([None, {"units": "USD"}]))
            elif verb in (1, 2, 3) and m.items:
                item = m.items[rng.choice(list(m.items))]
                coords = {d: rng.choice(m.dimensions[d].coords) for d in item.dims}
                value = rng.choice([rng.uniform(-5, 5), tm.NIL, "lambda x: 3.0"])
                meta = rng.choice([None, {"confidence": rng.choice(["assumed", "stated"]), "reasoning": "r"}])
                m.set_value(item.name, coords, value, SRC, meta=meta)
            elif verb == 4 and len(m):
                e = rng.choice(list(m))
                m.clear_value(e.item, e.coords)
            elif verb == 5:
                m.remove_item(name)
            elif verb == 6 and name in m.items:
                m.set_formula(name, rng.choice([None, "lambda x: 2.0", f"lambda x: x['{rng.choice(NAMES)}'] + 1"]))
            elif verb == 7 and name in m.items:
                m.update_item(name, description=rng.choice([None, "described"]))
            elif verb == 8 and m.items:
                item = m.items[rng.choice(list(m.items))]
                m.add_check(f"K{rng.randrange(3)}", coords={d: tm.ANY for d in item.dims} or None,
                            expect=f"lambda x: x['{item.name}'] >= -10")
            elif verb == 9 and m.checks:
                m.remove_check(rng.choice(list(m.checks)))
            elif verb == 10:
                added += 1
                dim = rng.choice(["contract", "month", "region"])
                if dim == "contract":
                    m.add_coords("contract", [(f"N{added}", f"New {added}")])
                elif dim == "month":
                    last = m.dimensions["month"].time.periods[-1].shift(1).label
                    m.add_coords("month", tm.period_range(last, periods=1, freq="M"))
                else:
                    m.add_coords("region", [f"/north/n{added}"])
            elif verb == 11:
                dim = m.dimensions[rng.choice(["contract", "month", "region"])]
                m.remove_coords(dim.name, [dim.coords[-1]])
            elif verb == 12:
                if "extra" in m.dimensions:
                    m.remove_dimension("extra")
                else:
                    m.add_dimension("extra", ["x", "y"])
        except tm.TraceModelError:
            pass


@pytest.mark.parametrize("seed", range(40))
def test_a_random_session_replays_and_undoes(seed):
    rng = random.Random(seed)
    m = tm.Model()
    with m.recording() as ops:
        _session(rng, m, 60)
    data = [json.loads(json.dumps(o.to_data(), default=str)) for o in ops]
    again = replay(tm.Model(), [Op.from_data(d) for d in data])
    assert state(again) == state(m)
    for op in reversed(ops):
        apply(m, invert(op))
    assert state(m) == state(tm.Model())

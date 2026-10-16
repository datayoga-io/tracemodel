"""The cells a formula can read, found from its source without reading any value.

The walk evaluates what doesn't depend on values (constants, closure values, the cell's
labels: x.coords, x.attrs(), x.period, x.children(), x.labels()) and records each cell whose
value is used. A value is unknown, so a branch on one (`a if x["flag"] else b`, a
comprehension's `if`) counts both ways. The item and labels a formula reads must not depend on
a value: x[x["which"]] is an error.

Most formulas read the same cells relative to any cell (x["revenue"], x.lag("period")): one
walk with no labels gives a template of cells, resolved per cell as an expression's are. A
formula that uses the labels is walked per cell.

The walk uses the formula's own guards, size bounds and a step budget, so a hostile formula
can't escape or hang through it.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .cellfn import Bound, _item_or_label
from .errors import DefinitionError
from .expr import Cell
from .formula import _ACTIVE, BINARY, BUILTINS, OP_TEXT, WALK_STEPS, Formula, _Budget, _getattr, _getitem, compare

if TYPE_CHECKING:
    from .model import Item, Model


class NeedsLabels(Exception):
    """The formula uses the cell's labels: walk it per cell."""


class _Unknown:
    def __repr__(self) -> str:
        return "<unknown>"


UNKNOWN = _Unknown()


@dataclass(frozen=True)
class _Ref:
    """x, or a cell reached from it (x["a"].lag("period")): not yet read."""

    cell: Cell | None
    root: bool = True  # x itself, which takes item names; a cell reached from it takes labels


@dataclass(frozen=True)
class _Method:
    ref: _Ref
    name: str


@dataclass
class Precedents:
    cells: list[Cell]  # the cells read, relative to the cell (resolve each with its labels)
    branched: bool  # a branch on a value: some of the cells may not be read at run time
    labels: frozenset[str] = frozenset()  # the dims whose label the walk used: it holds for any cell with the same


_REF_METHODS = {"sel", "lag", "lead", "shift", "is_nil", "children", "parent", "labels", "attrs"}
_LABEL_ATTRS = {"coords", "period", "periods"}
_MOVES = {"sel", "lag", "lead", "shift"}  # change which cell is read
# builtins that only arrange their arguments, so a list of cells passes through them unread
_SHAPERS = {
    "zip": lambda *a: [list(t) for t in zip(*a)],
    "enumerate": lambda a, start=0: [[i, v] for i, v in enumerate(a, start)],
    "list": list, "tuple": list, "reversed": lambda a: list(reversed(a)),
}
_PYTHON_ERRORS = (TypeError, ValueError, KeyError, IndexError, ArithmeticError)
_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: operator.not_}


def precedents(formula: Formula, model: Model, item: Item | None, at: Mapping[str, str] | None) -> Precedents:
    """The cells `formula` can read for the cell of `item` at `at` (a check run when item is
    None). With at=None, a template for any cell; NeedsLabels when it uses the labels."""
    return _Walk(formula, model, item, at).run()


class _Walk:
    def __init__(self, formula: Formula, model: Model, item: Item | None, at: Mapping[str, str] | None):
        self.formula, self.model, self.at = formula, model, at
        self.own = _Ref(Cell(model, item) if item is not None else None)
        self.reads: list[Cell] = []
        self.branched = False
        self.labels: set[str] = set()
        # the formula's builtins, run here on known values, spend the same budget as the walk
        self.budget = _Budget(WALK_STEPS, f"formula {formula.body!r} takes more than {WALK_STEPS:,} steps to check")

    def run(self) -> Precedents:
        _ACTIVE.append(self.budget)
        try:
            scope = {self.formula.param: self.own}
            args = self.formula.node.args
            for a, d in zip(reversed([*args.posonlyargs, *args.args]), reversed(args.defaults)):
                scope[a.arg] = self.walk(d, {})
            for a, d in zip(args.kwonlyargs, args.kw_defaults):
                if d is not None:
                    scope[a.arg] = self.walk(d, {})
            self.use(self.walk(self.formula.node.body, scope))
        except _PYTHON_ERRORS as e:  # a constant or a label the formula computes with is wrong: int('abc')
            raise DefinitionError(f"formula {self.formula.body!r}: {type(e).__name__}: {e}") from None
        except RecursionError:
            raise DefinitionError(f"formula {self.formula.body!r} is nested too deeply to check") from None
        finally:
            _ACTIVE.pop()
        return Precedents(self.reads, self.branched, frozenset(self.labels))

    def fail(self, node: ast.AST, why: str) -> DefinitionError:
        return DefinitionError(f"formula {self.formula.body!r}: {ast.unparse(node)}: {why}")

    def step(self) -> None:
        self.budget.charge(1)

    # == values ==
    def use(self, v: Any) -> Any:
        """`v`'s value is used: a cell is read; the value is unknown."""
        if isinstance(v, _Ref):
            if v.cell is None:
                raise DefinitionError(f"check {self.formula.body!r}: in a check, x is the run, not a cell; "
                                      "read an item, e.g. x['revenue']")
            self.reads.append(v.cell)
            return UNKNOWN
        if isinstance(v, _Method):
            raise DefinitionError(f"formula {self.formula.body!r}: a method of a cell must be called directly, "
                                  f"e.g. x['revenue'].{v.name}(...)")
        if isinstance(v, (list, tuple, set)):
            self.budget.charge(len(v))
            used = [self.use(e) for e in v]
            return UNKNOWN if any(e is UNKNOWN for e in used) else type(v)(used)
        if isinstance(v, dict):
            used = {k: self.use(e) for k, e in v.items()}
            return UNKNOWN if any(e is UNKNOWN for e in used.values()) else used
        return v

    def bound(self, ref: _Ref, dims: Any = None) -> Bound:
        """The real x, to read the cell's labels; `dims` the labels it reads (None: all)."""
        if self.at is None:
            raise NeedsLabels
        self.labels |= set(self.at) if dims is None else set(dims) & set(self.at)
        return Bound(self.model, ref.cell, self.at, [])

    def known(self, v: Any, node: ast.AST, what: str) -> Any:
        if isinstance(v, (_Ref, _Method)) or v is UNKNOWN or (isinstance(v, (list, tuple))
                                                                and any(e is UNKNOWN or isinstance(e, _Ref) for e in v)):
            raise self.fail(node, f"{what} depends on a value; the cells a formula reads must be fixed by "
                                  "the formula and the cell's labels")
        return v

    # == nodes ==
    def walk(self, node: ast.AST, scope: dict[str, Any]) -> Any:
        self.step()
        match node:
            case ast.Constant(value=v):
                return v
            case ast.Name(id=name):
                if name in scope:
                    return scope[name]
                if name in self.formula.env:
                    return self.formula.env[name]
                return BUILTINS[name]
            case ast.Subscript(value=ast.Attribute(value=inner, attr="coords"), slice=key) if \
                    isinstance(self.walk(inner, scope), _Ref):  # x.coords["period"]: only that label
                dim = self.known(self.use(self.walk(key, scope)), key, "the dim")
                return self.bound(self.walk(inner, scope), [dim]).coords[dim]
            case ast.Subscript(value=value, slice=key):
                target, k = self.walk(value, scope), self.walk(key, scope)
                if isinstance(target, _Ref):
                    k = self.known(k, key, "the item or label read")
                    return _Ref(_item_or_label(self.model, target.cell, k, target.root), root=False)
                target, k = self.use(target), self.use(k)
                if target is UNKNOWN and isinstance(k, str):
                    raise self.fail(node, "a cell chosen by a value can't take a label; pick the cell in each "
                                          "branch, e.g. x['a']['/x'] if ... else x['b']['/x']")
                if target is UNKNOWN or k is UNKNOWN:
                    return UNKNOWN
                return _getitem(target, k)
            case ast.Slice(lower=lo, upper=hi, step=st):
                parts = [self.use(self.walk(p, scope)) if p is not None else None for p in (lo, hi, st)]
                return UNKNOWN if UNKNOWN in parts else slice(*parts)
            case ast.Attribute(value=value, attr=attr):
                target = self.walk(value, scope)
                if isinstance(target, _Ref):
                    if attr in _REF_METHODS:
                        return _Method(target, attr)
                    if attr in _LABEL_ATTRS:
                        time_dims = [d for d in self.at or () if self.model.dimensions[d].time is not None]
                        return getattr(self.bound(target, None if attr == "coords" else time_dims), attr)
                    if attr == "dt":
                        return self.use(target)
                    raise self.fail(node, f"a formula can't use .{attr} on a cell")
                target = self.use(target)
                if target is UNKNOWN and attr in _MOVES:
                    raise self.fail(node, f"a cell chosen by a value can't be moved with .{attr}(); move it in "
                                          f"each branch, e.g. x['a'].{attr}(...) if ... else x['b'].{attr}(...)")
                return UNKNOWN if target is UNKNOWN else _getattr(target, attr)
            case ast.Call(func=func, args=args, keywords=keywords):
                return self.call(node, self.walk(func, scope), [self.walk(a, scope) for a in args],
                                 {k.arg: self.walk(k.value, scope) for k in keywords})
            case ast.BinOp(left=left, op=op, right=right):
                a, b = self.use(self.walk(left, scope)), self.use(self.walk(right, scope))
                return UNKNOWN if a is UNKNOWN or b is UNKNOWN else BINARY[type(op)](a, b)
            case ast.UnaryOp(op=op, operand=operand):
                a = self.use(self.walk(operand, scope))
                return UNKNOWN if a is UNKNOWN else _UNARY[type(op)](a)
            case ast.Compare(left=left, ops=ops, comparators=comparators):
                values = [self.use(self.walk(n, scope)) for n in [left, *comparators]]
                if UNKNOWN in values:
                    return UNKNOWN
                return all(compare(OP_TEXT[type(op)], a, b) for op, a, b in zip(ops, values, values[1:]))
            case ast.BoolOp(op=op, values=values):
                result: Any = None
                for n in values:
                    result = self.use(self.walk(n, scope))
                    if result is UNKNOWN:
                        self.branched = True
                        for rest in values[values.index(n) + 1:]:
                            self.use(self.walk(rest, scope))
                        return UNKNOWN
                    if bool(result) == isinstance(op, ast.Or):
                        return result  # short-circuits as Python would
                return result
            case ast.IfExp(test=test, body=body, orelse=orelse):
                t = self.use(self.walk(test, scope))
                if t is not UNKNOWN:
                    return self.walk(body if t else orelse, scope)
                self.branched = True
                self.use(self.walk(body, scope))
                self.use(self.walk(orelse, scope))
                return UNKNOWN
            case ast.List(elts=elts) | ast.Tuple(elts=elts) | ast.Set(elts=elts):
                return [self.walk(e, scope) for e in elts]
            case ast.Dict(keys=keys, values=values):
                return {self.use(self.walk(k, scope)): self.walk(v, scope) for k, v in zip(keys, values)}
            case ast.ListComp(elt=elt, generators=gens) | ast.SetComp(elt=elt, generators=gens) | \
                    ast.GeneratorExp(elt=elt, generators=gens):
                return [self.walk(elt, s) for s in self.comprehend(gens, scope)]
            case ast.DictComp(key=key, value=value, generators=gens):
                return {self.use(self.walk(key, s)): self.walk(value, s) for s in self.comprehend(gens, scope)}
        raise self.fail(node, f"{type(node).__name__} isn't allowed in a formula")

    def comprehend(self, gens: list[ast.comprehension], scope: dict[str, Any]):
        if not gens:
            yield scope
            return
        gen, rest = gens[0], gens[1:]
        items = self.walk(gen.iter, scope)
        if isinstance(items, _Ref):
            raise self.fail(gen.iter, "a formula can't loop over a cell")
        items = self.known(items, gen.iter, "the loop") if not isinstance(items, list) else items
        for v in items:
            self.step()
            inner = dict(scope)
            self.bind(gen.target, v, inner)
            keep = True
            for cond in gen.ifs:
                t = self.use(self.walk(cond, inner))
                if t is UNKNOWN:
                    self.branched = True  # kept: it may be read
                elif not t:
                    keep = False
                    break
            if keep:
                yield from self.comprehend(rest, inner)

    def bind(self, target: ast.AST, v: Any, scope: dict[str, Any]) -> None:
        if isinstance(target, ast.Name):
            scope[target.id] = v
            return
        parts = list(v) if isinstance(v, (list, tuple)) else list(self.known(v, target, "unpacking"))
        if not isinstance(target, ast.Tuple) or len(parts) != len(target.elts):
            raise self.fail(target, "can't unpack")
        for t, p in zip(target.elts, parts):
            self.bind(t, p, scope)

    def call(self, node: ast.Call, fn: Any, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if isinstance(fn, _Method):
            name, ref = fn.name, fn.ref
            if name == "is_nil":
                return self.use(ref)
            if name == "labels":
                return Bound(self.model, ref.cell, self.at or {}, []).labels(
                    *[self.known(a, node, "the dim") for a in args])
            if name in ("children", "parent", "attrs"):
                fixed = [self.known(a, node, "the dim") for a in args]
                return getattr(self.bound(ref, fixed[:1]), name)(*fixed)
            if ref.cell is None:
                raise self.fail(node, f"in a check, x is the run, not a cell: use x['item'].{name}(...)")
            fixed_args = [self.known(a, node, f".{name}()") for a in args]
            fixed_kwargs = {k: self.known(v, node, f".{name}()") for k, v in kwargs.items()}
            return _Ref(getattr(ref.cell, name)(*fixed_args, **fixed_kwargs), root=False)
        if fn is UNKNOWN:
            for v in [*args, *kwargs.values()]:
                self.use(v)
            return UNKNOWN
        name = next((k for k, v in BUILTINS.items() if v is fn), "")
        sequences = args[:1] if name == "enumerate" else args  # enumerate(cells, start)
        if name in _SHAPERS and sequences and all(isinstance(a, (list, tuple)) for a in sequences) and \
                set(kwargs) <= {"start"}:  # zip / enumerate / list ... over cells: arrange them, read none
            self.budget.charge(sum(len(a) for a in sequences))
            rest = [self.known(a, node, "start") for a in args[len(sequences):]]
            return _SHAPERS[name](*sequences, *rest, **{k: self.known(v, node, "start") for k, v in kwargs.items()})
        used = [self.use(a) for a in args]
        used_kw = {k: self.use(v) for k, v in kwargs.items()}
        if UNKNOWN in used or UNKNOWN in used_kw.values():
            return UNKNOWN
        return fn(*used, **used_kw)


__all__ = ["UNKNOWN", "NeedsLabels", "Precedents", "precedents"]

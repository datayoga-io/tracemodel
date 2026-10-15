"""Formulas and checks: a lambda of the cell, `lambda x: max(0, 25_000 - x["cash"])`.

A formula is its source text. A function is turned into its lambda's source, so the code that
runs in the session is the code that runs after `tm.load`; the function itself is never called.
The source is checked against an allow-list of Python expressions, then compiled with
RestrictedPython, which routes every attribute, `[]` and loop through the guards below:

- attributes: only the names listed for an object's exact type (`_ATTRS`), never one with `_`;
- names: the lambda's parameter, comprehension variables, the builtins in `BUILTINS`, and the
  closure values the formula was written with, saved as data (numbers, strings, lists and
  dicts of them, dates);
- size: `*`, `**`, `range` and loops are bounded as they run, so `9**9**9` or `"a" * 10**10`
  raise instead of hanging.

Comparisons keep the float slack a check has always had (abs(391_035.2 - 391_035) <= 0.2
holds), and a date never compares with a number.
"""

from __future__ import annotations

import ast
import copy
import functools
import inspect
import keyword
import math
import types
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from RestrictedPython import RestrictingNodeTransformer, compile_restricted
from RestrictedPython.Guards import guarded_iter_unpack_sequence, guarded_unpack_sequence

from . import dates
from .errors import DefinitionError
from .periods import Period

MAX_ITEMS = 100_000  # longest string, list or range a formula may build
MAX_BITS = 10_000  # largest whole number, about 3,000 digits
MAX_STEPS = 1_000_000  # loop iterations in one call
MAX_SOURCE = 20_000  # characters
MAX_DEPTH = 100  # nesting of the expression tree
WALK_STEPS = 200_000  # steps to find a formula's reads when it is added


# == comparisons and arithmetic, as they run ==================================

class CellValue:
    """The base of `x` (cellfn.Bound): a cell whose value is read on demand, with _get()."""

    __slots__ = ()

    def _get(self) -> Any:
        raise NotImplementedError


def _value(v: Any) -> Any:
    """A cell (x["a"]) as its value, read now; anything else as it is."""
    return v._get() if isinstance(v, CellValue) else v


def _slack(lhs: float, rhs: float) -> float:
    # Float slack, so bounds hold as written: abs(391_035.2 - 391_035) is 0.2000000000116.
    return 1e-9 * max(1.0, abs(lhs), abs(rhs))


_SLACK = {
    "<": lambda a, b, e: a < b - e,
    "<=": lambda a, b, e: a <= b + e,
    ">": lambda a, b, e: a > b + e,
    ">=": lambda a, b, e: a >= b - e,
    "==": lambda a, b, e: abs(a - b) <= e,
    "!=": lambda a, b, e: abs(a - b) > e,
}
_EXACT = {
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b, "in": lambda a, b: a in b,
    "not in": lambda a, b: a not in b, "is": lambda a, b: a is b, "is not": lambda a, b: a is not b,
}
OP_TEXT = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=",
            ast.In: "in", ast.NotIn: "not in", ast.Is: "is", ast.IsNot: "is not"}
_KINDS = {"number", "date", "datetime", "timedelta"}
_NUMBERS = (int, float)


def _kind(v: Any) -> str:
    return "number" if isinstance(v, bool) else dates.kind(v)


def compare(op: str, lhs: Any, rhs: Any) -> bool:
    """lhs <op> rhs as a check means it: numbers with float slack, dates exactly, and an
    error for a date against a number (Python would call them never equal)."""
    lhs, rhs = _value(lhs), _value(rhs)
    if type(lhs) in _NUMBERS and type(rhs) in _NUMBERS and op in _SLACK:
        return _SLACK[op](lhs, rhs, 1e-9 * max(1.0, abs(lhs), abs(rhs)))
    if op in ("in", "not in", "is", "is not"):
        _charge(rhs)
        return _EXACT[op](lhs, rhs)
    a, b = _kind(lhs), _kind(rhs)
    if a == b == "number":
        return _SLACK[op](lhs, rhs, _slack(lhs, rhs))
    if isinstance(lhs, date) and isinstance(rhs, str):  # a date compares with an ISO string, as in pandas
        rhs = dates.coerce(datetime if isinstance(lhs, datetime) else date, rhs, "comparison")
    elif isinstance(rhs, date) and isinstance(lhs, str):
        lhs = dates.coerce(datetime if isinstance(rhs, datetime) else date, lhs, "comparison")
    elif a != b and a in _KINDS and b in _KINDS:
        raise DefinitionError(f"can't compare a {a} with a {b} ({_fmt(lhs)} {op} {_fmt(rhs)})")
    return _EXACT[op](lhs, rhs)


def _fmt(value: Any) -> str:
    from .table import _fmt

    return _fmt(value)


def _sized(v: Any) -> int | None:
    return len(v) if isinstance(v, (str, list, tuple)) else None


# == the work budget: every loop, and every builtin that walks a sequence, spends it ==

class _Budget:
    """Steps of work left in the current call: loop iterations and items walked by builtins."""

    __slots__ = ("left", "limit", "message")

    def __init__(self, limit: int = MAX_STEPS, message: str = "") -> None:
        self.left = self.limit = limit
        self.message = message or f"a formula may do at most {limit:,} steps of work (loops, sums, sorts)"

    def charge(self, n: int) -> None:
        self.left -= n
        if self.left < 0:
            raise DefinitionError(self.message)

    def iterate(self, obj: Any):
        if type(obj) not in _ITER_TYPES:
            raise DefinitionError(f"a formula can't loop over {_what(obj)}")
        for v in obj:
            self.charge(1)
            yield v


_ACTIVE: list[_Budget] = [_Budget()]  # the innermost formula call's budget is last
_SIZED = (str, list, tuple, dict, set, frozenset, range)


def _charge(v: Any) -> None:
    """Spend the work of walking `v`, a sequence a builtin or `in` is about to go through."""
    if isinstance(v, _SIZED):
        _ACTIVE[-1].charge(len(v))


def _counted(v: Any) -> Any:
    """`v` as an argument to a builtin: sized values are charged now, iterators as they are walked."""
    if isinstance(v, _SIZED):
        _ACTIVE[-1].charge(len(v))
        return v
    if type(v) in _ITER_TYPES:
        return _ACTIVE[-1].iterate(v)
    return v


def _walking(fn: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(*args: Any, **kwargs: Any) -> Any:
        return fn(*(_counted(a) for a in args), **kwargs)
    guarded.__name__ = guarded.__qualname__ = fn.__name__
    return guarded


def _arith(symbol: str, fn: Callable[[Any, Any], Any]) -> Callable[[Any, Any], Any]:
    def op(a: Any, b: Any) -> Any:
        a, b = _value(a), _value(b)
        if type(a) in _NUMBERS and type(b) in _NUMBERS:
            return fn(a, b)
        _charge(a)
        _charge(b)
        try:
            return fn(a, b)
        except TypeError:  # a date and a number, two dates added, ...
            hint = (" (a number of days is a timedelta: days * timedelta(days=1))"
                    if {dates.kind(a), dates.kind(b)} & {"date", "datetime"} else "")
            raise DefinitionError(f"can't compute {dates.kind(a)} {symbol} {dates.kind(b)} "
                                  f"({_fmt(a)} {symbol} {_fmt(b)}){hint}") from None
    return op


add = _arith("+", lambda a, b: a + b)
sub = _arith("-", lambda a, b: a - b)
div = _arith("/", lambda a, b: a / b)
floordiv = _arith("//", lambda a, b: a // b)
for _fn, _name in ((add, "add"), (sub, "sub"), (div, "div"), (floordiv, "floordiv")):
    _fn.__name__ = _fn.__qualname__ = _name


def mul(a: Any, b: Any) -> Any:
    a, b = _value(a), _value(b)
    if type(a) is float and type(b) in _NUMBERS or type(b) is float and type(a) in _NUMBERS:
        return a * b
    for seq, n in ((a, b), (b, a)):
        size = _sized(seq)
        if size is not None and isinstance(n, int) and size * n > MAX_ITEMS:
            raise DefinitionError(f"a formula may build at most {MAX_ITEMS:,} items; this makes {size * n:,}")
    if isinstance(a, int) and isinstance(b, int) and a.bit_length() + b.bit_length() > MAX_BITS:
        raise DefinitionError("the number is too large")
    result = a * b
    _charge(result)
    return result


def power(a: Any, b: Any) -> Any:
    a, b = _value(a), _value(b)
    if isinstance(a, int) and isinstance(b, int) and b > 0 and abs(a) > 1 and a.bit_length() * b > MAX_BITS:
        raise DefinitionError(f"{a} ** {b} is too large")
    return a ** b


def mod(a: Any, b: Any) -> Any:
    a, b = _value(a), _value(b)
    if isinstance(a, str):
        raise DefinitionError("% formatting of text isn't allowed in a formula")
    return a % b


# what each operator runs as: the compiled formula calls these, and so does the static walk
BINARY: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: add, ast.Sub: sub, ast.Div: div, ast.FloorDiv: floordiv, ast.Mult: mul, ast.Pow: power, ast.Mod: mod,
}


def _range(*args: int) -> range:
    r = range(*args)
    if len(r) > MAX_ITEMS:
        raise DefinitionError(f"range(...) in a formula may have at most {MAX_ITEMS:,} numbers")
    return r


# == what a formula may use ===================================================

_MATH = frozenset({"ceil", "copysign", "e", "exp", "fabs", "floor", "inf", "isclose", "isfinite",
                   "isinf", "isnan", "log", "log10", "log1p", "log2", "nan", "pi", "sqrt", "trunc"})

BUILTINS: dict[str, Any] = {
    "abs": abs, "bool": bool, "float": float, "int": int, "len": len, "range": _range, "round": round,
    **{fn.__name__: _walking(fn) for fn in (all, any, dict, enumerate, list, max, min, reversed, sorted, sum,
                                            tuple, zip)},
    "date": date, "datetime": datetime, "timedelta": timedelta, "math": math,
    "True": True, "False": False, "None": None,
}

# Attributes a formula may read, by the object's exact type (subclasses get none). Classes
# that formulas reach add theirs with `allow_attrs`.
_ATTRS: dict[type, frozenset[str]] = {
    str: frozenset({"endswith", "isdigit", "lower", "split", "startswith", "strip", "upper"}),
    list: frozenset({"count", "index"}),
    tuple: frozenset({"count", "index"}),
    dict: frozenset({"get", "items", "keys", "values"}),
    date: frozenset({"day", "isoformat", "isoweekday", "month", "replace", "toordinal", "weekday", "year"}),
    datetime: frozenset({"date", "day", "hour", "isoformat", "isoweekday", "microsecond", "minute", "month",
                         "replace", "second", "toordinal", "weekday", "year"}),
    timedelta: frozenset({"days", "microseconds", "seconds", "total_seconds"}),
    Period: frozenset({"asfreq", "days", "end_time", "freq", "label", "month", "overlap", "quarter", "shift",
                       "start_time", "year"}),
    dates.DatetimeAccessor: frozenset(n for n in dir(dates.DatetimeAccessor) if not n.startswith("_")),
}
_CLASS_ATTRS: dict[int, tuple[Any, frozenset[str]]] = {
    id(math): (math, _MATH),
    id(date): (date, frozenset({"fromisoformat"})),
    id(datetime): (datetime, frozenset({"fromisoformat"})),
}


def allow_attrs(cls: type, names: set[str] | frozenset[str]) -> None:
    """Let formulas read `names` on objects of exactly `cls`."""
    _ATTRS[cls] = frozenset(names)


def _getattr(obj: Any, name: str) -> Any:
    special = _CLASS_ATTRS.get(id(obj))
    allowed = special[1] if special is not None and special[0] is obj else _ATTRS.get(type(obj))
    if name.startswith("_") or allowed is None or name not in allowed:
        raise DefinitionError(f"a formula can't use .{name} on {_what(obj)}")
    if type(obj) in (str, list, tuple):  # its methods walk it: lower(), split(), index(), ...
        _ACTIVE[-1].charge(len(obj))
    return getattr(obj, name)


def _what(obj: Any) -> str:
    if obj is math:
        return "math"
    return f"a {type(obj).__name__}" if not isinstance(obj, type) else obj.__name__


_ITEM_TYPES = (dict, list, tuple, str)


def _getitem(obj: Any, key: Any) -> Any:
    if isinstance(obj, CellValue) or type(obj) in _ITEM_TYPES:
        return obj[key]
    raise DefinitionError(f"a formula can't index {_what(obj)}")


_ITER_TYPES = frozenset({list, tuple, dict, str, range, set, frozenset, types.GeneratorType, enumerate, zip,
                         reversed, type({}.keys()), type({}.values()), type({}.items()), type(reversed([])),
                         type(iter(range(0)))})


def _iterate(obj: Any):
    return _ACTIVE[-1].iterate(obj)


# == checking the source ======================================================

_ALLOWED = (
    ast.Expression, ast.Lambda, ast.arguments, ast.arg, ast.BoolOp, ast.And, ast.Or, ast.BinOp, ast.Add,
    ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.UnaryOp, ast.USub, ast.UAdd, ast.Not,
    ast.IfExp, ast.Compare, *OP_TEXT, ast.Call, ast.keyword, ast.Constant, ast.Attribute, ast.Subscript,
    ast.Slice, ast.Name, ast.Load, ast.Store, ast.List, ast.Tuple, ast.Dict, ast.Set, ast.ListComp,
    ast.SetComp, ast.GeneratorExp, ast.DictComp, ast.comprehension,
)
_WHY = {
    ast.BitAnd: "& isn't allowed; use `and` (or all([...]))", ast.BitOr: "| isn't allowed; use `or` (or any([...]))",
    ast.Invert: "~ isn't allowed; use `not`", ast.Lambda: "a formula can't define another lambda",
    ast.NamedExpr: ":= isn't allowed", ast.Starred: "*args aren't allowed", ast.JoinedStr: "f-strings aren't allowed",
    ast.Await: "await isn't allowed", ast.Yield: "yield isn't allowed", ast.MatMult: "@ isn't allowed",
    ast.LShift: "<< isn't allowed", ast.RShift: ">> isn't allowed", ast.BitXor: "^ isn't allowed",
}


class _Unparser(ast._Unparser):  # type: ignore[name-defined, misc]
    """ast.unparse, writing sum(c for c in cs) rather than sum((c for c in cs))."""

    def visit_Call(self, node: ast.Call) -> None:
        if len(node.args) == 1 and not node.keywords and isinstance(node.args[0], ast.GeneratorExp):
            self.set_precedence(ast._Precedence.ATOM, node.func)  # type: ignore[attr-defined]
            self.traverse(node.func)
            with self.delimit("(", ")"):
                self.traverse(node.args[0].elt)
                for gen in node.args[0].generators:
                    self.traverse(gen)
            return
        super().visit_Call(node)


def unparse(node: ast.AST) -> str:
    """The canonical one-line text of a formula, as saved and shown."""
    try:
        return _Unparser().visit(node)
    except Exception:  # noqa: BLE001 - the private unparser changed: fall back to the public one
        return ast.unparse(node)


def _parse(source: str, what: str) -> ast.Lambda:
    if not isinstance(source, str):
        raise DefinitionError(f"{what} must be a lambda or its source text, got {source!r}")
    text = source.strip()
    if len(text) > MAX_SOURCE:
        raise DefinitionError(f"{what} is longer than {MAX_SOURCE:,} characters")
    try:
        try:
            tree = ast.parse(text, mode="eval")
        except SyntaxError:  # a lambda written over several lines inside a call's parentheses
            tree = ast.parse(f"({text}\n)", mode="eval")
    except (MemoryError, RecursionError):
        raise DefinitionError(f"{what} is nested too deeply to read; split it into items") from None
    except SyntaxError as e:
        hint = "; a formula is one lambda, e.g. 'lambda x: x[\"revenue\"] * 0.6'" if text.startswith("def ") else ""
        raise DefinitionError(f"{what} {text!r}: {e.msg}{hint}") from None
    depth, stack = 0, [(tree, 0)]
    while stack:  # without recursion: a deep tree is what this guards against
        node, d = stack.pop()
        depth = max(depth, d)
        if depth > MAX_DEPTH:
            raise DefinitionError(f"{what} is nested more than {MAX_DEPTH} levels deep; split it into items")
        stack.extend((c, d + 1) for c in ast.iter_child_nodes(node))
    if not isinstance(tree.body, ast.Lambda):
        raise DefinitionError(f"{what} {text!r} must be a lambda, e.g. 'lambda x: x[\"revenue\"] * 0.6'")
    return tree.body


def _check(node: ast.Lambda, text: str, env: Mapping[str, Any], what: str) -> str:
    """Check the lambda against the allow-list; returns its parameter (the cell, usually x)."""

    def fail(n: ast.AST, why: str) -> DefinitionError:
        return DefinitionError(f"{what} {text!r}: {why} ({ast.unparse(n)!r})")

    args = node.args
    if args.vararg or args.kwarg:
        raise fail(node.args, "a formula takes one argument, the cell (x), not *args or **kwargs")
    positional = [*args.posonlyargs, *args.args]
    required = positional[:len(positional) - len(args.defaults)]
    if len(required) != 1:
        raise fail(node.args, "a formula takes exactly one argument without a default: the cell, e.g. lambda x: ...")
    params = {a.arg for a in [*positional, *args.kwonlyargs]}
    bound = set(params)
    for n in ast.walk(node.body):
        if isinstance(n, ast.comprehension):
            bound |= {t.id for t in ast.walk(n.target) if isinstance(t, ast.Name)}

    for n in ast.walk(node):
        if n is not node and isinstance(n, ast.Lambda):
            raise fail(n, _WHY[ast.Lambda])
        if not isinstance(n, _ALLOWED):
            raise fail(n, _WHY.get(type(n), f"{type(n).__name__} isn't allowed in a formula"))
        match n:
            case ast.Name(id=name) if name.startswith("_"):
                raise fail(n, f"names starting with _ aren't allowed ({name})")
            case ast.Name(id=name, ctx=ast.Load()) if name not in bound and name not in env and name not in BUILTINS:
                raise fail(n, f"{name!r} isn't defined; a formula reads cells through x, e.g. x[{name!r}]")
            case ast.Attribute(attr=attr) if attr.startswith("_"):
                raise fail(n, f"attributes starting with _ aren't allowed (.{attr})")
            case ast.Constant(value=v) if not (v is None or isinstance(v, (bool, int, float, str))):
                raise fail(n, f"a {type(v).__name__} constant isn't allowed")
            case ast.Constant(value=int() as v) if v.bit_length() > MAX_BITS:
                raise fail(n, "the number is too large")
            case ast.Constant(value=str() as v) if len(v) > MAX_ITEMS:
                raise fail(n, "the text is too long")
            case ast.keyword(arg=None):
                raise fail(n, "**kwargs aren't allowed")
            case ast.comprehension(is_async=1):
                raise fail(n, "async isn't allowed")
            case ast.BinOp(op=ast.Mod(), left=ast.Constant(value=str())):
                raise fail(n, "% formatting of text isn't allowed")
            case ast.Call(func=ast.Name(id=name)) if name not in BUILTINS or name in env:
                raise fail(n, f"{name}(...) isn't a function a formula may call; it may call "
                              f"{', '.join(k for k, v in BUILTINS.items() if callable(v) and k != 'math')}")
    for d in [*args.defaults, *(d for d in args.kw_defaults if d is not None)]:
        if not isinstance(d, (ast.Constant, ast.Name)):
            raise fail(d, "a default value must be a constant or a name (k=k)")
    return required[0].arg


class _Policy(RestrictingNodeTransformer):
    """RestrictedPython's checks and guards, plus comparisons and * ** % through our helpers."""

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        node = self.node_contents_visit(node)
        operands = [node.left, *node.comparators]
        calls = [_call("_compare_", ast.Constant(OP_TEXT[type(op)]), a, b, like=node)
                 for op, a, b in zip(node.ops, operands, operands[1:])]
        if len(calls) == 1:
            return calls[0]
        chain = ast.copy_location(ast.BoolOp(op=ast.And(), values=calls), node)  # a < b < c: a < b and b < c
        return ast.fix_missing_locations(chain)

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        node = self.node_contents_visit(node)
        helper = BINARY.get(type(node.op))
        return _call(f"_{helper.__name__}_", node.left, node.right, like=node) if helper else node


def _call(name: str, *args: ast.expr, like: ast.AST) -> ast.Call:
    call = ast.copy_location(ast.Call(func=ast.Name(name, ast.Load()), args=list(args), keywords=[]), like)
    return ast.fix_missing_locations(call)


def _compile(node: ast.Lambda, namespace: dict[str, Any], what: str, text: str) -> Callable[..., Any]:
    tree = ast.fix_missing_locations(ast.Expression(body=copy.deepcopy(node)))  # the policy rewrites it
    try:
        code = compile_restricted(tree, filename="<tracemodel formula>", mode="eval", policy=_Policy)
    except SyntaxError as e:
        raise DefinitionError(f"{what} {text!r}: {e}") from None
    return eval(code, namespace)  # checked above, run with guarded builtins only


# == closure values, saved as data =============================================

def _env_ok(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float, str, date, timedelta)):
        return type(value) in (type(None), bool, int, float, str, date, datetime, timedelta)
    if isinstance(value, (list, tuple)):
        return type(value) in (list, tuple) and all(_env_ok(v) for v in value)
    if isinstance(value, dict):
        return type(value) is dict and all(isinstance(k, str) and _env_ok(v) for k, v in value.items())
    return False


def _plain(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _check_env(env: Mapping[str, Any], what: str, text: str) -> dict[str, Any]:
    out = {}
    for name, value in env.items():
        if (not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name)
                or name.startswith("_") or name in BUILTINS):
            raise DefinitionError(f"{what} {text!r}: {name!r} can't be a name a formula uses")
        if not _env_ok(value):
            hint = ("; read cells through x instead, e.g. x['revenue']"
                    if type(value).__module__.startswith("tracemodel") else "")
            shown = repr(value) if len(repr(value)) <= 60 else f"a {type(value).__name__}"
            raise DefinitionError(
                f"{what} {text!r} uses {name!r} = {shown}; a formula may use numbers, strings, "
                f"lists and dicts of them, and dates{hint}")
        out[name] = _plain(value)  # a copy: changing the original later doesn't change the formula
    return out


def env_to_data(env: Mapping[str, Any]) -> dict[str, Any]:
    def one(v: Any) -> Any:
        if isinstance(v, list):
            return [one(x) for x in v]
        if isinstance(v, dict):
            return {k: one(x) for k, x in v.items()}
        saved = dates.env_value(v)
        return saved if saved is not None else v

    return {k: one(v) for k, v in env.items()}


def env_from_data(data: Mapping[str, Any], what: str) -> dict[str, Any]:
    """Saved closure values back as values. Old files may name the model, an expression or a
    module: the model and expressions are refused (x reads cells), math is built in."""

    def one(name: str, v: Any) -> Any:
        if isinstance(v, list):
            return [one(name, x) for x in v]
        if isinstance(v, dict) and len(v) == 1:
            (key, inner), = v.items()
            if key in dates.ENV_KEYS:
                return dates.env_restore(key, inner)
            if key in ("$model", "$expr", "$module"):
                raise DefinitionError(
                    f"{what}: the saved formula uses {name!r}, "
                    f"{'the model' if key == '$model' else 'a module' if key == '$module' else 'an expression'}; "
                    "a formula reads cells through x, e.g. x['revenue']. Rewrite it as a lambda of x")
        if isinstance(v, dict):
            return {k: one(name, x) for k, x in v.items()}
        return v

    out = {}
    for name, v in data.items():
        if isinstance(v, dict) and (set(v) == {"$class"} or v == {"$module": "math"}):
            continue  # date, datetime, timedelta and math are built in
        out[name] = one(name, v)
    return out


# == Formula ===================================================================

class FormulaBase:
    """What a model keeps as a formula or check: a compiled Formula, or one kept as text
    (Uncompiled). Only a runnable one is run; both are saved with to_data()."""

    runnable = False
    source = ""
    op: str | None = None
    sides: tuple[Formula, Formula] | None = None  # a single comparison's two sides
    parts: list[Formula] | None = None  # the parts joined by and / or

    @property
    def rule(self) -> str:
        """Short label for reports: the comparison, or and / or."""
        return self.op or ""

    def to_data(self) -> dict[str, Any]:
        raise NotImplementedError


class Uncompiled(FormulaBase):
    """A formula loaded with formulas="off": kept as its saved text, never compiled or run."""

    def __init__(self, data: Mapping[str, Any]):
        self.data = dict(data)
        self.source = str(data.get("formula") or data.get("python") or data.get("expect") or "")

    def to_data(self) -> dict[str, Any]:
        return dict(self.data)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Uncompiled) and other.data == self.data

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"{self.source} (not loaded: formulas='off')"


class Formula(FormulaBase):
    """A checked, compiled lambda of the cell. `repr(f)` is its body, `f.source` the lambda, both
    on one line as Python writes them (x['revenue'], single quotes)."""

    runnable = True

    def __init__(self, source: str, env: Mapping[str, Any] | None = None, what: str = "formula"):
        node = _parse(source, what)
        self.source = unparse(node)  # one line, as saved: lambda x: x['revenue'] * 0.6
        self.env = _check_env(dict(env or {}), what, self.source)
        self.param = _check(node, self.source, self.env, what)
        self.node = node
        self.body = unparse(node.body)
        self._budget = _Budget()
        self._namespace = {
            "__builtins__": BUILTINS, "_getattr_": _getattr, "_getitem_": _getitem,
            "_getiter_": _iterate, "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
            "_unpack_sequence_": guarded_unpack_sequence, "_compare_": compare,
            **{f"_{fn.__name__}_": fn for fn in BINARY.values()}, **self.env,
        }
        self._fn = _compile(node, self._namespace, what, self.source)
        self.call = self.__call__
        self._what = what
        self.op, self._side_nodes, self._part_nodes = None, None, None
        body = node.body
        if isinstance(body, ast.Compare) and len(body.ops) == 1 and type(body.ops[0]) in OP_TEXT:
            self.op = OP_TEXT[type(body.ops[0])]
            if self.op in _SLACK:
                self._side_nodes = (body.left, body.comparators[0])
        elif isinstance(body, ast.BoolOp):
            self.op = "and" if isinstance(body.op, ast.And) else "or"
            self._part_nodes = body.values
        elif (isinstance(body, ast.Call) and isinstance(body.func, ast.Name) and body.func.id in ("all", "any")
              and len(body.args) == 1 and not body.keywords and isinstance(body.args[0], (ast.List, ast.Tuple))):
            self.op = "and" if body.func.id == "all" else "or"
            self._part_nodes = body.args[0].elts

    @functools.cached_property
    def sides(self) -> tuple[Formula, Formula] | None:  # type: ignore[override]
        return tuple(self._part(n) for n in self._side_nodes) if self._side_nodes else None  # type: ignore[return-value]

    @functools.cached_property
    def parts(self) -> list[Formula] | None:  # type: ignore[override]
        return [self._part(n) for n in self._part_nodes] if self._part_nodes else None

    def _part(self, node: ast.expr) -> Formula:
        lam = ast.Lambda(args=self.node.args, body=node)
        return Formula(unparse(lam), self.env, self._what)

    def __call__(self, x: Any) -> Any:
        # each call has a full budget; a cell it reads calculates with its own formula's
        budget = self._budget
        saved, budget.left = budget.left, MAX_STEPS
        _ACTIVE.append(budget)
        try:
            return self._fn(x)
        finally:
            _ACTIVE.pop()
            budget.left = saved

    @classmethod
    def of(cls, value: Any, what: str = "formula") -> Formula:
        """A Formula from a Formula, a lambda, or a lambda's source text."""
        if isinstance(value, Formula):
            return value
        if isinstance(value, str):
            return cls(value, None, what)
        if callable(value):
            return cls.from_function(value, what)
        raise DefinitionError(f"{what} must be a lambda, e.g. lambda x: x['revenue'] * 0.6; got {value!r}")

    @classmethod
    def from_function(cls, fn: Callable[..., Any], what: str = "formula") -> Formula:
        from .cellfn import _function_source

        if getattr(fn, "__name__", None) != "<lambda>" and inspect.isfunction(fn):
            raise DefinitionError(
                f"{what} {fn.__name__}(): a formula is one lambda, e.g. lambda x: max(0, x['a'] - x['b']); "
                "a def can't be saved")
        source = _function_source(fn)
        if source is None:
            raise DefinitionError(
                f"{what} {fn!r}: its source isn't available (was it typed at a plain python prompt or "
                "built with exec?); pass the source text instead, e.g. 'lambda x: x[\"a\"] * 2'")
        node = _parse(source, what)
        return cls(source, _closure_values(fn, node, source, what), what)

    def to_data(self) -> dict[str, Any]:
        """{"formula": source} plus {"env": {...}} when it uses closure values."""
        return {"formula": self.source} | ({"env": env_to_data(self.env)} if self.env else {})

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Formula) and (self.source, self.env) == (other.source, other.env)

    def __hash__(self) -> int:
        return hash(self.source)

    def __repr__(self) -> str:
        return self.body

    def _repr_pretty_(self, p, cycle) -> None:
        p.text(self.source)


def _closure_values(fn: Callable[..., Any], node: ast.Lambda, source: str, what: str) -> dict[str, Any]:
    """The values of the names a lambda uses from where it was written (closures, globals, k=k)."""
    args = node.args
    params = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
    local = set(params)
    for n in ast.walk(node.body):
        if isinstance(n, ast.comprehension):
            local |= {t.id for t in ast.walk(n.target) if isinstance(t, ast.Name)}
    closure = dict(zip(fn.__code__.co_freevars, (c.cell_contents for c in fn.__closure__ or ())))
    env: dict[str, Any] = {}

    def lookup(name: str) -> Any:
        if name in closure:
            return closure[name]
        if name in fn.__globals__:
            return fn.__globals__[name]
        raise DefinitionError(f"{what} {source!r}: {name!r} isn't defined")

    for n in ast.walk(node.body):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in local:
            if n.id in BUILTINS:
                if (n.id in closure or n.id in fn.__globals__) and lookup(n.id) is not BUILTINS[n.id]:
                    raise DefinitionError(f"{what} {source!r}: {n.id!r} is a built-in name in formulas; rename it")
                continue
            env[n.id] = lookup(n.id)
    # k=k: the default is evaluated where the lambda is written, so save k's value
    positional = [*args.posonlyargs, *args.args]
    defaults = list(zip(positional[len(positional) - len(args.defaults):], args.defaults,
                        getattr(fn, "__defaults__", None) or ()))
    kwdefaults = getattr(fn, "__kwdefaults__", None) or {}
    defaults += [(a, d, kwdefaults[a.arg]) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None]
    for _, d, value in defaults:
        if isinstance(d, ast.Name) and d.id not in BUILTINS:
            env[d.id] = value
    return env


__all__ = ["BINARY", "BUILTINS", "OP_TEXT", "CellValue", "Formula", "FormulaBase", "Uncompiled", "allow_attrs",
           "compare"]

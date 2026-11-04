"""Dimensions: a named, ordered set of coord labels, flat or a path hierarchy."""

from __future__ import annotations

import warnings
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any, Self

from .errors import DefinitionError, UnknownCoord
from .periods import Period, PeriodRange

ROOT = "/"

# A coord entry: "code", ("code", "description") or ("code", "description", {attrs}).
Entry = str | tuple[str, str] | tuple[str, str | None, Mapping[str, Any]]


class Label(str):
    """A label of a dimension: a str that also knows its description and attrs.

    It is its code as far as anything else can tell: it compares, hashes, sorts, prints and
    formats as that text, so it goes wherever a label string is taken, and a plain string
    finds it in a dict. `dim["PA"]` returns it; `dim.labels` lists them.

        >>> unit = model.dimensions["unit_type"]["Studio"]
        >>> unit.description, unit.attrs["beds"], unit.dimension
        ("Studio apartment", 0, "unit_type")
    """

    def __new__(cls, code: str, dimension: str | None = None, description: str | None = None,
                attrs: Mapping[str, Any] | None = None) -> Self:
        self = super().__new__(cls, code)
        self.dimension = dimension  # the name of the dimension it belongs to
        self._description = description
        self._attrs = dict(attrs or {})
        return self

    @property
    def description(self) -> str:
        """Its description; the code itself when it has none (a hierarchy: the last path segment)."""
        return self._description if self._description is not None else str(self)

    @property
    def attrs(self) -> Mapping[str, Any]:
        """Its attrs, e.g. {"end": "2025-09-27"}; empty when it has none. Read-only: change them with
        add_coords."""
        return MappingProxyType(self._attrs)


class _Labels:
    """Ordered labels with an optional description and attrs each."""

    def __init__(self, entries: Iterable[Entry]):
        labels: list[str] = []
        self.descriptions: dict[str, str] = {}
        self.label_attrs: dict[str, dict[str, Any]] = {}
        for entry in entries:
            label, description, attrs = _parse_entry(entry)
            labels.append(label)
            if description is not None:
                self.descriptions[label] = description
            if attrs:
                self.label_attrs[label] = attrs
        self.labels: tuple[str, ...] = tuple(labels)


def _parse_entry(entry: Entry) -> tuple[str, str | None, dict[str, Any]]:
    if isinstance(entry, str):
        return entry, None, {}
    if isinstance(entry, tuple) and len(entry) in (2, 3):
        code, description, attrs = (*entry, {}) if len(entry) == 2 else entry
        if (isinstance(code, str) and isinstance(description, (str, type(None)))
                and isinstance(attrs, Mapping)):
            return code, description, dict(attrs)
    raise DefinitionError(
        f"coord entry {entry!r} must be a string \"code\", a tuple (code, description) "
        "or a tuple (code, description, {attrs})"
    )


class Hierarchy:
    """Coord labels written as paths: "/", "/products", "/products/iphone".

    Each entry is a path, a tuple (path, description) or a tuple
    (path, description, {attrs}).

    A path's parent is the path with its last segment removed. Every parent must
    be declared too, except the implicit root "/" (so "/rnd" and "/gna" can stand
    alone without declaring "/").
    """

    def __init__(self, entries: Iterable[Entry]):
        parsed = _Labels(entries)
        self.paths: tuple[str, ...] = parsed.labels
        self.descriptions, self.label_attrs = parsed.descriptions, parsed.label_attrs
        seen: set[str] = set()
        for path in self.paths:
            _validate_path(path)
            if path in seen:
                raise DefinitionError(f"duplicate path {path!r}")
            seen.add(path)
        for path in self.paths:
            parent = parent_path(path)
            if parent is not None and parent != ROOT and parent not in seen:
                raise DefinitionError(
                    f"path {path!r} needs its parent {parent!r} to be declared"
                )

    def parent(self, path: str) -> str | None:
        """The declared parent of `path`, or None for a top-level path."""
        self._require(path)
        parent = parent_path(path)
        return parent if parent in self.paths else None

    def children(self, path: str) -> list[str]:
        """Declared paths whose parent is `path`, in declaration order."""
        if path != ROOT:
            self._require(path)
        return [p for p in self.paths if p != path and parent_path(p) == path]

    def description(self, path: str) -> str:
        """The entry's description, or its last path segment when it has none."""
        self._require(path)
        if path in self.descriptions:
            return self.descriptions[path]
        return path if path == ROOT else path.rsplit("/", 1)[-1]

    def _require(self, path: str) -> None:
        if path not in self.paths:
            raise UnknownCoord(f"{path!r} is not a declared path")

    def __repr__(self) -> str:
        return f"Hierarchy({list(self.paths)!r})"


def parent_path(path: str) -> str | None:
    if path == ROOT:
        return None
    head = path.rsplit("/", 1)[0]
    return head or ROOT


def _validate_path(path: str) -> None:
    if not isinstance(path, str) or not path.startswith("/"):
        raise DefinitionError(f"path {path!r} must be a string starting with '/'")
    if path != ROOT and (path.endswith("/") or "//" in path):
        raise DefinitionError(f"path {path!r} has an empty segment")


class Dimension:
    """A named dimension with ordered coord labels and an optional hierarchy.

    `coords` is a Hierarchy, a time spine (tm.period_range: its labels know their dates), or a
    list of entries: "code", ("code", "description") or ("code", "description", {attrs}).
    """

    def __init__(self, name: str, coords: Iterable[Entry] | Hierarchy | PeriodRange):
        self.name = name
        self.time: PeriodRange | None = coords if isinstance(coords, PeriodRange) else None
        if isinstance(coords, Hierarchy):
            self.hierarchy: Hierarchy | None = coords
            self.coords: tuple[str, ...] = coords.paths
            self.descriptions, self.label_attrs = coords.descriptions, coords.label_attrs
        else:
            if isinstance(coords, str):
                raise DefinitionError(f"dimension {name!r}: coords must be a list, got the string {coords!r}")
            self.hierarchy = None
            parsed = _Labels(coords)
            self.coords = parsed.labels
            self.descriptions, self.label_attrs = parsed.descriptions, parsed.label_attrs
            if len(set(self.coords)) != len(self.coords):
                raise DefinitionError(f"dimension {name!r} has duplicate coords")
        if not self.coords:
            raise DefinitionError(f"dimension {name!r} has no coords")
        for c in self.coords:
            if not isinstance(c, str):
                raise DefinitionError(f"dimension {name!r}: coord {c!r} must be a string")
        self.index: dict[str, int] = {c: i for i, c in enumerate(self.coords)}  # label -> position
        self.labels: tuple[Label, ...] = tuple(  # the same labels as objects; `coords` stays plain text
            Label(c, name, self._described(c), self.label_attrs.get(c)) for c in self.coords)

    def _described(self, label: str) -> str:
        if self.hierarchy:
            return self.hierarchy.description(label)
        return self.descriptions.get(label, label)

    def __getitem__(self, label: str) -> Label:
        """The label as an object: `dim["PA"].description`, `dim["PA"].attrs`."""
        self.require(label)
        return self.labels[self.index[label]]

    def description(self, label: str) -> str:
        """Deprecated: use `dim[label].description`."""
        warnings.warn("Dimension.description(label) is deprecated; use dim[label].description",
                      DeprecationWarning, stacklevel=2)
        return self[label].description

    def attrs(self, label: str) -> dict[str, Any]:
        """Deprecated: use `dim[label].attrs`."""
        warnings.warn("Dimension.attrs(label) is deprecated; use dim[label].attrs",
                      DeprecationWarning, stacklevel=2)
        return dict(self[label].attrs)

    def period(self, label: str) -> Period:
        """A time dimension's label as a Period, with its dates: start_time, end_time, days."""
        self.require(label)
        if self.time is None:
            raise DefinitionError(f"dimension {self.name!r} is not a time dimension; make it with tm.period_range(...)")
        return self.time.period(label)

    def require(self, label: str) -> None:
        if label not in self.index:
            raise UnknownCoord(
                f"{label!r} is not a coord of {self.name!r}; "
                f"declared: {', '.join(self.coords)}"
            )

    def __contains__(self, label: object) -> bool:
        return label in self.index

    def __repr__(self) -> str:
        kind = "hierarchy" if self.hierarchy else f"time, {self.time.freq}" if self.time else "flat"
        return f"<Dimension {self.name} ({kind}): {', '.join(self.coords)}>"

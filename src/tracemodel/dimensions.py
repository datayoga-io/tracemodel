"""Dimensions: a named, ordered set of coord labels, flat or a path hierarchy."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .errors import DefinitionError, UnknownCoord

ROOT = "/"

# A coord entry: "code", ("code", "description") or ("code", "description", {attrs}).
Entry = str | tuple[str, str] | tuple[str, str | None, Mapping[str, Any]]


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

    `coords` is a Hierarchy or a list of entries: "code", ("code", "description")
    or ("code", "description", {attrs}).
    """

    def __init__(self, name: str, coords: Iterable[Entry] | Hierarchy):
        self.name = name
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

    def description(self, label: str) -> str:
        """The label's description; a hierarchy falls back to the last path segment."""
        self.require(label)
        if self.hierarchy:
            return self.hierarchy.description(label)
        return self.descriptions.get(label, label)

    def attrs(self, label: str) -> dict[str, Any]:
        """The label's attrs, e.g. {"end": "2025-09-27"}; empty when it has none."""
        self.require(label)
        return self.label_attrs.get(label, {})

    def require(self, label: str) -> None:
        if label not in self.coords:
            raise UnknownCoord(
                f"{label!r} is not a coord of {self.name!r}; "
                f"declared: {', '.join(self.coords)}"
            )

    def __contains__(self, label: object) -> bool:
        return label in self.coords

    def __repr__(self) -> str:
        kind = "hierarchy" if self.hierarchy else "flat"
        return f"<Dimension {self.name} ({kind}): {', '.join(self.coords)}>"

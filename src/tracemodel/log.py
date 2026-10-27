"""The transaction log: an append-only stream of records, the indexes over it, and its file.

The stream is the truth. Every index here is rebuilt from it when it is read and none is saved,
so an index can't disagree with the log. Nothing is edited in place: a correction is a later
record. The design is basin's (datayoga/basis): changes, commits and branch moves as records,
each named by a hash of its contents and of the record of its kind before it, so changing
anything already written changes every name after it.

Four kinds of record:

- `Change`: one op (`tracemodel.ops.Op`).
- `Commit`: the changes it lists, the commit it follows (`parent`), who and when and why. A
  change belongs to the commit that lists it; history is a line of commits, each naming one
  parent, never two.
- `Ref`: a branch moved, from the commit it was believed to point at (`expected`) to another
  (`target`). A branch's position is its latest move; ROOT ("") means no commit.
- `Review`: a person's ruling on changes proposed on a branch (`tracemodel.review`).

On disk the log is one file of lines, each a crc32 of its JSON payload, a space, the payload.
The first line declares the format. A line with no newline is a write a crash cut short: reading
reports it (`TornLog`) unless told to drop it (`strict=False`).
"""

from __future__ import annotations

import hashlib
import json
import os
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import TraceModelError
from .ops import Op, canonical

ROOT = ""  # the commit before the first, and where a deleted branch points
FORMAT = "tracemodel-log/1"
_DIGEST = 32  # hex characters of a sha256: 128 bits


class LogError(TraceModelError):
    """The log can't be read as written, or a write to it is refused."""


class TornLog(LogError):
    """The log file ends mid-record, as a crash leaves it."""

    def __init__(self, path: str, recovered: int):
        super().__init__(f"{path}: the last line is cut short after {recovered} records; "
                         "open with strict=False to drop it")
        self.path, self.recovered = path, recovered


class StaleRef(LogError):
    """A branch move from a position the branch no longer holds."""

    def __init__(self, name: str, expected: str, actual: str):
        super().__init__(f"branch {name!r} points at {actual[:8] or 'no commit'}, "
                         f"not at {expected[:8] or 'no commit'}")
        self.name, self.expected, self.actual = name, expected, actual


def chain_id(previous: str, payload: dict[str, Any]) -> str:
    """A record's name: a hash of what came before it and of its own contents."""
    h = hashlib.sha256()
    h.update(previous.encode())
    h.update(canonical(payload).encode())
    return h.hexdigest()[:_DIGEST]


@dataclass(frozen=True)
class Change:
    id: str
    op: Op
    kind: str = "change"

    def payload(self) -> dict[str, Any]:
        return {"op": self.op.to_data()}


@dataclass(frozen=True)
class Commit:
    id: str
    parent: str
    author: str
    at: str
    message: str
    changes: tuple[str, ...]
    info: dict[str, Any] = field(default_factory=dict)  # e.g. {"approved": True}, {"approves": "proposal/x"}
    kind: str = "commit"

    def payload(self) -> dict[str, Any]:
        data = {"parent": self.parent, "author": self.author, "at": self.at, "message": self.message,
                "changes": list(self.changes)}
        return data | ({"info": self.info} if self.info else {})


@dataclass(frozen=True)
class Ref:
    id: str
    name: str
    expected: str
    target: str
    author: str
    at: str
    note: str = ""
    kind: str = "ref"

    def payload(self) -> dict[str, Any]:
        return {"name": self.name, "expected": self.expected, "target": self.target, "author": self.author,
                "at": self.at, "note": self.note}


@dataclass(frozen=True)
class Review:
    """A ruling on what a branch proposes: `action` (accept, reject or comment) on `scope` (see
    tracemodel.review), by `actor`, covering the changes up to the branch's `head` when made."""

    id: str
    branch: str
    scope: str
    action: str
    actor: str
    at: str
    head: str
    note: str = ""
    kind: str = "review"

    def payload(self) -> dict[str, Any]:
        return {"branch": self.branch, "scope": self.scope, "action": self.action, "actor": self.actor,
                "at": self.at, "head": self.head, "note": self.note}


Record = Change | Commit | Ref | Review


@dataclass
class Log:
    """The records, in the order they were appended, and the indexes over them."""

    records: list[Record] = field(default_factory=list)

    def __post_init__(self) -> None:
        records, self.records = self.records, []
        self._pos: dict[str, int] = {}
        self._changes: dict[str, Change] = {}
        self._commits: dict[str, Commit] = {}
        self._commit_of: dict[str, str] = {}  # change -> the commit that lists it
        self._children: dict[str, list[str]] = {}
        self._moves: dict[str, list[Ref]] = {}  # branch -> its moves, oldest first: the reflog
        self._last: dict[str, str] = {"change": ROOT, "ref": ROOT, "review": ROOT}  # chain heads, per kind
        self._reviews: dict[str, list[Review]] = {}  # branch -> its rulings, oldest first
        for r in records:
            self.add(r)

    # -- writing -----------------------------------------------------------
    def add(self, record: Record) -> None:
        """Append a record that already has its id, checking the id is what its contents make."""
        previous = record.parent if isinstance(record, Commit) else self._last[record.kind]
        expected = chain_id(previous, record.payload())
        if record.id != expected:
            raise LogError(f"record {record.id[:8]} doesn't match its contents (expected {expected[:8]}): "
                           "the log was changed after it was written")
        if isinstance(record, Commit):
            missing = [c for c in record.changes if c not in self._changes or c in self._commit_of]
            if missing:
                raise LogError(f"commit {record.id[:8]} lists changes that aren't open: {missing[0][:8]}")
            if record.parent != ROOT and record.parent not in self._commits:
                raise LogError(f"commit {record.id[:8]} follows an unknown commit {record.parent[:8]}")
        self._pos[record.id] = len(self.records)
        self.records.append(record)
        if isinstance(record, Change):
            self._changes[record.id] = record
            self._last["change"] = record.id
        elif isinstance(record, Commit):
            self._commits[record.id] = record
            self._children.setdefault(record.parent, []).append(record.id)
            for c in record.changes:
                self._commit_of[c] = record.id
        elif isinstance(record, Review):
            self._reviews.setdefault(record.branch, []).append(record)
            self._last["review"] = record.id
        else:
            self._moves.setdefault(record.name, []).append(record)
            self._last["ref"] = record.id

    def last(self, kind: str) -> str:
        """The id of the last change or ref appended: what the next one is named after."""
        return self._last[kind]

    # -- reading -------------------------------------------------------------
    def commit(self, id: str) -> Commit:
        try:
            return self._commits[self.resolve(id)]
        except KeyError:
            raise LogError(f"no commit {id!r}") from None

    def changes_in(self, commit: str) -> list[Change]:
        return [self._changes[c] for c in self._commits[commit].changes]

    def head(self, branch: str) -> str:
        """Where a branch points: its latest move, or ROOT if it has none."""
        moves = self._moves.get(branch)
        return moves[-1].target if moves else ROOT

    def branches(self) -> list[str]:
        """Branches that point at a commit."""
        return sorted(name for name in self._moves if self.head(name) != ROOT)

    def reviews(self, branch: str) -> list[Review]:
        return list(self._reviews.get(branch, ()))

    def position(self, id: str) -> int:
        return self._pos[id]

    def commit_of(self, change: str) -> str:
        return self._commit_of[change]

    def moves(self, branch: str) -> list[Ref]:
        return list(self._moves.get(branch, ()))

    def ancestry(self, commit: str) -> Iterator[str]:
        """A commit, then the one it follows, back to the first."""
        while commit != ROOT:
            yield commit
            commit = self._commits[commit].parent

    def chain(self, commit: str) -> list[str]:
        """The commits up to `commit`, oldest first."""
        return list(reversed(list(self.ancestry(commit))))

    def resolve(self, prefix: str) -> str:
        """A commit's full id from a unique prefix of it, as git takes one."""
        if prefix in self._commits:
            return prefix
        hits = [c for c in self._commits if c.startswith(prefix)] if prefix else []
        if len(hits) != 1:
            raise LogError(f"{'no' if not hits else 'more than one'} commit matches {prefix!r}")
        return hits[0]

    def __len__(self) -> int:
        return len(self.records)


# == the file ==================================================================
def _line(data: dict[str, Any]) -> bytes:
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    return b"%08x %s\n" % (zlib.crc32(body), body)


def _data(record: Record) -> dict[str, Any]:
    return {"kind": record.kind, "id": record.id, **record.payload()}


def _record(data: dict[str, Any]) -> Record:
    kind = data.get("kind")
    if kind == "change":
        return Change(data["id"], Op.from_data(data["op"]))
    if kind == "commit":
        return Commit(data["id"], data["parent"], data["author"], data["at"], data["message"],
                      tuple(data["changes"]), dict(data.get("info") or {}))
    if kind == "review":
        return Review(data["id"], data["branch"], data["scope"], data["action"], data["actor"], data["at"],
                      data["head"], data.get("note", ""))
    if kind == "ref":
        return Ref(data["id"], data["name"], data["expected"], data["target"], data["author"], data["at"],
                   data.get("note", ""))
    raise LogError(f"unknown record kind {kind!r}: written by a newer tracemodel?")


class LogFile:
    """The log's file: records appended as lines, read back with every line checked."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._size = 0  # the file's size when we last read or wrote it

    def read(self, strict: bool = True) -> list[Record]:
        """Every record. A last line cut short raises TornLog, or with strict=False is dropped
        (and cut from the file, so the next write starts on a clean line)."""
        if not self.path.exists():
            self._size = 0
            return []
        raw = self.path.read_bytes()
        lines = raw.splitlines(keepends=True)
        records: list[Record] = []
        good = 0
        for i, line in enumerate(lines):
            data = _parse(line)
            if data is None:
                if i == len(lines) - 1 and not line.endswith(b"\n"):
                    if strict:
                        raise TornLog(str(self.path), len(records))
                    with open(self.path, "r+b") as f:
                        f.truncate(good)
                    break
                raise LogError(f"{self.path}: line {i + 1} is damaged (its checksum doesn't match)")
            if i == 0:
                if data.get("kind") != "header" or data.get("format") != FORMAT:
                    raise LogError(f"{self.path}: not a {FORMAT} log (header {data!r})")
            else:
                records.append(_record(data))
            good += len(line)
        self._size = good
        return records

    def append(self, records: Iterable[Record]) -> None:
        """Write records at the end and sync. Refused when the file changed since we last read
        or wrote it: another writer got there first, and writing past it could lose its commit."""
        size = self.path.stat().st_size if self.path.exists() else 0
        if size != self._size:
            raise LogError(f"{self.path} was written by someone else since it was opened; open the repo again")
        out = b"" if size else _line({"kind": "header", "format": FORMAT})
        out += b"".join(_line(_data(r)) for r in records)
        with open(self.path, "ab") as f:
            f.write(out)
            f.flush()
            os.fsync(f.fileno())
        self._size = size + len(out)


def _parse(line: bytes) -> dict[str, Any] | None:
    if not line.endswith(b"\n") or len(line) < 10 or line[8:9] != b" ":
        return None
    body = line[9:-1]
    try:
        if zlib.crc32(body) != int(line[:8], 16):
            return None
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def make_change(previous: str, op: Op) -> Change:
    """A change record for `op`, named after the change before it."""
    return Change(chain_id(previous, {"op": op.to_data()}), op)


def make_commit(parent: str, author: str, at: str, message: str, changes: tuple[str, ...],
                info: dict[str, Any] | None = None) -> Commit:
    payload = {"parent": parent, "author": author, "at": at, "message": message, "changes": list(changes)}
    if info:
        payload["info"] = info
    return Commit(chain_id(parent, payload), parent, author, at, message, changes, dict(info or {}))


def make_review(previous: str, branch: str, scope: str, action: str, actor: str, at: str, head: str,
                note: str = "") -> Review:
    payload = {"branch": branch, "scope": scope, "action": action, "actor": actor, "at": at, "head": head,
               "note": note}
    return Review(chain_id(previous, payload), branch, scope, action, actor, at, head, note)


def make_ref(previous: str, name: str, expected: str, target: str, author: str, at: str, note: str = "") -> Ref:
    """A move of `name` from `expected` to `target`, named after the ref before it."""
    payload = {"name": name, "expected": expected, "target": target, "author": author, "at": at, "note": note}
    return Ref(chain_id(previous, payload), name, expected, target, author, at, note)

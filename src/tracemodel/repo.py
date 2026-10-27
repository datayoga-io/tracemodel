"""A repo: a model under version control, as git keeps code.

    repo = tm.Repo.open("models/", author="oren")
    m = repo.model                       # the checked-out branch's model, built from the log
    m.set_value("price", {"term": "T1"}, 10, SRC)
    repo.commit("first values")

The log (`tracemodel.log`) is the only thing a repo keeps. A model is a cache of it: built by
replaying the ops of every commit up to the branch's head, and dropped and rebuilt whenever it
might disagree. Changes made through `repo.model` are recorded as ops and stay pending, seen only
through that model, until `commit` writes them to the log as one commit; `discard` drops them.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import DefinitionError
from .log import Commit, Log, LogError, LogFile, make_change, make_commit, make_ref
from .model import Model
from .ops import Op, replay

__all__ = ["CommitInfo", "Repo", "Version"]

MAIN = "main"
_KEEP = 8  # models at past commits kept for reading


@dataclass(frozen=True)
class CommitInfo:
    """A commit as people read it: who, when, why, and the ops it made."""

    id: str
    parent: str
    author: str
    at: str
    message: str
    ops: tuple[Op, ...]

    @property
    def short(self) -> str:
        return self.id[:8]

    def __str__(self) -> str:
        return f"{self.short}  {self.at}  {self.author}  {self.message} ({len(self.ops)} changes)"


@dataclass(frozen=True)
class Version:
    """One change to an object, in the commit that made it."""

    commit: CommitInfo
    op: Op

    @property
    def value(self) -> Any:
        """The object after the change (None when it was removed): for a value, its entry data."""
        return self.op.new

    def __str__(self) -> str:
        return f"{self.commit.short}  {self.commit.at}  {self.commit.author}  {self.op.kind}  {self.commit.message}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Repo:
    """A model's log, its branches, and a model per branch built from them."""

    def __init__(self, log: Log, file: LogFile | None, author: str, branch: str = MAIN,
                 clock: Callable[[], str] = _now):
        if not isinstance(author, str) or not author.strip():
            raise DefinitionError("a repo needs an author: who makes its commits, e.g. author='oren'")
        self._log, self._file, self.author, self._branch, self._clock = log, file, author, branch, clock
        self._model: Model | None = None  # the checked-out branch's model, with its pending ops
        self._at: OrderedDict[str, Model] = OrderedDict()  # read-only models at past commits

    @classmethod
    def open(cls, path: str | Path, author: str, *, branch: str = MAIN, strict: bool = True,
             clock: Callable[[], str] = _now) -> Repo:
        """The repo in directory `path`, made if it doesn't exist. strict=False drops a last
        line a crash cut short instead of refusing to open."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        file = LogFile(path / "log.ndjson")
        return cls(Log(file.read(strict=strict)), file, author, branch, clock)

    @classmethod
    def memory(cls, author: str, *, branch: str = MAIN, clock: Callable[[], str] = _now) -> Repo:
        """A repo kept in memory only, e.g. for tests."""
        return cls(Log(), None, author, branch, clock)

    # -- the checked-out branch ---------------------------------------------
    @property
    def branch(self) -> str:
        return self._branch

    @property
    def head(self) -> str:
        """The commit the checked-out branch points at; "" before its first commit."""
        return self._log.head(self._branch)

    @property
    def model(self) -> Model:
        """The checked-out branch's model: its head, plus the changes not yet committed."""
        if self._model is None:
            self._model = self._build(self.head)
            self._model._journal = []
        return self._model

    def status(self) -> list[Op]:
        """The changes made to `model` and not yet committed."""
        return list(self._model._journal) if self._model is not None and self._model._journal else []

    def commit(self, message: str) -> CommitInfo:
        """Write the pending changes to the log as one commit on the checked-out branch."""
        if not isinstance(message, str) or not message.strip():
            raise DefinitionError("a commit needs a message: what changed and why")
        model = self._model
        if model is not None and model._tx is not None:
            raise DefinitionError("the model has an open transaction; commit or roll it back first")
        ops = self.status()
        if not ops:
            raise LogError("nothing to commit")
        commit = self._write(ops, message)
        model._journal.clear()  # type: ignore[union-attr]
        return self._info(commit)

    def discard(self) -> None:
        """Drop the changes not yet committed: the model is the branch's head again."""
        self._model = None

    # -- history ---------------------------------------------------------------
    def log(self, branch: str | None = None) -> list[CommitInfo]:
        """The commits on a branch (the checked-out one by default), newest first."""
        head = self._log.head(branch or self._branch)
        return [self._info(self._log.commit(c)) for c in self._log.ancestry(head)]

    def show(self, commit: str) -> CommitInfo:
        """One commit, by its id or a unique prefix of it."""
        return self._info(self._log.commit(commit))

    def at(self, commit: str) -> Model:
        """The model as it was at a commit (an id or a unique prefix): read-only."""
        id = self._log.resolve(commit)
        model = self._at.get(id)
        if model is None:
            model = self._build(id)
            model._readonly = f"it is the model at commit {id[:8]}; change the branch's model, repo.model"
            self._at[id] = model
            if len(self._at) > _KEEP:
                self._at.popitem(last=False)
        else:
            self._at.move_to_end(id)
        return model

    def history(self, line_item: str, branch: str | None = None, **coords: str) -> list[Version]:
        """Every change to one value on a branch, oldest first, each with its commit. Without
        coords on an item that has dims: the changes to the item's definition."""
        model = self.at(self.head) if self.head else self.model
        item = model.item(line_item)
        if coords or not item.dims:
            target = Op("value.set", {"item": line_item, "coords": {d: coords.get(d) for d in item.dims}}).target
        else:
            target = f"item:{line_item}"
        return self._versions(target, branch)

    def _versions(self, target: str, branch: str | None) -> list[Version]:
        out = []
        for c in self._log.chain(self._log.head(branch or self._branch)):
            commit = self._log.commit(c)
            info = None
            for change in self._log.changes_in(c):
                if change.op.target == target:
                    info = info or self._info(commit)
                    out.append(Version(info, change.op))
        return out

    def export(self, definition: str | Path, values: str | Path | None = None, at: str | None = None) -> None:
        """Write the head (or the commit `at`) in tracemodel's files: the definition, as
        model.save writes it, and the values, as model.to_jsonl does. A copy to share: the
        repo itself is its log."""
        if not (at or self.head):
            raise LogError(f"branch {self._branch!r} has no commit to export")
        model = self.at(at or self.head)
        model.save(definition)
        if values is not None:
            model.to_jsonl(values)

    # -- internals ---------------------------------------------------------------
    def _build(self, commit: str) -> Model:
        """A model from the ops of every commit up to `commit`, replayed in one transaction."""
        ops = [ch.op for c in self._log.chain(commit) for ch in self._log.changes_in(c)]
        return replay(Model(), ops)

    def _write(self, ops: list[Op], message: str, branch: str | None = None) -> Commit:
        """Append ops as one commit on a branch, and move the branch: to the file first, then
        to the log in memory, so a refused write leaves both as they were."""
        branch = branch or self._branch
        at = self._clock()
        previous, changes = self._log.last("change"), []
        for op in ops:
            changes.append(make_change(previous, op))
            previous = changes[-1].id
        parent = self._log.head(branch)
        commit = make_commit(parent, self.author, at, message.strip(), tuple(c.id for c in changes))
        ref = make_ref(self._log.last("ref"), branch, parent, commit.id, self.author, at, "commit")
        records = [*changes, commit, ref]
        if self._file is not None:
            self._file.append(records)
        for r in records:
            self._log.add(r)
        return commit

    def _info(self, commit: Commit) -> CommitInfo:
        return CommitInfo(commit.id, commit.parent, commit.author, commit.at, commit.message,
                          tuple(c.op for c in self._log.changes_in(commit.id)))

    def __repr__(self) -> str:
        where = f" at {self.head[:8]}" if self.head else " (no commits)"
        pending = f", {len(self.status())} changes pending" if self.status() else ""
        return f"<tracemodel.Repo on {self._branch!r}{where}{pending}>"


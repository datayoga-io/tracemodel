"""A repo: a model under version control, as git keeps code.

    repo = tm.Repo.open("fees/", author="oren")
    m = repo.model                       # the checked-out branch's model, built from the log
    m.set_value("rate", {"term": "T1"}, 6_000, SRC)
    repo.commit("terms from the architect agreement")

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

from . import review as rv
from .diff import Diff, diff
from .errors import DefinitionError, TraceModelError
from .log import ROOT, Commit, Log, LogError, LogFile, Record, make_change, make_commit, make_ref, make_review
from .model import Model
from .ops import Op, apply, replay, same, state

__all__ = ["Approved", "CommitInfo", "Conflict", "Conflicted", "Rebased", "Repo", "Version"]

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


@dataclass(frozen=True)
class Conflict:
    """One object both sides changed differently since the branch point."""

    target: str  # e.g. "value:rate[term=T1]"
    base: Any  # at the branch point (None: it didn't exist)
    ours: Any  # on the branch being landed
    theirs: Any  # on the branch it lands on

    def __str__(self) -> str:
        return f"{self.target}: was {_short(self.base)}, the branch says {_short(self.ours)}, the target says {_short(self.theirs)}"


class Conflicted(LogError):
    """A rebase refused because both sides changed the same objects differently. Carries every
    conflicting object, not just the first."""

    def __init__(self, branch: str, onto: str, conflicts: list[Conflict]):
        super().__init__(f"rebasing {branch!r} onto {onto!r} conflicts at {len(conflicts)} object(s): "
                         + "; ".join(map(str, conflicts)))
        self.branch, self.onto, self.conflicts = branch, onto, conflicts


@dataclass(frozen=True)
class Rebased:
    """What a rebase did: where the target points now, the commit it wrote (None for a fast
    forward), and the ops it carried, restated against the target."""

    branch: str
    onto: str
    point: str  # the branch point: the last commit both had
    head: str
    commit: CommitInfo | None
    ops: tuple[Op, ...]

    @property
    def fast_forward(self) -> bool:
        return self.commit is None


@dataclass(frozen=True)
class Approved:
    """What approve landed: the accepted objects, in one commit on `onto`, and what it left on
    the branch (rejected or undecided)."""

    branch: str
    onto: str
    commit: CommitInfo | None
    landed: tuple[str, ...]
    left: tuple[str, ...]


def _short(value: Any) -> str:
    if value is None:
        return "nothing"
    if isinstance(value, dict) and "value" in value:
        return repr(value["value"])
    if isinstance(value, dict) and "formula" in value:
        return f"= {value['formula']}"
    return "a definition"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _net(ops: list[Op]) -> list[Op]:
    """The ops that matter: a value or a check ends as its last op says, so earlier ops on it
    are dropped. Definitions keep every op, since one may build on another."""
    last = {op.target: i for i, op in enumerate(ops) if op.kind.startswith(("value.", "check."))}
    return [op for i, op in enumerate(ops) if last.get(op.target, i) == i]


def _refuse_new_failures(before: Any, after: Any, branch: str, onto: str) -> None:
    """A rebase with check=True: refused when a check run fails that passed before (or is new)."""
    failed_before = {(r.check.id, tuple(r.at.items())) for r in before.errors}
    new = [r for r in after.errors if (r.check.id, tuple(r.at.items())) not in failed_before]
    if new:
        runs = "; ".join(f"{r.check.id} {r.at}" if r.at else r.check.id for r in new)
        raise LogError(f"rebasing {branch!r} onto {onto!r} breaks checks: {runs}")


class Repo:
    """A model's log, its branches, and a model per branch built from them."""

    def __init__(self, log: Log, file: LogFile | None, author: str, branch: str = MAIN,
                 clock: Callable[[], str] = _now, self_approve: bool = True):
        if not isinstance(author, str) or not author.strip():
            raise DefinitionError("a repo needs an author: who makes its commits, e.g. author='oren'")
        self._log, self._file, self.author, self._branch, self._clock = log, file, author, branch, clock
        self.self_approve = self_approve  # this author's commits are accepted by them (a person); False for an agent
        self._model: Model | None = None  # the checked-out branch's model, with its pending ops
        self._model_head = ROOT  # the commit it was built from
        self._at: OrderedDict[str, Model] = OrderedDict()  # read-only models at past commits

    @classmethod
    def open(cls, path: str | Path, author: str, *, branch: str = MAIN, strict: bool = True,
             clock: Callable[[], str] = _now, self_approve: bool = True) -> Repo:
        """The repo in directory `path`, made if it doesn't exist. strict=False drops a last
        line a crash cut short instead of refusing to open. self_approve=False for an author
        whose changes someone else must accept (an agent): see tracemodel.review."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        file = LogFile(path / "log.ndjson")
        return cls(Log(file.read(strict=strict)), file, author, branch, clock, self_approve)

    @classmethod
    def memory(cls, author: str, *, branch: str = MAIN, clock: Callable[[], str] = _now,
               self_approve: bool = True) -> Repo:
        """A repo kept in memory only, e.g. for tests."""
        return cls(Log(), None, author, branch, clock, self_approve)

    def as_author(self, author: str, *, self_approve: bool = True) -> Repo:
        """The same log, worked on by another author: e.g. a reviewer reading an agent's repo
        in one process. Both write to the same log."""
        other = type(self)(self._log, self._file, author, self._branch, self._clock, self_approve)
        return other

    # -- the checked-out branch ---------------------------------------------
    @property
    def branch(self) -> str:
        return self._branch

    @property
    def head(self) -> str:
        """The commit the checked-out branch points at; "" before its first commit."""
        return self._log.head(self._branch)

    def head_of(self, branch: str) -> str:
        """The commit a branch points at; "" when it has none."""
        return self._log.head(branch)

    @property
    def model(self) -> Model:
        """The checked-out branch's model: its head, plus the changes not yet committed."""
        if self._model is not None and self._model_head != self.head and not self._model._journal:
            self._model = None  # the branch moved (another author committed to it): build it again
        if self._model is None:
            self._model, self._model_head = self._build(self.head), self.head
            self._model._journal = []
        return self._model

    def new_branch(self, name: str, frm: str | None = None) -> str:
        """Make a branch at the head of `frm` (a branch, default the checked-out one, or a
        commit). Returns the commit it points at."""
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise DefinitionError(f"a branch needs a name without surrounding spaces, got {name!r}")
        if self._log.head(name) != ROOT:
            raise LogError(f"branch {name!r} already exists")
        target = self._commit_of(frm or self._branch)
        if target == ROOT:
            raise LogError(f"{frm or self._branch!r} has no commit to branch from; commit first")
        self._append([make_ref(self._log.last("ref"), name, ROOT, target, self.author, self._clock(), "branch")])
        return target

    def checkout(self, name: str) -> None:
        """Make `name` the branch `model` and `commit` work on. Refused while changes are pending."""
        if self._log.head(name) == ROOT and name not in (self._branch, MAIN):
            raise LogError(f"no branch {name!r}; branches: {', '.join(self.branches()) or 'none'}")
        if self.status():
            raise LogError("commit or discard the pending changes before checking out another branch")
        self._branch, self._model = name, None

    def branches(self) -> list[str]:
        """The branches that point at a commit."""
        return self._log.branches()

    def drop_branch(self, name: str) -> None:
        """Delete a branch. Its commits stay in the log, reachable from no branch."""
        if name == self._branch:
            raise LogError(f"{name!r} is checked out; check out another branch first")
        head = self._log.head(name)
        if head == ROOT:
            raise LogError(f"no branch {name!r}")
        self._append([make_ref(self._log.last("ref"), name, head, ROOT, self.author, self._clock(), "drop")])

    def rebase(self, branch: str, onto: str = MAIN, *, message: str | None = None, check: bool = False) -> Rebased:
        """Land a branch's changes on `onto`, as one commit at the head of `onto`.

        The branch's ops since the branch point are applied, through the model's verbs, to the
        model at the head of `onto`, so they are checked against it as it stands. Refused, with
        nothing written, when the two sides changed an object differently (Conflicted, naming
        each), or when an op no longer applies; with check=True, also when a check that passes
        or isn't run on `onto` fails on the result. History stays a line: nothing merges.
        Afterwards both branches point at the new head.
        """
        if self.status():
            raise LogError("commit or discard the pending changes before rebasing")
        if branch == onto:
            raise LogError(f"can't rebase {branch!r} onto itself")
        here, there = self._log.head(branch), self._log.head(onto)
        if here == ROOT:
            raise LogError(f"no branch {branch!r}")
        point = self.branch_point(branch, onto)
        ours = [ch.op for c in self._after(here, point) for ch in self._log.changes_in(c)]
        if point == there:  # onto hasn't moved since the branch was made: move it along
            self._append([make_ref(self._log.last("ref"), onto, there, here, self.author, self._clock(),
                                   f"fast-forward to {branch}")])
            self._model = None
            return Rebased(branch, onto, point, here, None, tuple(ours))
        conflicts = self.conflicts(branch, onto)
        if conflicts:
            raise Conflicted(branch, onto, conflicts)
        trial = self._build(there)
        before = trial.check() if check else None
        with trial.recording() as carried:
            try:
                with trial.transaction():
                    for op in _net(ours):
                        if not same(state(trial, op), op.new):  # already so on the target
                            apply(trial, op)
            except TraceModelError as e:
                raise LogError(f"rebasing {branch!r} onto {onto!r}: {e}") from None
        if check:
            _refuse_new_failures(before, trial.check(), branch, onto)
        if not carried:  # everything was already so: just point the branch at the target
            self._append([make_ref(self._log.last("ref"), branch, here, there, self.author, self._clock(),
                                   f"rebased onto {onto}, nothing to carry")])
            return Rebased(branch, onto, point, there, None, ())
        commit = self._write(list(carried), message or f"rebase {branch} onto {onto}", onto,
                             also=[(branch, here)])
        self._model = None
        return Rebased(branch, onto, point, commit.id, self._info(commit), tuple(carried))

    def branch_point(self, branch: str, onto: str) -> str:
        """The last commit both branches have."""
        theirs = set(self._log.ancestry(self._log.head(onto)))
        for c in self._log.ancestry(self._log.head(branch)):
            if c in theirs:
                return c
        if not theirs:
            return ROOT
        raise LogError(f"branches {branch!r} and {onto!r} share no commit")

    def conflicts(self, branch: str, onto: str = MAIN) -> list[Conflict]:
        """The objects the branch and `onto` both changed, differently, since the branch point:
        each with its state at the branch point, on the branch, and on `onto`."""
        point = self.branch_point(branch, onto)
        here, there = self._log.head(branch), self._log.head(onto)
        ours = [ch.op for c in self._after(here, point) for ch in self._log.changes_in(c)]
        if not ours or point == there:
            return []
        base = self._build(point) if point != ROOT else Model()
        mine, other = self.at(here), self.at(there)
        out, seen = [], set()
        for op in ours:
            if op.target in seen:
                continue
            seen.add(op.target)
            b, o, t = state(base, op), state(mine, op), state(other, op)
            if not same(b, o) and not same(b, t) and not same(o, t):
                out.append(Conflict(op.target, b, o, t))
        return out

    # -- review --------------------------------------------------------------
    def review(self, branch: str, onto: str = MAIN) -> rv.Report:
        """What `branch` changes since it left `onto`, object by object, with each decision; and
        the calculated cells it moves, each naming the undecided changes it rests on."""
        proposed = self._proposed(branch, onto)
        point = self.branch_point(branch, onto)
        base = self._build(point) if point != ROOT else Model()
        mine = self.at(branch)
        items = []
        for target, (op, change, commit, author) in proposed.items():
            before, after = state(base, op), state(mine, op)
            if same(before, after):
                continue
            decision, by, level, note, comments = self._decide(branch, target, op.key, commit, author)
            items.append(rv.Item(target, "added" if before is None else "removed" if after is None else "changed",
                                 before, after, author, commit, dict((after or {}).get("meta") or {})
                                 if target.startswith("value:") else {}, decision, by, level, note, comments))
        open_ = {i.target for i in items if i.decision is None}
        calculated = [c for c in diff(base, mine, calculated=True).of("calc")]
        calculated = [type(c)(c.target, c.change, c.before, c.after, tuple(t for t in c.because if t in open_))
                      for c in calculated]
        return rv.Report(branch, onto, items, calculated)

    def unreviewed(self, branch: str, onto: str = MAIN) -> list[str]:
        """The objects `branch` changes that nobody has accepted or rejected."""
        return [i.target for i in self.review(branch, onto).undecided()]

    def accept(self, branch: str, *scopes: str, note: str = "", by: str | None = None, onto: str = MAIN) -> None:
        """Accept what `branch` changes in each scope (see tracemodel.review: cell, slice, item,
        commit, ALL). Refused when a scope covers nothing the branch changed, or only changes
        the reviewer made themselves."""
        self._rule(branch, scopes, "accept", note, by, onto)

    def reject(self, branch: str, *scopes: str, note: str = "", by: str | None = None, onto: str = MAIN) -> None:
        """Reject what `branch` changes in each scope; it stays on the branch and never lands."""
        self._rule(branch, scopes, "reject", note, by, onto)

    def comment(self, branch: str, scope: str, note: str, *, by: str | None = None, onto: str = MAIN) -> None:
        """Say something about a change without deciding it."""
        if not note.strip():
            raise DefinitionError("a comment needs a note")
        self._rule(branch, (scope,), "comment", note, by, onto)

    def approve(self, branch: str, onto: str = MAIN, *, message: str | None = None, check: bool = True) -> Approved:
        """Land what was accepted on `branch` onto `onto` as one commit; rejected and undecided
        changes stay on the branch. Refused, with nothing written, when nothing is accepted, an
        accepted change needs a definition that isn't (naming it), the two sides conflict on an
        accepted object, or (with check=True, the default) a check newly fails on the result."""
        if self.status():
            raise LogError("commit or discard the pending changes before approving")
        report = self.review(branch, onto)
        accepted = {i.target for i in report.accepted()}
        if not accepted:
            raise LogError(f"nothing on {branch!r} is accepted yet; accept changes with repo.accept")
        changed = {i.target for i in report.items}
        for i in report.accepted():
            if i.kind == "value":
                key = self._proposed(branch, onto)[i.target][0].key
                needs = [f"item:{key['item']}", *(f"dimension:{d}" for d in key["coords"])]
                missing = [n for n in needs if n in changed and n not in accepted]
                if missing:
                    raise LogError(f"approving {branch!r}: {i.target} needs {', '.join(missing)}, which isn't "
                                   f"accepted; accept it too, or reject {i.target}")
        conflicts = [c for c in self.conflicts(branch, onto) if c.target in accepted]
        if conflicts:
            raise Conflicted(branch, onto, conflicts)
        point, there = self.branch_point(branch, onto), self._log.head(onto)
        ops = [ch.op for c in self._after(self._log.head(branch), point) for ch in self._log.changes_in(c)
               if ch.op.target in accepted]
        trial = self._build(there) if there != ROOT else Model()
        before = trial.check() if check else None
        with trial.recording() as carried:
            try:
                with trial.transaction():
                    for op in _net(ops):
                        if not same(state(trial, op), op.new):
                            apply(trial, op)
            except TraceModelError as e:
                raise LogError(f"approving {branch!r}: {e}") from None
        if check:
            _refuse_new_failures(before, trial.check(), branch, onto)
        left = tuple(i.target for i in report.items if i.target not in accepted)
        if not carried:
            return Approved(branch, onto, None, (), left)
        by = {i.target: i.by for i in report.accepted()}
        commit = self._write(list(carried), message or f"approve {branch}: {len(accepted)} accepted", onto,
                             info={"approves": branch, "by": by})
        if self._branch == onto:
            self._model = None
        return Approved(branch, onto, self._info(commit), tuple(sorted(accepted)), left)

    def approved_by(self, line_item: str, branch: str | None = None, **coords: str) -> str | None:
        """Who let a value on a branch (main by default) in: the reviewer who accepted it, or
        its author when they approve their own commits; None when nobody did."""
        versions = self.history(line_item, branch or MAIN, **coords)
        if not versions:
            return None
        v = versions[-1]
        info = self._log.commit(v.commit.id).info
        if "approves" in info:
            return info.get("by", {}).get(v.op.target)
        return v.commit.author if info.get("approved") else None

    def _proposed(self, branch: str, onto: str) -> dict[str, tuple[Op, str, str, str]]:
        """Each object the branch changed since the branch point: its last op, that change, the
        commit it is in, and the commit's author."""
        point = self.branch_point(branch, onto)
        out: dict[str, tuple[Op, str, str, str]] = {}
        for c in self._after(self._log.head(branch), point):
            commit = self._log.commit(c)
            for ch in self._log.changes_in(c):
                out.pop(ch.op.target, None)  # in the order last changed
                out[ch.op.target] = (ch.op, ch.id, c, commit.author)
        return out

    def _decide(self, branch: str, target: str, key: Any, commit: str,
                author: str) -> tuple[str | None, str | None, str | None, str, tuple[str, ...]]:
        """The narrowest ruling covering a change, the later at the same level; nobody's own."""
        candidates: list[tuple[int, int, str, str, str]] = []  # level, position, action, actor, note
        comments = []
        if self._log.commit(commit).info.get("approved"):  # its author approves their own commits
            candidates.append((rv.LEVELS.index("commit"), self._log.position(commit), "accept", author, ""))
        seen: dict[str, frozenset[str]] = {}
        for r in self._log.reviews(branch):
            lv = rv.level(r.scope, target, key, commit)
            if lv is None:
                continue
            if r.head not in seen:
                seen[r.head] = frozenset(self._log.ancestry(r.head))
            if commit not in seen[r.head]:  # made before this change: it didn't see it
                continue
            if r.action == "comment":
                comments.append(f"{r.actor}: {r.note}")
            elif r.actor != author:  # nobody rules on their own change
                candidates.append((lv, self._log.position(r.id), r.action, r.actor, r.note))
        if not candidates:
            return None, None, None, "", tuple(comments)
        lv, _, action, by, note = min(candidates, key=lambda c: (c[0], -c[1]))  # narrowest, then latest
        return ("accepted" if action == "accept" else "rejected"), by, rv.LEVELS[lv], note, tuple(comments)

    def _rule(self, branch: str, scopes: tuple[str, ...], action: str, note: str, by: str | None,
              onto: str) -> None:
        if not scopes:
            raise DefinitionError(f"say what to {action}: a scope such as tm.review.cell(...) or tm.review.ALL")
        by = by or self.author
        head = self._log.head(branch)
        if head == ROOT:
            raise LogError(f"no branch {branch!r}")
        proposed = self._proposed(branch, onto)
        records: list[Record] = []
        previous = self._log.last("review")
        for scope in scopes:
            covered = [(t, a) for t, (op, _, c, a) in proposed.items()
                       if rv.level(scope, t, op.key, c) is not None]
            if not covered:
                raise LogError(f"{scope!r} covers nothing {branch!r} changed")
            if action != "comment" and all(a == by for _, a in covered):
                raise LogError(f"{by!r} can't {action} {scope!r}: they made those changes; someone else reviews them")
            records.append(make_review(previous, branch, scope, action, by, self._clock(), head, note))
            previous = records[-1].id
        self._append(records)

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
        if self._model_head != self.head:
            from .log import StaleRef

            raise StaleRef(self._branch, self._model_head, self.head)
        commit = self._write(ops, message, info={"approved": True} if self.self_approve else None)
        model._journal.clear()  # type: ignore[union-attr]
        self._model_head = commit.id
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
        """The model at a commit (an id or a unique prefix of one) or at a branch's head:
        read-only."""
        id = self._commit_of(commit)
        if id == ROOT:
            raise LogError(f"{commit!r} has no commit yet")
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

    def diff(self, a: str, b: str | None = None, *, calculated: bool = False) -> Diff:
        """What differs from commit or branch `a` to `b` (default: the checked-out model, with
        its pending changes). With calculated=True, also the calculated cells that moved, each
        naming the differing values and definitions it rests on."""
        before = self.at(a)
        after = self.model if b is None else self.at(b)
        return diff(before, after, calculated, title=f"{a} -> {b or 'working model'}")

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

    def _write(self, ops: list[Op], message: str, branch: str | None = None,
               also: list[tuple[str, str]] = (), info: dict[str, Any] | None = None) -> Commit:  # type: ignore[assignment]
        """Append ops as one commit on a branch and move the branch (and any `also` branches,
        from the commit each holds) to it."""
        branch = branch or self._branch
        at = self._clock()
        previous, changes = self._log.last("change"), []
        for op in ops:
            changes.append(make_change(previous, op))
            previous = changes[-1].id
        parent = self._log.head(branch)
        commit = make_commit(parent, self.author, at, message.strip(), tuple(c.id for c in changes), info)
        refs = [make_ref(self._log.last("ref"), branch, parent, commit.id, self.author, at, "commit")]
        for name, held in also:
            refs.append(make_ref(refs[-1].id, name, held, commit.id, self.author, at, f"rebased onto {branch}"))
        self._append([*changes, commit, *refs])
        return commit

    def _append(self, records: list[Record]) -> None:
        """To the file first, then to the log in memory, so a refused write leaves both as they
        were. A branch move from a position the branch doesn't hold is refused (StaleRef)."""
        from .log import Ref, StaleRef

        heads: dict[str, str] = {}
        for r in records:
            if isinstance(r, Ref):
                actual = heads.get(r.name, self._log.head(r.name))
                if actual != r.expected:
                    raise StaleRef(r.name, r.expected, actual)
                heads[r.name] = r.target
        if self._file is not None:
            self._file.append(records)
        for r in records:
            self._log.add(r)

    def _commit_of(self, name: str) -> str:
        """A branch's head, or a commit from its id or a unique prefix."""
        if name in self._log._moves or name == self._branch:
            return self._log.head(name)
        return self._log.resolve(name)

    def _after(self, head: str, point: str) -> list[str]:
        """The commits after `point` up to `head`, oldest first."""
        chain = self._log.chain(head)
        return chain[chain.index(point) + 1:] if point != ROOT else chain

    def _info(self, commit: Commit) -> CommitInfo:
        return CommitInfo(commit.id, commit.parent, commit.author, commit.at, commit.message,
                          tuple(c.op for c in self._log.changes_in(commit.id)))

    def __repr__(self) -> str:
        where = f" at {self.head[:8]}" if self.head else " (no commits)"
        pending = f", {len(self.status())} changes pending" if self.status() else ""
        return f"<tracemodel.Repo on {self._branch!r}{where}{pending}>"


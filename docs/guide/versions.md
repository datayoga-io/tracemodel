# Versions, branches and review

A repo keeps a model under version control, as git keeps code: every change is recorded, a
commit groups changes with an author and a message, and any past commit can be read back.

```python
repo = tm.Repo.open("models/", author="oren")   # a directory, made if it doesn't exist
m = repo.model                                 # the branch's model, built from the log
m.set_value("price", {"term": "T1"}, 10, SRC)
repo.status()                                  # the changes not yet committed, as tm.Op
repo.commit("first values")

repo.log()                                     # commits, newest first: id, author, at, message, ops
repo.at("e4c935b7").get("revenue", term="T1")  # the model at a commit (an id prefix will do), read-only
repo.history("price", term="T1")               # every change to one value, with its commit
repo.history("revenue")                        # changes to an item's definition
repo.discard()                                 # drop the changes not yet committed
repo.export("m.json", "m.values.jsonl")        # the head as tracemodel's files, to share
```

| Call | Does |
| --- | --- |
| `tm.Repo.open(path, author, *, branch="main", strict=True, self_approve=True)` | Opens the repo in a directory, making it if needed. `strict=False` drops a last line a crash cut short instead of refusing to open. `self_approve=False` for an author whose changes someone else must accept, such as an agent (see Review). |
| `tm.Repo.memory(author)` | A repo kept in memory only, e.g. for tests. |
| `repo.model` | The checked-out branch's model: its head, plus changes not yet committed. Changes made through it are recorded (see Recording changes) and stay pending until `commit`. |
| `repo.commit(message)` | Writes the pending changes to the log as one commit. Refused with nothing pending, or while the model has an open transaction. |
| `repo.discard()` | Drops the pending changes. |
| `repo.status()` | The pending changes, as `tm.Op`. |
| `repo.log()`, `repo.show(commit)` | Commits as `tm.CommitInfo` (`id`, `short`, `parent`, `author`, `at`, `message`, `ops`). |
| `repo.at(commit)` | The model at a commit (an id, a unique prefix, or a branch name), read-only: its verbs raise `DefinitionError`. |
| `repo.history(item, **coords)` | Every change to one value, oldest first, as `tm.Version` (`commit`, `op`, `value`); without coords on an item with dims, the changes to its definition. |
| `repo.new_branch(name, frm=None)` | Makes a branch at the head of `frm` (a branch, default the checked-out one, or a commit). |
| `repo.checkout(name)`, `repo.branch`, `repo.branches()`, `repo.head_of(name)` | Switches the branch `model` and `commit` work on (refused while changes are pending); the checked-out branch; every branch; where one points. |
| `repo.drop_branch(name)` | Deletes a branch; its commits stay in the log. |
| `repo.rebase(branch, onto="main", *, message=None, check=False)` | Lands a branch's changes on `onto` as one commit; see Branches. |
| `repo.conflicts(branch, onto="main")` | The objects both sides changed differently since the branch point, as `tm.Conflict` (`target`, `base`, `ours`, `theirs`). |
| `repo.diff(a, b=None, *, calculated=False)` | What differs from commit or branch `a` to `b` (default: the working model, with its pending changes); see Diff. |
| `repo.review(branch, onto="main")`, `repo.unreviewed(...)` | What a branch proposes, with each decision; see Review. |
| `repo.accept(branch, *scopes, note="")`, `repo.reject(...)`, `repo.comment(branch, scope, note)` | Rule on what a branch changes; see Review. |
| `repo.approve(branch, onto="main", *, message=None, check=True)` | Lands what was accepted, as one commit. |
| `repo.approved_by(item, branch=None, **coords)` | Who let a value in. |
| `repo.as_author(author, *, self_approve=True)` | The same repo, worked on by another author. |
| `repo.export(definition, values=None, at=None)` | Writes the head, or the commit `at`, as `model.save` and `model.to_jsonl` do. A repo has no `save`: every commit is written to its log. |

### Branches

A branch is a line of work: a what-if, a draft, an agent's proposal. It starts at a commit and
moves as it gets commits; other branches don't see them.

```python
repo.new_branch("high-qty")
repo.checkout("high-qty")
repo.model.set_value("qty", {"term": "T1"}, 60, SRC)
repo.commit("what if qty is 60")
repo.at("main").get("revenue", term="T1"), repo.at("high-qty").get("revenue", term="T1")   # side by side

repo.rebase("high-qty", onto="main")   # land it on main, or be told what conflicts
```

`rebase` takes the branch's changes since the branch point (the last commit both have) and
applies them, through the model's verbs, to the model at the head of `onto`, then writes them as
one commit there. Both branches then point at it. History stays a line: nothing merges, and no
commit has two parents. When `onto` hasn't moved since the branch was made, it just moves along
(a fast forward).

It is refused, with nothing written:

- when both sides changed an object (a value, an item, a check, a dimension) to different
  things since the branch point. `tm.Conflicted` names each with its three states; two sides
  that made the same change don't conflict. Settle it with a commit on either side and rebase
  again;
- when a change no longer applies, e.g. it reads an item `onto` removed;
- with `check=True`, when a check run fails on the result that didn't fail on `onto`.

A what-if that is never rebased is a scenario; compare it with `repo.diff`.

### Review

An agent drafts on a branch; a person reads what it changes, changes or rules on it, and lands
what was accepted. Main only ever holds approved work.

```python
from tracemodel import review as rv

agent = tm.Repo.open("models/", author="agent:drafter", self_approve=False)
agent.new_branch("proposal/draft"); agent.checkout("proposal/draft")
agent.model.set_value("price", {"term": "T1"}, 12, SRC)
agent.model.set_value("qty", {"term": "T1"}, 40, SRC)
agent.commit("first draft")

dana = tm.Repo.open("models/", author="dana")
dana.review("proposal/draft").show()          # each change: before, after, author, decision;
                                              # and each calculated cell it moves, with what it rests on
dana.accept("proposal/draft", rv.ALL, note="matches the source")
dana.reject("proposal/draft", rv.cell("qty", term="T1"), note="40 is out of date")
dana.approve("proposal/draft")                # lands the accepted changes on main, as one commit
dana.approved_by("price", term="T1")          # "dana"
```

A ruling is a record in the log, never a field on the value: a value is what its writer
entered; a ruling is what someone else decided. It names a scope, narrowest first:
`rv.cell(item, **coords)` (one value), `rv.slice(**coords)` (every value at those labels),
`rv.item(name)` / `rv.check(id)` / `rv.dimension(name)` (a definition, and an item's values),
`rv.commit(id)`, and `rv.ALL`. The narrowest ruling that covers a change decides it; at the same
level the later one wins, so a reject can follow an accept. A ruling covers the changes up to the
branch head it was made at: a value the agent changes again afterwards is undecided again.

- **Nobody approves their own changes.** A ruling never counts for a change its author made,
  and one that covers only their own changes is refused. An author with `self_approve` (a
  person, the default) accepts their own commits as they make them, so a reviewer can correct
  the draft on its branch and approve the rest.
- **`approve` lands only what was accepted;** rejected and undecided changes stay on the branch,
  and approving again later lands what was accepted since. It is refused, with nothing written,
  when an accepted value needs a definition that isn't accepted (an item the branch added),
  when main changed an accepted object differently, or (with `check=True`, the default) when a
  check newly fails on the result.
- **Only entered values are ruled on.** A calculated cell isn't stored; the review lists the
  ones the branch moves, each naming the undecided changes it rests on.

### Diff

```python
repo.diff("main", "high-qty", calculated=True).show()
# object                    change   before  after  because
# value:qty[term=T1]        changed      40     60
# calc:revenue[term=T1]     changed     400    600  value:qty[term=T1]
# calc:total[]              changed     500    700  value:qty[term=T1]
```

`repo.diff(a, b)` (or `tm.diff(model_a, model_b)`) lists each object that differs, as
changes (`target`, `change`: added, removed or changed, `before`, `after`):
dimensions, items and checks first, then entered values.
With `calculated=True` it adds every calculated cell whose value differs (`calc:...`), and
`because` names the differing entered values and formulas it rests on, so each moved figure
says which input moved it. `diff.of("calc")` picks one kind; `diff.table()` is a `Table`.

**The log.** A repo keeps one file, `log.ndjson`: one record per line, each a checksum and its
JSON. A change holds one op; a commit lists its changes and names the commit before it; a branch
move says where a branch pointed and where it points now. Records are only appended, and each is
named by a hash of its contents and of the record before it, so an edit to anything already
written is caught when the log is read. A model is a cache of the log, built by replaying it.
A second writer on the same directory is refused at commit, and told to open the repo again.

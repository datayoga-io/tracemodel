"""Branches and rebase: work on a branch, then land it on another as one commit, or be told
which objects both sides changed differently."""

import pytest

import tracemodel as tm

SRC = {"document": "Architect agreement", "url": "https://example.com/a.pdf", "section": "4.2"}


@pytest.fixture
def repo():
    r = tm.Repo.memory("oren")
    m = r.model
    m.add_dimension("term", ["T1", "T2"])
    m.add_item("rate", ("term",))
    m.add_item("units", ("term",))
    m.add_item("fee", ("term",), formula=lambda x: x["rate"] * x["units"])
    m.add_check("RATE-UNDER-10K", coords={"term": tm.ANY}, expect=lambda x: x["rate"] < 10_000)
    with m.transaction():
        for t in ("T1", "T2"):
            m.set_value("rate", {"term": t}, 6_000.0, SRC)
            m.set_value("units", {"term": t}, 100.0, SRC)
    r.commit("terms")
    return r


def edit(repo, branch, message, **cells):
    repo.checkout(branch)
    for key, value in cells.items():
        item, term = key.split("_")
        repo.model.set_value(item, {"term": term}, value, SRC)
    return repo.commit(message)


def test_branches_diverge(repo):
    start = repo.head
    assert repo.new_branch("what-if") == start
    edit(repo, "what-if", "more units", units_T1=470.0)
    assert repo.branches() == ["main", "what-if"]
    assert repo.at("what-if").get("fee", term="T1") == 6_000 * 470
    assert repo.at("main").get("fee", term="T1") == 6_000 * 100
    repo.checkout("main")
    assert repo.model.get("units", term="T1") == 100 and repo.head == start


def test_branch_names_and_checkout_rules(repo):
    repo.new_branch("draft")
    with pytest.raises(tm.LogError, match="already exists"):
        repo.new_branch("draft")
    with pytest.raises(tm.DefinitionError, match="needs a name"):
        repo.new_branch(" draft ")
    with pytest.raises(tm.LogError, match="no branch 'nope'"):
        repo.checkout("nope")
    repo.model.set_value("rate", {"term": "T1"}, 1.0, SRC)
    with pytest.raises(tm.LogError, match="commit or discard"):
        repo.checkout("draft")


def test_a_dropped_branch_keeps_its_commits(repo):
    repo.new_branch("draft")
    c = edit(repo, "draft", "draft work", rate_T1=7_000.0)
    with pytest.raises(tm.LogError, match="checked out"):
        repo.drop_branch("draft")
    repo.checkout("main")
    repo.drop_branch("draft")
    assert repo.branches() == ["main"]
    assert repo.at(c.short).get("rate", term="T1") == 7_000  # the commit is still in the log


def test_rebase_fast_forwards_when_the_target_has_not_moved(repo):
    repo.new_branch("what-if")
    c = edit(repo, "what-if", "more units", units_T1=470.0)
    done = repo.rebase("what-if", onto="main")
    assert done.fast_forward and repo.at("main").get("units", term="T1") == 470
    assert repo.log("main")[0].id == c.id


def test_rebase_lands_the_branch_as_one_commit_restated_against_the_target(repo):
    repo.new_branch("proposal")
    edit(repo, "main", "main moves T2", rate_T2=6_500.0)
    edit(repo, "proposal", "proposal T1", rate_T1=7_000.0)
    edit(repo, "proposal", "proposal units", units_T1=470.0, units_T2=30.0)
    done = repo.rebase("proposal", onto="main", message="land the proposal")
    assert not done.fast_forward and done.commit.message == "land the proposal"
    assert [o.target for o in done.ops] == ["value:rate[term=T1]", "value:units[term=T1]", "value:units[term=T2]"]
    main = repo.at("main")
    assert (main.get("rate", term="T1"), main.get("rate", term="T2"), main.get("units", term="T2")) == (7_000, 6_500, 30)
    assert repo.head_of("proposal") == repo.head_of("main") == done.head
    assert [c.message for c in repo.log("main")][:2] == ["land the proposal", "main moves T2"]


def test_a_conflict_refuses_and_names_every_object(repo):
    repo.new_branch("proposal")
    edit(repo, "main", "main", rate_T1=6_100.0, rate_T2=6_200.0)
    edit(repo, "proposal", "proposal", rate_T1=7_000.0, rate_T2=6_200.0, units_T1=1.0)
    conflicts = repo.conflicts("proposal")
    assert [(c.target, c.base["value"], c.ours["value"], c.theirs["value"]) for c in conflicts] == [
        ("value:rate[term=T1]", 6_000.0, 7_000.0, 6_100.0)]  # T2: both moved it to 6,200, no conflict
    head = repo.head_of("main")
    with pytest.raises(tm.Conflicted, match="rate\\[term=T1\\]: was 6000.0, the branch says 7000.0") as e:
        repo.rebase("proposal")
    assert len(e.value.conflicts) == 1 and repo.head_of("main") == head  # nothing written
    edit(repo, "proposal", "settle T1 as main has it", rate_T1=6_100.0)
    repo.rebase("proposal")
    assert repo.at("main").get("units", term="T1") == 1


def test_a_branch_change_that_no_longer_applies_is_refused(repo):
    repo.new_branch("proposal")
    repo.checkout("main")
    repo.model.remove_check("RATE-UNDER-10K")
    repo.model.remove_item("fee")
    repo.model.remove_item("units")
    repo.commit("drop units")
    repo.checkout("proposal")
    repo.model.set_value("units", {"term": "T1"}, 470.0, SRC)
    repo.commit("set units")
    with pytest.raises(tm.Conflicted, match="units\\[term=T1\\].*the target says nothing"):
        repo.rebase("proposal")  # main removed the value the branch changed
    repo.model.set_value("units", {"term": "T1"}, 100.0, SRC)  # back as it was
    repo.model.add_check("UNITS", coords={"term": tm.ANY}, expect=lambda x: x["units"] > 0)
    repo.commit("check units instead")
    with pytest.raises(tm.LogError, match="rebasing 'proposal' onto 'main':.*units"):
        repo.rebase("proposal")  # no conflict, but main has no units to check


def test_rebase_with_check_refuses_a_new_failure(repo):
    repo.new_branch("proposal")
    edit(repo, "main", "main moves", units_T2=50.0)
    edit(repo, "proposal", "too high", rate_T1=12_000.0)
    with pytest.raises(tm.LogError, match="breaks checks: RATE-UNDER-10K"):
        repo.rebase("proposal", check=True)
    repo.rebase("proposal")  # without check it lands; the report says what fails
    assert not repo.at("main").check().ok


def test_rebase_carries_definitions(repo):
    repo.new_branch("proposal")
    edit(repo, "main", "main moves", units_T2=50.0)
    repo.checkout("proposal")
    m = repo.model
    m.add_item("supervision", ("term",))
    m.set_value("supervision", {"term": "T1"}, 25_000.0, SRC, meta={"confidence": "assumed"})
    m.set_formula("fee", lambda x: x["rate"] * x["units"] + x["supervision"])
    m.set_value("supervision", {"term": "T2"}, 0.0, SRC)
    repo.commit("supervision fee")
    repo.rebase("proposal")
    main = repo.at("main")
    assert main.get("fee", term="T1") == 6_000 * 100 + 25_000 and main.get("fee", term="T2") == 6_000 * 50
    assert main.entry("supervision", term="T1").meta == {"confidence": "assumed"}


def test_rebase_is_saved(tmp_path):
    r = tm.Repo.open(tmp_path, "oren")
    m = r.model
    m.add_dimension("term", ["T1"])
    m.add_item("rate", ("term",))
    m.set_value("rate", {"term": "T1"}, 1.0, SRC)
    r.commit("one")
    r.new_branch("b")
    edit(r, "main", "main", rate_T1=2.0)
    r.checkout("b")
    r.model.add_item("other", ())
    r.commit("b adds an item")
    r.rebase("b")
    again = tm.Repo.open(tmp_path, "x")
    assert again.branches() == ["b", "main"] and "other" in again.at("main").items
    assert again.at("main").get("rate", term="T1") == 2

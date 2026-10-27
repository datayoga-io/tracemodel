"""Branches and rebase: work on a branch, then land it on another as one commit, or be told
which objects both sides changed differently."""

import pytest

import tracemodel as tm

SRC = {"document": "Source doc", "url": "https://example.com/doc.pdf", "section": "2"}


@pytest.fixture
def repo():
    r = tm.Repo.memory("oren")
    m = r.model
    m.add_dimension("term", ["T1", "T2"])
    m.add_item("price", ("term",))
    m.add_item("qty", ("term",))
    m.add_item("revenue", ("term",), formula=lambda x: x["price"] * x["qty"])
    m.add_check("PRICE-UNDER-20", coords={"term": tm.ANY}, expect=lambda x: x["price"] < 20)
    with m.transaction():
        for t in ("T1", "T2"):
            m.set_value("price", {"term": t}, 10.0, SRC)
            m.set_value("qty", {"term": t}, 20.0, SRC)
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
    edit(repo, "what-if", "a higher qty", qty_T1=40.0)
    assert repo.branches() == ["main", "what-if"]
    assert repo.at("what-if").get("revenue", term="T1") == 10 * 40
    assert repo.at("main").get("revenue", term="T1") == 10 * 20
    repo.checkout("main")
    assert repo.model.get("qty", term="T1") == 20 and repo.head == start


def test_branch_names_and_checkout_rules(repo):
    repo.new_branch("draft")
    with pytest.raises(tm.LogError, match="already exists"):
        repo.new_branch("draft")
    with pytest.raises(tm.DefinitionError, match="needs a name"):
        repo.new_branch(" draft ")
    with pytest.raises(tm.LogError, match="no branch 'nope'"):
        repo.checkout("nope")
    repo.model.set_value("price", {"term": "T1"}, 1.0, SRC)
    with pytest.raises(tm.LogError, match="commit or discard"):
        repo.checkout("draft")


def test_a_dropped_branch_keeps_its_commits(repo):
    repo.new_branch("draft")
    c = edit(repo, "draft", "draft work", price_T1=14.0)
    with pytest.raises(tm.LogError, match="checked out"):
        repo.drop_branch("draft")
    repo.checkout("main")
    repo.drop_branch("draft")
    assert repo.branches() == ["main"]
    assert repo.at(c.short).get("price", term="T1") == 14  # the commit is still in the log


def test_rebase_fast_forwards_when_the_target_has_not_moved(repo):
    repo.new_branch("what-if")
    c = edit(repo, "what-if", "a higher qty", qty_T1=40.0)
    done = repo.rebase("what-if", onto="main")
    assert done.fast_forward and repo.at("main").get("qty", term="T1") == 40
    assert repo.log("main")[0].id == c.id


def test_rebase_lands_the_branch_as_one_commit_restated_against_the_target(repo):
    repo.new_branch("proposal")
    edit(repo, "main", "main moves T2", price_T2=12.0)
    edit(repo, "proposal", "proposal T1", price_T1=14.0)
    edit(repo, "proposal", "proposal qty", qty_T1=40.0, qty_T2=3.0)
    done = repo.rebase("proposal", onto="main", message="land the proposal")
    assert not done.fast_forward and done.commit.message == "land the proposal"
    assert [o.target for o in done.ops] == ["value:price[term=T1]", "value:qty[term=T1]", "value:qty[term=T2]"]
    main = repo.at("main")
    assert (main.get("price", term="T1"), main.get("price", term="T2"), main.get("qty", term="T2")) == (14, 12, 3)
    assert repo.head_of("proposal") == repo.head_of("main") == done.head
    assert [c.message for c in repo.log("main")][:2] == ["land the proposal", "main moves T2"]


def test_a_conflict_refuses_and_names_every_object(repo):
    repo.new_branch("proposal")
    edit(repo, "main", "main", price_T1=11.0, price_T2=13.0)
    edit(repo, "proposal", "proposal", price_T1=14.0, price_T2=13.0, qty_T1=1.0)
    conflicts = repo.conflicts("proposal")
    assert [(c.target, c.base["value"], c.ours["value"], c.theirs["value"]) for c in conflicts] == [
        ("value:price[term=T1]", 10.0, 14.0, 11.0)]  # T2: both moved it to 13, no conflict
    head = repo.head_of("main")
    with pytest.raises(tm.Conflicted, match="price\\[term=T1\\]: was 10.0, the branch says 14.0") as e:
        repo.rebase("proposal")
    assert len(e.value.conflicts) == 1 and repo.head_of("main") == head  # nothing written
    edit(repo, "proposal", "settle T1 as main has it", price_T1=11.0)
    repo.rebase("proposal")
    assert repo.at("main").get("qty", term="T1") == 1


def test_a_branch_change_that_no_longer_applies_is_refused(repo):
    repo.new_branch("proposal")
    repo.checkout("main")
    repo.model.remove_check("PRICE-UNDER-20")
    repo.model.remove_item("revenue")
    repo.model.remove_item("qty")
    repo.commit("drop qty")
    repo.checkout("proposal")
    repo.model.set_value("qty", {"term": "T1"}, 40.0, SRC)
    repo.commit("set qty")
    with pytest.raises(tm.Conflicted, match="qty\\[term=T1\\].*the target says nothing"):
        repo.rebase("proposal")  # main removed the value the branch changed
    repo.model.set_value("qty", {"term": "T1"}, 20.0, SRC)  # back as it was
    repo.model.add_check("QTY", coords={"term": tm.ANY}, expect=lambda x: x["qty"] > 0)
    repo.commit("check qty instead")
    with pytest.raises(tm.LogError, match="rebasing 'proposal' onto 'main':.*qty"):
        repo.rebase("proposal")  # no conflict, but main has no qty to check


def test_rebase_with_check_refuses_a_new_failure(repo):
    repo.new_branch("proposal")
    edit(repo, "main", "main moves", qty_T2=6.0)
    edit(repo, "proposal", "too high", price_T1=25.0)
    with pytest.raises(tm.LogError, match="breaks checks: PRICE-UNDER-20"):
        repo.rebase("proposal", check=True)
    repo.rebase("proposal")  # without check it lands; the report says what fails
    assert not repo.at("main").check().ok


def test_rebase_carries_definitions(repo):
    repo.new_branch("proposal")
    edit(repo, "main", "main moves", qty_T2=6.0)
    repo.checkout("proposal")
    m = repo.model
    m.add_item("extra", ("term",))
    m.set_value("extra", {"term": "T1"}, 7.0, SRC)
    m.set_formula("revenue", lambda x: x["price"] * x["qty"] + x["extra"])
    m.set_value("extra", {"term": "T2"}, 0.0, SRC)
    repo.commit("add extra")
    repo.rebase("proposal")
    main = repo.at("main")
    assert main.get("revenue", term="T1") == 10 * 20 + 7 and main.get("revenue", term="T2") == 10 * 6


def test_rebase_is_saved(tmp_path):
    r = tm.Repo.open(tmp_path, "oren")
    m = r.model
    m.add_dimension("term", ["T1"])
    m.add_item("price", ("term",))
    m.set_value("price", {"term": "T1"}, 1.0, SRC)
    r.commit("one")
    r.new_branch("b")
    edit(r, "main", "main", price_T1=2.0)
    r.checkout("b")
    r.model.add_item("other", ())
    r.commit("b adds an item")
    r.rebase("b")
    again = tm.Repo.open(tmp_path, "x")
    assert again.branches() == ["b", "main"] and "other" in again.at("main").items
    assert again.at("main").get("price", term="T1") == 2

"""Reviewing a proposal: an agent drafts on a branch, a person changes, accepts or rejects, and
approve lands what was accepted."""

import pytest

import tracemodel as tm
from tracemodel import review as rv

SRC = {"document": "Source doc", "url": "https://example.com/doc.pdf", "section": "2"}
DRAFT = "proposal/draft"


def base(repo):
    m = repo.model
    m.add_dimension("term", ["T1", "T2"])
    m.add_item("price", ("term",))
    m.add_item("qty", ("term",))
    m.add_item("revenue", ("term",), formula=lambda x: x["price"] * x["qty"])
    m.add_check("PRICE-UNDER-20", coords={"term": tm.ANY}, expect=lambda x: x["price"] < 20)
    with m.transaction():
        for t in ("T1", "T2"):
            m.set_value("price", {"term": t}, 10.0, SRC)
            m.set_value("qty", {"term": t}, 20.0, SRC)
    repo.commit("terms")


def draft(agent):
    agent.new_branch(DRAFT)
    agent.checkout(DRAFT)
    m = agent.model
    m.set_value("price", {"term": "T1"}, 12.0, SRC)
    m.set_value("qty", {"term": "T1"}, 40.0, SRC)
    m.set_value("qty", {"term": "T2"}, 3.0, SRC)
    return agent.commit("first draft")


@pytest.fixture
def repos():
    person = tm.Repo.memory("dana")
    base(person)
    agent = person.as_author("agent:drafter", self_approve=False)
    draft(agent)
    return person, agent


def test_review_lists_each_change_with_what_the_writer_said(repos):
    person, _ = repos
    report = person.review(DRAFT)
    assert [(i.target, i.before["value"], i.after["value"], i.author) for i in report.items] == [
        ("value:price[term=T1]", 10.0, 12.0, "agent:drafter"),
        ("value:qty[term=T1]", 20.0, 40.0, "agent:drafter"),
        ("value:qty[term=T2]", 20.0, 3.0, "agent:drafter")]
    assert person.unreviewed(DRAFT) == [i.target for i in report.items]
    revenue = {c.target: c for c in report.calculated}
    assert revenue["calc:revenue[term=T1]"].because == ("value:price[term=T1]", "value:qty[term=T1]")
    assert "rests on undecided" in str(report)


def test_accept_everything_but_one_cell(repos):
    person, _ = repos
    person.accept(DRAFT, rv.ALL, note="matches the source")
    person.reject(DRAFT, rv.cell("qty", term="T1"), note="40 is out of date")
    report = person.review(DRAFT)
    assert [(i.decision, i.level) for i in report.items] == [
        ("accepted", "all"), ("rejected", "cell"), ("accepted", "all")]
    assert report["value:qty[term=T1]"].note == "40 is out of date"
    done = person.approve(DRAFT)
    assert done.landed == ("value:price[term=T1]", "value:qty[term=T2]")
    assert done.left == ("value:qty[term=T1]",)
    main = person.at("main")
    assert (main.get("price", term="T1"), main.get("qty", term="T1"), main.get("qty", term="T2")) == (12, 20, 3)
    assert person.approved_by("price", term="T1") == "dana"
    assert person.approved_by("qty", term="T1") == "dana"  # their own commit, approved by them
    assert person.head_of(DRAFT) != person.head_of("main")  # the rejected change stays on the branch


def test_a_later_ruling_at_the_same_level_wins(repos):
    person, _ = repos
    cell = rv.cell("qty", term="T1")
    person.accept(DRAFT, cell)
    person.reject(DRAFT, cell)
    assert person.review(DRAFT)[cell].decision == "rejected"
    person.accept(DRAFT, cell, note="checked the plan again")
    assert person.review(DRAFT)[cell].decision == "accepted"


def test_slices_items_and_commits(repos):
    person, agent = repos
    person.accept(DRAFT, rv.slice(term="T2"))
    person.reject(DRAFT, rv.item("qty"))
    person.accept(DRAFT, rv.commit(agent.head_of(DRAFT)[:8]))
    decisions = {i.target: (i.decision, i.level) for i in person.review(DRAFT).items}
    assert decisions == {"value:price[term=T1]": ("accepted", "commit"),
                         "value:qty[term=T1]": ("rejected", "item"),
                         "value:qty[term=T2]": ("accepted", "slice")}  # the slice is narrower than the item


def test_nobody_approves_their_own_changes(repos):
    _, agent = repos
    with pytest.raises(tm.LogError, match="can't accept '\\*': they made those changes"):
        agent.accept(DRAFT, rv.ALL)


def test_a_reviewer_can_change_the_draft(repos):
    person, _ = repos
    person.checkout(DRAFT)
    person.model.set_value("qty", {"term": "T1"}, 48.0, SRC)
    person.commit("48, per the update")
    report = person.review(DRAFT)
    qty = report["value:qty[term=T1]"]
    assert (qty.after["value"], qty.author, qty.decision, qty.by) == (48.0, "dana", "accepted", "dana")
    assert person.unreviewed(DRAFT) == ["value:price[term=T1]", "value:qty[term=T2]"]
    person.accept(DRAFT, rv.ALL)
    person.checkout("main")
    person.approve(DRAFT)
    assert person.at("main").get("revenue", term="T1") == 12 * 48


def test_a_change_after_a_ruling_needs_review_again(repos):
    person, agent = repos
    person.accept(DRAFT, rv.ALL)
    agent.model.set_value("qty", {"term": "T2"}, 4.0, SRC)
    agent.commit("4, not 3")
    assert person.unreviewed(DRAFT) == ["value:qty[term=T2]"]


def test_a_model_built_before_another_author_committed(repos):
    person, agent = repos
    person.checkout(DRAFT)
    assert person.model.get("qty", term="T2") == 3
    agent.model.set_value("qty", {"term": "T2"}, 4.0, SRC)
    agent.commit("4")
    assert person.model.get("qty", term="T2") == 4  # nothing pending: built again
    person.model.set_value("price", {"term": "T2"}, 11.0, SRC)
    agent.model.set_value("qty", {"term": "T2"}, 5.0, SRC)
    agent.commit("5")
    with pytest.raises(tm.StaleRef, match="points at"):
        person.commit("on an old head")


def test_approve_refuses_with_nothing_accepted(repos):
    person, _ = repos
    with pytest.raises(tm.LogError, match="nothing on 'proposal/draft' is accepted"):
        person.approve(DRAFT)


def test_a_value_cant_land_without_the_definition_it_needs(repos):
    person, agent = repos
    agent.model.add_item("extra", ("term",))
    agent.model.set_value("extra", {"term": "T1"}, 7.0, SRC)
    agent.commit("add extra")
    person.accept(DRAFT, rv.cell("extra", term="T1"))
    with pytest.raises(tm.LogError, match="needs item:extra, which isn't accepted"):
        person.approve(DRAFT)
    person.accept(DRAFT, rv.item("extra"))
    assert "value:extra[term=T1]" in person.approve(DRAFT).landed


def test_approve_reruns_the_checks(repos):
    person, agent = repos
    agent.model.set_value("price", {"term": "T2"}, 25.0, SRC)
    agent.commit("raise the T2 price")
    person.accept(DRAFT, rv.ALL)
    head = person.head_of("main")
    with pytest.raises(tm.LogError, match="breaks checks: PRICE-UNDER-20"):
        person.approve(DRAFT)
    assert person.head_of("main") == head
    person.reject(DRAFT, rv.cell("price", term="T2"))
    person.approve(DRAFT)


def test_approve_refuses_a_conflict_on_an_accepted_change(repos):
    person, _ = repos
    person.model.set_value("qty", {"term": "T1"}, 24.0, SRC)  # main moves meanwhile
    person.commit("main: 24 qty")
    person.accept(DRAFT, rv.ALL)
    with pytest.raises(tm.Conflicted, match="qty\\[term=T1\\]"):
        person.approve(DRAFT)
    person.reject(DRAFT, rv.cell("qty", term="T1"))
    person.approve(DRAFT)
    assert person.at("main").get("qty", term="T1") == 24


def test_comments_decide_nothing(repos):
    person, _ = repos
    cell = rv.cell("qty", term="T1")
    person.comment(DRAFT, cell, "where does 40 come from?")
    item = person.review(DRAFT)[cell]
    assert item.decision is None and item.comments == ("dana: where does 40 come from?",)


def test_a_scope_must_cover_something(repos):
    person, _ = repos
    with pytest.raises(tm.LogError, match="covers nothing"):
        person.accept(DRAFT, rv.cell("price", term="T2"))


def test_approve_again_lands_what_was_accepted_since(repos):
    person, _ = repos
    person.accept(DRAFT, rv.cell("price", term="T1"))
    person.approve(DRAFT)
    person.accept(DRAFT, rv.slice(term="T2"))
    second = person.approve(DRAFT)
    assert [o.target for o in second.commit.ops] == ["value:qty[term=T2]"]


def test_the_whole_flow_on_disk(tmp_path):
    """The definition of done: an agent generates a version, a person changes and approves it,
    and it is committed."""
    person = tm.Repo.open(tmp_path, "dana")
    base(person)
    draft(tm.Repo.open(tmp_path, "agent:drafter", self_approve=False))

    person = tm.Repo.open(tmp_path, "dana")  # another process, later
    assert len(person.unreviewed(DRAFT)) == 3
    person.checkout(DRAFT)
    person.model.set_value("qty", {"term": "T1"}, 48.0, SRC)
    person.commit("48, per the update")
    person.checkout("main")
    person.accept(DRAFT, rv.ALL, note="checked against the source")
    person.approve(DRAFT, message="the draft")

    again = tm.Repo.open(tmp_path, "anyone")
    assert again.log("main")[0].message == "the draft"
    assert again.at("main").get("revenue", term="T1") == 12 * 48
    assert again.approved_by("price", term="T1") == "dana"
    assert again.review(DRAFT).undecided() == []

"""Reviewing a proposal: an agent drafts on a branch, a person changes, accepts or rejects, and
approve lands what was accepted."""

import pytest

import tracemodel as tm
from tracemodel import review as rv

SRC = {"document": "Architect agreement", "url": "https://example.com/a.pdf", "section": "4.2"}
DRAFT = "proposal/architect"


def base(repo):
    m = repo.model
    m.add_dimension("term", ["T1", "T2"])
    m.add_item("rate", ("term",))
    m.add_item("units", ("term",))
    m.add_item("fee", ("term",), formula=lambda x: x["rate"] * x["units"])
    m.add_check("RATE-UNDER-10K", coords={"term": tm.ANY}, expect=lambda x: x["rate"] < 10_000)
    with m.transaction():
        for t in ("T1", "T2"):
            m.set_value("rate", {"term": t}, 6_000.0, SRC)
            m.set_value("units", {"term": t}, 100.0, SRC)
    repo.commit("terms")


def draft(agent):
    agent.new_branch(DRAFT)
    agent.checkout(DRAFT)
    m = agent.model
    m.set_value("rate", {"term": "T1"}, 6_500.0, SRC, meta={"confidence": "stated", "reasoning": "clause 4.2"})
    m.set_value("units", {"term": "T1"}, 470.0, SRC,
                meta={"confidence": "assumed", "reasoning": "units open; 470 from the feasibility study"})
    m.set_value("units", {"term": "T2"}, 30.0, SRC, meta={"confidence": "inferred"})
    return agent.commit("read the architect agreement")


@pytest.fixture
def repos():
    person = tm.Repo.memory("dana")
    base(person)
    agent = person.as_author("agent:fee-forecast", self_approve=False)
    draft(agent)
    return person, agent


def test_review_lists_each_change_with_what_the_writer_said(repos):
    person, _ = repos
    report = person.review(DRAFT)
    assert [(i.target, i.before["value"], i.after["value"], i.author) for i in report.items] == [
        ("value:rate[term=T1]", 6_000.0, 6_500.0, "agent:fee-forecast"),
        ("value:units[term=T1]", 100.0, 470.0, "agent:fee-forecast"),
        ("value:units[term=T2]", 100.0, 30.0, "agent:fee-forecast")]
    assert report["value:units[term=T1]"].meta["confidence"] == "assumed"
    assert person.unreviewed(DRAFT) == [i.target for i in report.items]
    fee = {c.target: c for c in report.calculated}
    assert fee["calc:fee[term=T1]"].because == ("value:rate[term=T1]", "value:units[term=T1]")
    assert "rests on undecided" in str(report)


def test_accept_everything_but_one_cell(repos):
    person, _ = repos
    person.accept(DRAFT, rv.ALL, note="matches the agreement")
    person.reject(DRAFT, rv.cell("units", term="T1"), note="470 is the old plan")
    report = person.review(DRAFT)
    assert [(i.decision, i.level) for i in report.items] == [
        ("accepted", "all"), ("rejected", "cell"), ("accepted", "all")]
    assert report["value:units[term=T1]"].note == "470 is the old plan"
    done = person.approve(DRAFT)
    assert done.landed == ("value:rate[term=T1]", "value:units[term=T2]")
    assert done.left == ("value:units[term=T1]",)
    main = person.at("main")
    assert (main.get("rate", term="T1"), main.get("units", term="T1"), main.get("units", term="T2")) == (6_500, 100, 30)
    assert main.entry("rate", term="T1").meta["reasoning"] == "clause 4.2"
    assert person.approved_by("rate", term="T1") == "dana"
    assert person.approved_by("units", term="T1") == "dana"  # her own commit, approved by her
    assert person.head_of(DRAFT) != person.head_of("main")  # the rejected change stays on the branch


def test_a_later_ruling_at_the_same_level_wins(repos):
    person, _ = repos
    cell = rv.cell("units", term="T1")
    person.accept(DRAFT, cell)
    person.reject(DRAFT, cell)
    assert person.review(DRAFT)[cell].decision == "rejected"
    person.accept(DRAFT, cell, note="checked the plan again")
    assert person.review(DRAFT)[cell].decision == "accepted"


def test_slices_items_and_commits(repos):
    person, agent = repos
    person.accept(DRAFT, rv.slice(term="T2"))
    person.reject(DRAFT, rv.item("units"))
    person.accept(DRAFT, rv.commit(agent.head_of(DRAFT)[:8]))
    decisions = {i.target: (i.decision, i.level) for i in person.review(DRAFT).items}
    assert decisions == {"value:rate[term=T1]": ("accepted", "commit"),
                         "value:units[term=T1]": ("rejected", "item"),
                         "value:units[term=T2]": ("accepted", "slice")}  # the slice is narrower than the item


def test_nobody_approves_their_own_changes(repos):
    _, agent = repos
    with pytest.raises(tm.LogError, match="can't accept '\\*': they made those changes"):
        agent.accept(DRAFT, rv.ALL)


def test_a_reviewer_can_change_the_draft(repos):
    person, _ = repos
    person.checkout(DRAFT)
    person.model.set_value("units", {"term": "T1"}, 480.0, SRC, meta={"confidence": "stated",
                                                                       "reasoning": "the revised plan, p. 2"})
    person.commit("480 units, per the revised plan")
    report = person.review(DRAFT)
    units = report["value:units[term=T1]"]
    assert (units.after["value"], units.author, units.decision, units.by) == (480.0, "dana", "accepted", "dana")
    assert person.unreviewed(DRAFT) == ["value:rate[term=T1]", "value:units[term=T2]"]
    person.accept(DRAFT, rv.ALL)
    person.checkout("main")
    person.approve(DRAFT)
    assert person.at("main").get("fee", term="T1") == 6_500 * 480


def test_a_change_after_a_ruling_needs_review_again(repos):
    person, agent = repos
    person.accept(DRAFT, rv.ALL)
    agent.model.set_value("units", {"term": "T2"}, 35.0, SRC)
    agent.commit("35, not 30")
    assert person.unreviewed(DRAFT) == ["value:units[term=T2]"]


def test_a_model_built_before_another_author_committed(repos):
    person, agent = repos
    person.checkout(DRAFT)
    assert person.model.get("units", term="T2") == 30
    agent.model.set_value("units", {"term": "T2"}, 35.0, SRC)
    agent.commit("35")
    assert person.model.get("units", term="T2") == 35  # nothing pending: built again
    person.model.set_value("rate", {"term": "T2"}, 6_100.0, SRC)
    agent.model.set_value("units", {"term": "T2"}, 36.0, SRC)
    agent.commit("36")
    with pytest.raises(tm.StaleRef, match="points at"):
        person.commit("on an old head")


def test_approve_refuses_with_nothing_accepted(repos):
    person, _ = repos
    with pytest.raises(tm.LogError, match="nothing on 'proposal/architect' is accepted"):
        person.approve(DRAFT)


def test_a_value_cant_land_without_the_definition_it_needs(repos):
    person, agent = repos
    agent.model.add_item("supervision", ("term",))
    agent.model.set_value("supervision", {"term": "T1"}, 25_000.0, SRC, meta={"confidence": "inferred"})
    agent.commit("supervision fee")
    person.accept(DRAFT, rv.cell("supervision", term="T1"))
    with pytest.raises(tm.LogError, match="needs item:supervision, which isn't accepted"):
        person.approve(DRAFT)
    person.accept(DRAFT, rv.item("supervision"))
    assert "value:supervision[term=T1]" in person.approve(DRAFT).landed


def test_approve_reruns_the_checks(repos):
    person, agent = repos
    agent.model.set_value("rate", {"term": "T2"}, 12_000.0, SRC)
    agent.commit("tier 2 rate")
    person.accept(DRAFT, rv.ALL)
    head = person.head_of("main")
    with pytest.raises(tm.LogError, match="breaks checks: RATE-UNDER-10K"):
        person.approve(DRAFT)
    assert person.head_of("main") == head
    person.reject(DRAFT, rv.cell("rate", term="T2"))
    person.approve(DRAFT)


def test_approve_refuses_a_conflict_on_an_accepted_change(repos):
    person, _ = repos
    person.model.set_value("units", {"term": "T1"}, 120.0, SRC)  # main moves meanwhile
    person.commit("main: 120 units")
    person.accept(DRAFT, rv.ALL)
    with pytest.raises(tm.Conflicted, match="units\\[term=T1\\]"):
        person.approve(DRAFT)
    person.reject(DRAFT, rv.cell("units", term="T1"))
    person.approve(DRAFT)
    assert person.at("main").get("units", term="T1") == 120


def test_comments_decide_nothing(repos):
    person, _ = repos
    cell = rv.cell("units", term="T1")
    person.comment(DRAFT, cell, "where does 470 come from?")
    item = person.review(DRAFT)[cell]
    assert item.decision is None and item.comments == ("dana: where does 470 come from?",)


def test_a_scope_must_cover_something(repos):
    person, _ = repos
    with pytest.raises(tm.LogError, match="covers nothing"):
        person.accept(DRAFT, rv.cell("rate", term="T2"))


def test_approve_again_lands_what_was_accepted_since(repos):
    person, _ = repos
    person.accept(DRAFT, rv.cell("rate", term="T1"))
    person.approve(DRAFT)
    person.accept(DRAFT, rv.slice(term="T2"))
    second = person.approve(DRAFT)
    assert [o.target for o in second.commit.ops] == ["value:units[term=T2]"]


def test_the_whole_flow_on_disk(tmp_path):
    """The definition of done: an agent generates a version, a person changes and approves it,
    and it is committed."""
    person = tm.Repo.open(tmp_path, "dana")
    base(person)
    draft(tm.Repo.open(tmp_path, "agent:fee-forecast", self_approve=False))

    person = tm.Repo.open(tmp_path, "dana")  # another process, later
    assert len(person.unreviewed(DRAFT)) == 3
    person.checkout(DRAFT)
    person.model.set_value("units", {"term": "T1"}, 480.0, SRC, meta={"confidence": "stated"})
    person.commit("480, per the revised plan")
    person.checkout("main")
    person.accept(DRAFT, rv.ALL, note="checked against the agreement")
    person.approve(DRAFT, message="architect terms")

    again = tm.Repo.open(tmp_path, "anyone")
    assert again.log("main")[0].message == "architect terms"
    assert again.at("main").get("fee", term="T1") == 6_500 * 480
    assert again.approved_by("rate", term="T1") == "dana"
    assert again.review(DRAFT).undecided() == []

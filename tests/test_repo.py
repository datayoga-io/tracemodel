"""A repo on one branch: commit, discard, log, history, the model at a commit, and the log file."""

import itertools
import json
import zlib

import pytest

import tracemodel as tm
from tracemodel.log import LogError, TornLog

SRC = {"document": "Source doc", "url": "https://example.com/doc.pdf", "section": "2"}


def clock():
    ticks = itertools.count(1)
    return lambda: f"2026-10-27T10:{next(ticks):02d}:00+00:00"


def first_commit(repo):
    m = repo.model
    m.add_dimension("term", ["T1", "T2"])
    m.add_item("price", ("term",))
    m.add_item("qty", ("term",), dtype=int)
    m.add_item("revenue", ("term",), formula=lambda x: x["price"] * x["qty"])
    with m.transaction():
        for t, price, qty in [("T1", 10.0, 40), ("T2", 9.0, 3)]:
            m.set_value("price", {"term": t}, price, SRC)
            m.set_value("qty", {"term": t}, qty, SRC)
    return repo.commit("first values")


@pytest.fixture
def repo():
    r = tm.Repo.memory("oren", clock=clock())
    first_commit(r)
    return r


def test_commit_writes_the_pending_changes(repo):
    assert repo.status() == []
    repo.model.set_value("qty", {"term": "T1"}, 48, SRC)
    assert [o.target for o in repo.status()] == ["value:qty[term=T1]"]
    c = repo.commit("48, per the update")
    assert repo.head == c.id and c.parent == repo.log()[1].id and repo.status() == []
    assert (c.author, c.at, c.message) == ("oren", "2026-10-27T10:02:00+00:00", "48, per the update")
    assert repo.model.get("revenue", term="T1") == 10 * 48


def test_commit_refuses_nothing_an_open_transaction_and_no_message(repo):
    with pytest.raises(LogError, match="nothing to commit"):
        repo.commit("nothing")
    repo.model.set_value("qty", {"term": "T1"}, 48, SRC)
    with pytest.raises(tm.DefinitionError, match="needs a message"):
        repo.commit("  ")
    tx = repo.model.transaction()
    with pytest.raises(tm.DefinitionError, match="open transaction"):
        repo.commit("in the middle")
    tx.rollback()
    assert [o.target for o in repo.status()] == ["value:qty[term=T1]"]


def test_a_rolled_back_transaction_leaves_nothing_pending(repo):
    with pytest.raises(tm.DefinitionError), repo.model.transaction():
        repo.model.set_value("qty", {"term": "T1"}, 48, SRC)
        repo.model.remove_item("price")  # revenue reads it: the whole transaction rolls back
    assert repo.status() == []


def test_discard_drops_the_pending_changes(repo):
    repo.model.set_value("qty", {"term": "T1"}, 999, SRC)
    repo.discard()
    assert repo.status() == [] and repo.model.get("qty", term="T1") == 40


def test_log_is_newest_first(repo):
    repo.model.set_value("qty", {"term": "T1"}, 48, SRC)
    repo.commit("revised")
    assert [c.message for c in repo.log()] == ["revised", "first values"]
    first = repo.log()[-1]
    assert first.parent == "" and len(first.ops) == 8
    assert repo.show(first.short) == first


def test_the_model_at_a_commit_is_read_only(repo):
    first = repo.head
    repo.model.set_value("qty", {"term": "T1"}, 48, SRC)
    repo.commit("revised")
    past = repo.at(first[:6])
    assert past.get("revenue", term="T1") == 10 * 40 and repo.model.get("revenue", term="T1") == 10 * 48
    with pytest.raises(tm.DefinitionError, match="read-only: it is the model at commit"):
        past.set_value("qty", {"term": "T1"}, 1, SRC)
    with pytest.raises(LogError, match="no commit matches 'zzz'"):
        repo.at("zzz")


def test_history_of_a_value_and_of_a_definition(repo):
    repo.model.set_value("qty", {"term": "T1"}, 48, SRC)
    repo.commit("revised")
    repo.model.set_formula("revenue", lambda x: x["price"] * x["qty"] * 1.17)
    repo.commit("with tax")
    versions = repo.history("qty", term="T1")
    assert [(v.commit.message, v.value["value"]) for v in versions] == [
        ("first values", 40), ("revised", 48)]
    assert [v.op.kind for v in repo.history("revenue")] == ["item.add", "item.update"]


def test_export_writes_tracemodel_files(repo, tmp_path):
    repo.model.set_value("qty", {"term": "T1"}, 999, SRC)  # pending: not exported
    repo.export(tmp_path / "m.json", tmp_path / "v.jsonl")
    again = tm.load(tmp_path / "m.json")
    again.load_jsonl(tmp_path / "v.jsonl")
    assert again.get("revenue", term="T1") == 10 * 40
    with pytest.raises(LogError, match="no commit to export"):
        tm.Repo.memory("oren").export(tmp_path / "x.json")


# == on disk ====================================================================
def test_a_repo_opens_again_from_its_log(tmp_path):
    first_commit(tm.Repo.open(tmp_path, "oren", clock=clock()))
    again = tm.Repo.open(tmp_path, "dana")
    assert again.model.get("revenue", term="T2") == 9 * 3
    again.model.set_value("qty", {"term": "T2"}, 4, SRC)
    again.commit("one more")
    assert [c.author for c in tm.Repo.open(tmp_path, "x").log()] == ["dana", "oren"]


def test_a_second_writer_is_refused(tmp_path):
    first_commit(tm.Repo.open(tmp_path, "oren"))
    a, b = tm.Repo.open(tmp_path, "a"), tm.Repo.open(tmp_path, "b")
    a.model.set_value("qty", {"term": "T1"}, 1, SRC)
    a.commit("a")
    b.model.set_value("qty", {"term": "T1"}, 2, SRC)
    with pytest.raises(LogError, match="written by someone else"):
        b.commit("b")
    assert tm.Repo.open(tmp_path, "x").log()[0].message == "a"


def test_a_torn_last_line(tmp_path):
    first_commit(tm.Repo.open(tmp_path, "oren"))
    path = tmp_path / "log.ndjson"
    whole = path.read_bytes()
    path.write_bytes(whole + b'1234abcd {"kind":"change","id":')  # a crash mid-write
    with pytest.raises(TornLog, match="cut short after"):
        tm.Repo.open(tmp_path, "oren")
    repo = tm.Repo.open(tmp_path, "oren", strict=False)
    assert path.read_bytes() == whole and repo.model.get("qty", term="T1") == 40
    repo.model.set_value("qty", {"term": "T1"}, 41, SRC)
    repo.commit("after the tear")
    assert tm.Repo.open(tmp_path, "x").model.get("qty", term="T1") == 41


def test_a_damaged_line_is_refused(tmp_path):
    first_commit(tm.Repo.open(tmp_path, "oren"))
    path = tmp_path / "log.ndjson"
    path.write_bytes(path.read_bytes().replace(b"10.0", b"99.0"))
    with pytest.raises(LogError, match="damaged"):
        tm.Repo.open(tmp_path, "oren")


def test_an_edit_with_a_good_checksum_breaks_the_chain(tmp_path):
    first_commit(tm.Repo.open(tmp_path, "oren"))
    path = tmp_path / "log.ndjson"
    lines = path.read_bytes().splitlines(keepends=True)
    for i, line in enumerate(lines):
        if b"10.0" in line:
            body = line[9:-1].replace(b"10.0", b"99.0")
            lines[i] = b"%08x %s\n" % (zlib.crc32(body), body)
    path.write_bytes(b"".join(lines))
    with pytest.raises(LogError, match="doesn't match its contents"):
        tm.Repo.open(tmp_path, "oren")


def test_the_log_is_lines_of_json(tmp_path):
    first_commit(tm.Repo.open(tmp_path, "oren"))
    lines = (tmp_path / "log.ndjson").read_text().splitlines()
    kinds = [json.loads(line[9:])["kind"] for line in lines]
    assert kinds == ["header", *["change"] * 8, "commit", "ref"]


def test_a_repo_needs_an_author():
    with pytest.raises(tm.DefinitionError, match="needs an author"):
        tm.Repo.memory("")

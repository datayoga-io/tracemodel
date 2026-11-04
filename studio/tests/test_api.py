"""The studio's API over the sample model, and the sample itself."""

import json

import pytest
from fastapi.testclient import TestClient

from studio import sample
from studio.app import create_app, sample_studio


@pytest.fixture
def client():
    return TestClient(create_app(sample_studio("tester")))


def cells(client):
    return client.get("/api/cells").json()


def value(client, item, key):
    return cells(client)["cells"][item][key]["v"]


# == the sample ==
def test_the_sample_is_a_short_history_by_different_authors():
    repo = sample.build()
    log = repo.log()
    assert len(log) == 13
    assert {c.author for c in log} == {"modeler", "project-manager", "forecast-agent"}
    assert [c.at for c in reversed(log)] == sorted(c.at for c in log)  # in the order they happened


def test_the_sample_calculates_through_its_pointers():
    m = sample.build().model
    assert m.get("payment_date", line="L05") == __import__("datetime").date(2026, 8, 30)
    assert m.get("payment_amount", line="L05") == pytest.approx(2421)
    assert m.get("payment_amount", line="L08") == pytest.approx(307.74)
    assert m.get("engagement_amount", engagement="E03") == pytest.approx(3021)
    assert m.get("contract_committed", contract="C01") == pytest.approx(469)


# == reading ==
def test_the_model_lists_dimensions_items_checks_and_views(client):
    body = client.get("/api/model").json()
    assert body["title"] == sample.TITLE and body["branch"] == "main" and len(body["head"]) == 8
    assert [x["code"] for x in body["dimensions"]["project"]["labels"]] == ["PA", "PB"]
    assert body["dimensions"]["project"]["labels"][0]["description"] == "Harbour Quarter"
    pointer = body["items"]["line_engagement"]
    assert pointer["dtype"] == "ref" and pointer["ref"] == "engagement" and pointer["kind"] == "entered"
    amount = body["items"]["payment_amount"]
    assert amount["kind"] == "calc" and amount["formula"].startswith("lambda x:") and amount["dims"] == ["line"]
    assert body["items"]["milestone_date"]["dtype"] == "date"
    assert body["items"]["site_area"]["description"] == "Site area"   # the name the grid shows
    assert body["items"]["site_area"]["note"] == "The plot the project is built on"
    assert {c["id"] for c in body["checks"]} == {"lines_have_terms", "milestones_dated", "within_contract_value"}
    assert [t["id"] for t in body["tables"]][:2] == ["quantities", "milestones"]
    assert body["tables"][0]["levels"] is True   # the grid shows the model, each project and each unit type as rows
    assert body["items"]["vat_rate"]["dims"] == [] and body["items"]["fit_out_cost"]["dims"] == ["unit_type"]
    assert {v["id"] for v in body["views"]} == {"cashflow", "unitmix", "commitments"}


def test_the_cells_hold_every_item_by_key(client):
    body = cells(client)
    assert body["pending"] == []
    assert body["cells"]["payment_amount"]["L05"] == {"v": pytest.approx(2421)}
    assert body["cells"]["payment_date"]["L05"]["v"] == "2026-08-30"
    assert body["cells"]["unit_count"]["PA|Studio"]["v"] == 20
    assert body["cells"]["unit_count"]["PA|Penthouse"] == {"v": None, "nil": True}  # n/a
    assert body["cells"]["price_rate"]["L11"] == {"v": None}                          # not entered
    assert body["cells"]["engagement_amount"]["E06"]["v"] is None                     # reads a missing rate


def test_a_cell_says_who_entered_it_and_what_it_replaced(client):
    d = client.get("/api/cell", params={"item": "price_rate", "coords": json.dumps({"line": "L05"})}).json()
    assert d["kind"] == "entered" and d["v"] == 0.45
    assert d["source"] == {"document": "Contract", "section": "Payment terms", "url": "https://example.com/contract-payment-terms.pdf"}
    assert [(h["author"], h["value"]) for h in d["history"]] == [("forecast-agent", 0.45), ("forecast-agent", 0.4)]
    assert d["history"][1]["source"]["document"] == "Contract draft v1"
    assert d["history"][0]["message"] == "payment terms from the signed contracts"
    assert d["approved_by"] == "forecast-agent"


def test_a_calculated_cell_shows_its_formula_and_what_it_read(client):
    d = client.get("/api/cell", params={"item": "payment_date", "coords": json.dumps({"line": "L05"})}).json()
    assert d["kind"] == "calc" and "milestone_date" in d["formula"]
    reads = {(r["item"], tuple(r["coords"].values())): r["v"] for r in d["reads"]}
    assert reads[("line_engagement", ("L05",))] == "E03"
    assert reads[("engagement_project", ("E03",))] == "PA"
    assert reads[("milestone_date", ("PA", "Structure"))] == "2026-07-31"
    assert ("milestone_date", ("PB", "Structure")) not in reads  # only what it read, not what it could


def test_an_unknown_cell_is_a_404(client):
    assert client.get("/api/cell", params={"item": "nope", "coords": "{}"}).status_code == 404
    assert client.get("/api/cell", params={"item": "price_rate", "coords": "not json"}).status_code == 404


def test_the_checks_name_the_cells_that_fail(client):
    by_id = {c["id"]: c for c in client.get("/api/checks").json()}
    terms = by_id["lines_have_terms"]["failing"]
    assert [r["at"] for r in terms] == [{"line": "L11"}]
    assert terms[0]["cells"] == [{"item": "price_rate", "coords": {"line": "L11"}}]
    assert "price_rate" in terms[0]["why"] and "has no value" in terms[0]["why"]
    dated = by_id["milestones_dated"]["failing"]
    assert [r["at"] for r in dated] == [{"line": "L09"}]
    assert {"item": "milestone_date", "coords": {"project": "PB", "milestone": "Permit"}} in dated[0]["cells"]
    assert [r["at"] for r in by_id["within_contract_value"]["failing"]] == [{"contract": "C02"}]


# == changing ==
def put(client, item, coords, v=None, nil=False):
    return client.put("/api/value", json={"item": item, "coords": coords, "value": v, "nil": nil})


def test_a_change_recalculates_and_stays_pending_until_committed(client):
    assert put(client, "price_rate", {"line": "L11"}, 200).json() == {"ok": True}
    body = cells(client)
    assert "price_rate|L11" in body["pending"]
    assert body["cells"]["payment_amount"]["L11"]["v"] == 200
    assert body["cells"]["engagement_amount"]["E06"]["v"] == pytest.approx(400 + 1386 + 200)
    assert body["cells"]["contract_committed"]["C02"]["v"] == pytest.approx(3021 + 1495 + 1986)
    by_id = {c["id"]: c for c in client.get("/api/checks").json()}
    assert by_id["lines_have_terms"]["failing"] == []
    failing = by_id["within_contract_value"]["failing"][0]
    assert failing["at"] == {"contract": "C02"} and "against" in failing["why"]  # now over, not missing
    assert {(c["item"], tuple(c["coords"].values())) for c in failing["cells"]} == {
        ("contract_committed", ("C02",)), ("contract_value", ("C02",))}  # the two it compares, not their inputs


def test_changing_a_pointer_moves_a_line_to_another_project(client):
    before = value(client, "payment_amount", "L10")
    assert put(client, "engagement_project", {"engagement": "E06"}, "PA").status_code == 200
    assert value(client, "payment_amount", "L10") == pytest.approx(2421) and before == pytest.approx(1386)
    assert value(client, "payment_date", "L09") == "2026-03-20"  # now from project PA's Permit date


def test_a_pointer_must_name_a_real_label(client):
    r = put(client, "engagement_project", {"engagement": "E06"}, "PZ")
    assert r.status_code == 422 and "is not one of its labels" in r.json()["detail"]
    assert value(client, "engagement_project", "E06") == "PB"


def test_a_cell_can_be_marked_nil_or_cleared(client):
    assert put(client, "unit_count", {"project": "PA", "unit_type": "Retail"}, nil=True).status_code == 200
    assert cells(client)["cells"]["unit_count"]["PA|Retail"] == {"v": None, "nil": True}
    assert put(client, "unit_count", {"project": "PA", "unit_type": "Retail"}).status_code == 200  # cleared
    assert cells(client)["cells"]["unit_count"]["PA|Retail"] == {"v": None}


def test_a_bad_value_is_refused_with_a_reason(client):
    r = put(client, "date_offset_days", {"line": "L01"}, 2.5)
    assert r.status_code == 422 and "int" in r.json()["detail"]
    assert put(client, "milestone_date", {"project": "PA", "milestone": "Permit"}, "soon").status_code == 422


def test_discard_drops_the_changes(client):
    put(client, "price_rate", {"line": "L11"}, 200)
    assert client.post("/api/discard").json() == {"ok": True}
    body = cells(client)
    assert body["pending"] == [] and body["cells"]["price_rate"]["L11"] == {"v": None}


def test_a_commit_needs_a_message_and_keeps_the_change(client):
    put(client, "price_rate", {"line": "L11"}, 200)
    assert client.post("/api/commit", json={"message": " "}).status_code == 422
    done = client.post("/api/commit", json={"message": "L11 rate from the amendment"}).json()
    assert done["author"] == "tester" and done["changes"] == 1
    body = cells(client)
    assert body["pending"] == [] and body["cells"]["price_rate"]["L11"]["v"] == 200
    assert body["head"] == done["id"]
    log = client.get("/api/log").json()
    assert log[0]["message"] == "L11 rate from the amendment" and len(log) == 14
    d = client.get("/api/cell", params={"item": "price_rate", "coords": json.dumps({"line": "L11"})}).json()
    assert d["history"][0]["author"] == "tester" and d["approved_by"] == "tester"


def test_nothing_to_commit_is_refused(client):
    assert client.post("/api/commit", json={"message": "nothing"}).status_code == 422


def test_the_page_is_served(client):
    assert client.get("/").status_code == 200


# == the shape: rows (members) and columns (items) ==
def post(client, url, body):
    return client.post(url, json=body)


def test_a_row_is_added_with_its_pointer_and_stays_pending(client):
    r = post(client, "/api/members", {"dim": "line", "code": "L12", "description": "Extra fee", "values": {"line_engagement": "E03"}})
    assert r.status_code == 200
    body = cells(client)
    assert body["changes"] == 2 and "line_engagement|L12" in body["pending"]
    assert body["cells"]["line_engagement"]["L12"]["v"] == "E03"
    assert body["cells"]["price_rate"]["L12"] == {"v": None}
    assert "L12" in [x["code"] for x in client.get("/api/model").json()["dimensions"]["line"]["labels"]]
    checks = {c["id"]: c for c in client.get("/api/checks").json()}
    assert {"line": "L12"} in [r["at"] for r in checks["lines_have_terms"]["failing"]]  # what a new line is missing


def test_a_new_row_needs_a_new_code_and_a_real_pointer(client):
    assert post(client, "/api/members", {"dim": "line", "code": "L05"}).status_code == 422
    assert post(client, "/api/members", {"dim": "line", "code": " "}).status_code == 422
    bad = post(client, "/api/members", {"dim": "line", "code": "L13", "values": {"line_engagement": "E99"}})
    assert bad.status_code == 422 and "not one of its labels" in bad.json()["detail"]
    assert "L13" not in [x["code"] for x in client.get("/api/model").json()["dimensions"]["line"]["labels"]]  # all or none


def test_removing_a_row_says_what_it_takes_and_what_stops_it(client):
    free = client.get("/api/impact/member", params={"dim": "line", "code": "L11"}).json()
    assert free["pointed_by"] == [] and free["values"] >= 4
    held = client.get("/api/impact/member", params={"dim": "engagement", "code": "E03"}).json()
    assert {(p["item"], p["coords"]["line"]) for p in held["pointed_by"]} == {("line_engagement", "L04"), ("line_engagement", "L05")}
    refused = client.delete("/api/members", params={"dim": "engagement", "code": "E03"})
    assert refused.status_code == 422 and "still pointed at by" in refused.json()["detail"]
    assert client.delete("/api/members", params={"dim": "line", "code": "L11"}).status_code == 200
    assert "L11" not in [x["code"] for x in client.get("/api/model").json()["dimensions"]["line"]["labels"]]
    assert client.post("/api/discard").status_code == 200
    assert "L11" in [x["code"] for x in client.get("/api/model").json()["dimensions"]["line"]["labels"]]  # discard brings it back


def test_a_column_is_added_as_an_item_and_calculated_from_others(client):
    r = post(client, "/api/items", {"name": "area_gross", "dims": ["project", "unit_type"], "dtype": "float",
                                    "unit": "sqm", "description": "Floor area with 10% for walls",
                                    "formula": 'lambda x: x["area_total"] * 1.1'})
    assert r.status_code == 200
    item = client.get("/api/model").json()["items"]["area_gross"]
    assert item["kind"] == "calc" and item["unit"] == "sqm" and item["dims"] == ["project", "unit_type"]
    assert cells(client)["cells"]["area_gross"]["PA|Studio"]["v"] == pytest.approx(770)
    assert cells(client)["changes"] == 1


def test_a_column_that_cannot_be_made_says_why(client):
    assert post(client, "/api/items", {"name": "price_rate", "dims": ["line"]}).status_code == 422
    r = post(client, "/api/items", {"name": "bad", "dims": ["line"], "formula": 'lambda x: x["nothing"]'})
    assert r.status_code == 422 and "nothing" in r.json()["detail"]
    assert post(client, "/api/items", {"name": "p", "dims": ["line"], "dtype": "ref", "ref": "nowhere"}).status_code == 422


def test_a_column_is_changed_or_removed_only_when_nothing_reads_it(client):
    ok = client.patch("/api/items/price_rate", json={"description": "The line's rate", "unit": "percent or amount"})
    assert ok.status_code == 200
    item = client.get("/api/model").json()["items"]["price_rate"]
    assert item["description"] == "The line's rate" and item["unit"] == "percent or amount"
    impact = client.get("/api/impact/item", params={"name": "price_rate"}).json()
    assert "payment_amount" in impact["read_by"] and "lines_have_terms" in impact["read_by"] and impact["values"] == 10
    refused = client.delete("/api/items/price_rate")
    assert refused.status_code == 422
    assert client.delete("/api/items/project_location").status_code == 200
    assert "project_location" not in client.get("/api/model").json()["items"]


def test_a_formula_can_be_replaced_or_removed(client):
    assert client.patch("/api/items/area_total", json={"formula": 'lambda x: x["unit_count"] * x["area_per_unit"] + 1'}).status_code == 200
    assert cells(client)["cells"]["area_total"]["PA|Studio"]["v"] == 701
    assert client.patch("/api/items/area_total", json={"formula": "lambda x: x['nothing']"}).status_code == 422
    assert cells(client)["cells"]["area_total"]["PA|Studio"]["v"] == 701  # the refused one changed nothing

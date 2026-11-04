"""The sample model the studio opens with: a development's payment forecast.

Projects have engagements, which come from contracts and have payment lines. A line's date is
its milestone's date in its project, plus an offset; its amount follows its price term. Every
name and figure is example data.

The model is built in a repo as a short history of commits by different authors, so the grid
shows real authors, times and earlier values. Relations are pointers (`tm.Ref`): a line points
at its engagement, an engagement at its project and its contract.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import tracemodel as tm

TITLE = "Development payment forecast"
UNIT_USE = {"Studio": "residential", "2-bed": "residential", "3-bed": "residential",
            "Retail": "retail", "Penthouse": "residential"}
BASES = ("fixed", "per_sqm_residential", "per_sqm_retail", "pct_of_sales")

DIMENSIONS: dict[str, list[tuple[str, str | None, dict[str, Any]]]] = {
    "project": [("PA", "Harbour Quarter", {}), ("PB", "Hillside Court", {})],
    "contract": [("C01", "Design services", {}), ("C02", "Main construction", {}), ("C03", "Sales and marketing", {})],
    "engagement": [("E01", "Concept and permits", {}), ("E02", "Detailed design", {}), ("E03", "Structure", {}),
                   ("E04", "Finishes", {}), ("E05", "Commission", {}), ("E06", "Structure", {})],
    "line": [("L01", "Signing fee", {}), ("L02", "Permit approved", {}), ("L03", "Design complete", {}),
             ("L04", "Advance", {}), ("L05", "Structure complete", {}), ("L06", "Finishes complete", {}),
             ("L07", "Retention release", {}), ("L08", "Commission at handover", {}), ("L09", "Advance", {}),
             ("L10", "Structure complete", {}), ("L11", "Handover payment", {})],
    "unit_type": [(u, None, {"use": use}) for u, use in UNIT_USE.items()],
    "milestone": [(m, None, {}) for m in ("Signing", "Permit", "Structure", "Handover")],
}

# What each item is called in the grid. The library keeps it as the item's description; the sentence that explains it,
# written beside each item below, is kept as its `note`.
TITLES = {
    "project_name": "Project name", "project_location": "Location", "project_number": "Project number",
    "vat_rate": "VAT rate", "site_area": "Site area", "fit_out_cost": "Fit-out cost",
    "unit_count": "Unit count", "area_per_unit": "Area per unit", "price_per_sqm": "Price per sqm",
    "milestone_date": "Milestone date", "contract_name": "Contract title", "contract_supplier": "Supplier",
    "contract_signed": "Signed on", "contract_value": "Contract value", "engagement_project": "Project",
    "engagement_contract": "Contract", "engagement_start": "Start date", "line_engagement": "Engagement",
    "date_milestone": "Date milestone", "date_offset_days": "Days from milestone", "price_basis": "Price basis",
    "price_rate": "Price rate", "area_total": "Total area", "sale_value": "Sale value", "payment_date": "Payment date",
    "payment_amount": "Payment amount", "engagement_amount": "Engagement amount", "contract_committed": "Committed",
}


def _attrs(name: str, unit: str, note: str) -> dict[str, str] | None:
    return {k: v for k, v in (("unit", unit), ("note", note)) if v} or None


# (name, dims, dtype, unit, note). Entered unless a formula is given below.
ENTERED: list[tuple[str, tuple[str, ...], Any, str, str]] = [
    ("project_name", ("project",), "str", "", "The project's name"),
    ("project_location", ("project",), "str", "", "Where the project is"),
    ("project_number", ("project",), "str", "", "The project's number"),
    ("vat_rate", (), "float", "percent", "VAT on sales, one rate for the whole model"),
    ("site_area", ("project",), "float", "sqm", "The plot the project is built on"),
    ("fit_out_cost", ("unit_type",), "float", "thousands per sqm", "Fit-out cost per square metre, the same in every project"),
    ("unit_count", ("project", "unit_type"), "int", "units", "Units of a type in the project"),
    ("area_per_unit", ("project", "unit_type"), "float", "sqm", "Floor area of one unit"),
    ("price_per_sqm", ("project", "unit_type"), "float", "thousands per sqm", "Sale price per square metre"),
    ("milestone_date", ("project", "milestone"), "date", "", "When a project milestone happens"),
    ("contract_name", ("contract",), "str", "", "The contract's title"),
    ("contract_supplier", ("contract",), "str", "", "Who the contract is with"),
    ("contract_signed", ("contract",), "date", "", "The day the contract was signed"),
    ("contract_value", ("contract",), "float", "thousands", "The contract sum"),
    ("engagement_project", ("engagement",), tm.Ref("project"), "", "The project the engagement is for"),
    ("engagement_contract", ("engagement",), tm.Ref("contract"), "", "The contract the engagement comes from"),
    ("engagement_start", ("engagement",), "date", "", "When the engagement starts"),
    ("line_engagement", ("line",), tm.Ref("engagement"), "", "The engagement a payment line belongs to"),
    ("date_milestone", ("line",), tm.Ref("milestone"), "", "The milestone a line's date is counted from"),
    ("date_offset_days", ("line",), "int", "days", "Days after the milestone, negative for before"),
    ("price_basis", ("line",), "str", "", "How the amount is priced: " + ", ".join(BASES)),
    ("price_rate", ("line",), "float", "amount, rate or percent, by basis", "The price term's number"),
]

# A line's project, found through its engagement: used by the date and the amount.
# The lambdas are the model's formulas: their source is what the grid shows.
FORMULAS: dict[str, tuple[Callable[..., Any], tuple[str, ...], Any, str, str]] = {
    "area_total": (lambda x: x["unit_count"] * x["area_per_unit"],
                   ("project", "unit_type"), "float", "sqm", "Floor area of all the units of a type"),
    "sale_value": (lambda x: x["area_total"] * x["price_per_sqm"],
                   ("project", "unit_type"), "float", "thousands", "What the units of a type sell for"),
    "payment_date": (
        lambda x: x["milestone_date"].sel(
            project=x["engagement_project"].sel(engagement=x["line_engagement"]),
            milestone=x["date_milestone"]) + x["date_offset_days"] * timedelta(days=1),
        ("line",), "date", "", "The line's milestone date in its project, plus the offset"),
    "payment_amount": (
        lambda x: (
            x["price_rate"] if x["price_basis"] == "fixed"
            else x["price_rate"] * sum(
                x["area_total"].sel(project=x["engagement_project"].sel(engagement=x["line_engagement"]), unit_type=u)
                for u in x.labels("unit_type") if UNIT_USE[u] == "residential")
            if x["price_basis"] == "per_sqm_residential"
            else x["price_rate"] * sum(
                x["area_total"].sel(project=x["engagement_project"].sel(engagement=x["line_engagement"]), unit_type=u)
                for u in x.labels("unit_type") if UNIT_USE[u] == "retail")
            if x["price_basis"] == "per_sqm_retail"
            else x["price_rate"] / 100 * sum(
                x["sale_value"].sel(project=x["engagement_project"].sel(engagement=x["line_engagement"]), unit_type=u)
                for u in x.labels("unit_type"))
            if x["price_basis"] == "pct_of_sales"
            else None),
        ("line",), "float", "thousands", "What the line pays, by its price term"),
    "engagement_amount": (
        lambda x: sum(x["payment_amount"].sel(line=ln) for ln in x.labels("line")
                      if x["line_engagement"].sel(line=ln) == x.coords["engagement"]),
        ("engagement",), "float", "thousands", "The amounts of the engagement's lines, added up"),
    "contract_committed": (
        lambda x: sum(x["engagement_amount"].sel(engagement=e) for e in x.labels("engagement")
                      if x["engagement_contract"].sel(engagement=e) == x.coords["contract"]),
        ("contract",), "float", "thousands", "The amounts of the contract's engagements, across projects"),
}

CHECKS: list[tuple[str, Callable[..., Any], str, str]] = [
    ("lines_have_terms",
     lambda x: x["price_rate"] >= 0 and x["date_offset_days"] > -10000 and bool(x["date_milestone"])
     and bool(x["price_basis"]) and bool(x["line_engagement"]),
     "line", "every payment line has a date term and a price term"),
    ("milestones_dated",
     lambda x: x["milestone_date"].sel(project=x["engagement_project"].sel(engagement=x["line_engagement"]),
                                       milestone=x["date_milestone"]) >= "2000-01-01",
     "line", "every milestone a line depends on has a date"),
    ("within_contract_value",
     lambda x: x["contract_committed"] <= x["contract_value"],
     "contract", "what a contract's engagements commit is no more than the contract value"),
]

# Where each entered item's numbers come from: (document, section, url).
SOURCES: dict[str, tuple[str, str, str]] = {
    "project": ("Project file", "Cover sheet", "https://example.com/project-file.pdf"),
    "vat_rate": ("Tax ruling", "Section 2", "https://example.com/tax-ruling.pdf"),
    "site_area": ("Planning approval", "Table 1", "https://example.com/planning-approval.pdf"),
    "fit_out_cost": ("Pricing sheet", "Fit-out rates", "https://example.com/pricing-sheet-r2.xlsx"),
    "unit_count": ("Sales brief", "Unit mix", "https://example.com/sales-brief.pdf"),
    "area_per_unit": ("Planning approval", "Table 3", "https://example.com/planning-approval.pdf"),
    "price_per_sqm": ("Pricing sheet", "Revision 2", "https://example.com/pricing-sheet-r2.xlsx"),
    "milestone_date": ("Programme", "Key dates", "https://example.com/programme.pdf"),
    "contract": ("Contract", "Cover page", "https://example.com/contract-cover.pdf"),
    "engagement": ("Contract", "Scope of services", "https://example.com/contract-scope.pdf"),
    "terms": ("Contract", "Payment terms", "https://example.com/contract-payment-terms.pdf"),
}


def _source(item: str, document: str | None = None) -> dict[str, str]:
    group = ("project" if item.startswith("project_") else "contract" if item.startswith("contract_")
             else "engagement" if item.startswith("engagement_")
             else "terms" if item in ("line_engagement", "date_milestone", "date_offset_days", "price_basis", "price_rate")
             else item)
    doc, section, url = SOURCES[group]
    return {"document": document or doc, "section": section, "url": url}


# Example values. PA's penthouses are n/a like PB's, so no sum over unit types reads an empty cell.
NAMES = {"PA": ("Harbour Quarter", "Harbour district", "P-2026-01"), "PB": ("Hillside Court", "Hill district", "P-2026-02")}
UNITS = {"PA": (20, 40, 25, 4), "PB": (10, 30, 12, 2)}
AREAS = {"PA": (35, 62, 88, 120), "PB": (32, 58, 85, 90)}
PRICES = {"PA": (3.6, 3.4, 3.3, 4.8), "PB": (3.2, 3.1, 3.0, 4.2)}
DATES = {"PA": {"Signing": "2026-01-15", "Permit": "2026-03-20", "Structure": "2026-07-31", "Handover": "2026-11-30"},
         "PB": {"Signing": "2026-02-10", "Structure": "2026-08-15", "Handover": "2026-12-15"}}
CONTRACTS = {"C01": ("Design services", "Arcform Studio", "2026-01-10", 500),
             "C02": ("Main construction", "Northline Builders", "2026-02-15", 6000),
             "C03": ("Sales and marketing", "Keyhouse Realty", "2026-08-20", 350)}
ENGAGEMENTS = {"E01": ("PA", "C01", "2026-01-15"), "E02": ("PA", "C01", "2026-02-01"), "E03": ("PA", "C02", "2026-03-01"),
               "E04": ("PA", "C02", "2026-06-01"), "E05": ("PA", "C03", "2026-09-01"), "E06": ("PB", "C02", "2026-02-20")}
# line: (engagement, milestone, offset in days, price basis, rate). L11 has no rate yet.
LINES = {"L01": ("E01", "Signing", 30, "fixed", 120), "L02": ("E01", "Permit", 0, "fixed", 80),
         "L03": ("E02", "Permit", 60, "per_sqm_residential", 0.05), "L04": ("E03", "Signing", 45, "fixed", 600),
         "L05": ("E03", "Structure", 30, "per_sqm_residential", 0.45), "L06": ("E04", "Handover", -30, "per_sqm_residential", 0.25),
         "L07": ("E04", "Handover", 30, "fixed", 150), "L08": ("E05", "Handover", 0, "pct_of_sales", 1.5),
         "L09": ("E06", "Permit", 0, "fixed", 400), "L10": ("E06", "Structure", 30, "per_sqm_residential", 0.45),
         "L11": ("E06", "Handover", 0, "fixed", None)}


def define(model: tm.Model) -> None:
    """The dimensions, items and checks."""
    for name, entries in DIMENSIONS.items():
        model.add_dimension(name, [(c, d, a) for c, d, a in entries])
    for name, dims, dtype, unit, description in ENTERED:
        model.add_item(name, dims, dtype, _attrs(name, unit, description), description=TITLES[name])
    for name, (formula, dims, dtype, unit, description) in FORMULAS.items():
        model.add_item(name, dims, dtype, _attrs(name, unit, description), formula=formula, description=TITLES[name])
    for check_id, expect, dim, description in CHECKS:
        model.add_check(check_id, expect, coords={dim: tm.ANY}, description=description)


def _commits() -> Iterator[tuple[str, str, Callable[[tm.Model], None]]]:
    """(author, message, change): the sample's history, oldest first."""
    def set_(m: tm.Model, item: str, coords: dict[str, str], value: Any, document: str | None = None) -> None:
        m.set_value(item, coords, value, _source(item, document))

    def project_details(m: tm.Model) -> None:
        for p, (name, location, number) in NAMES.items():
            set_(m, "project_name", {"project": p}, name)
            set_(m, "project_location", {"project": p}, location)
            set_(m, "project_number", {"project": p}, number)
        set_(m, "vat_rate", {}, 17)
        set_(m, "site_area", {"project": "PA"}, 5000)   # PB's is not known yet
        for unit_type, cost in zip(UNIT_USE, (1.0, 1.2, 1.2, 0.9)):
            set_(m, "fit_out_cost", {"unit_type": unit_type}, cost)
        m.set_value("fit_out_cost", {"unit_type": list(UNIT_USE)[4]}, tm.NIL, _source("fit_out_cost"))

    def programme_rev0(m: tm.Model) -> None:
        set_(m, "milestone_date", {"project": "PA", "milestone": "Handover"}, "2026-09-30", "Programme rev 0")

    def programme_rev1(m: tm.Model) -> None:
        set_(m, "milestone_date", {"project": "PA", "milestone": "Handover"}, "2026-10-31", "Programme rev 1")

    def programme(m: tm.Model) -> None:
        for p, days in DATES.items():
            for milestone, day in days.items():
                set_(m, "milestone_date", {"project": p, "milestone": milestone}, day)

    def brief_rev1(m: tm.Model) -> None:
        set_(m, "unit_count", {"project": "PA", "unit_type": "2-bed"}, 38, "Sales brief rev 1")

    def unit_mix(m: tm.Model) -> None:
        for p in DIMENSIONS_PROJECTS:
            for i, unit_type in enumerate(UNIT_USE):
                c = {"project": p, "unit_type": unit_type}
                if i < 4:
                    set_(m, "unit_count", c, UNITS[p][i])
                    set_(m, "area_per_unit", c, AREAS[p][i])
                else:  # Penthouse: none in either project
                    for item in ("unit_count", "area_per_unit"):
                        m.set_value(item, c, tm.NIL, _source(item))

    def prices(m: tm.Model) -> None:
        for p in DIMENSIONS_PROJECTS:
            for i, unit_type in enumerate(UNIT_USE):
                c = {"project": p, "unit_type": unit_type}
                if i < 4:
                    set_(m, "price_per_sqm", c, PRICES[p][i])
                else:
                    m.set_value("price_per_sqm", c, tm.NIL, _source("price_per_sqm"))

    def contracts(m: tm.Model) -> None:
        for k, (name, supplier, signed, value) in CONTRACTS.items():
            c = {"contract": k}
            set_(m, "contract_name", c, name)
            set_(m, "contract_supplier", c, supplier)
            set_(m, "contract_signed", c, signed)
            set_(m, "contract_value", c, float(value))

    def engagements_draft(m: tm.Model) -> None:
        set_(m, "engagement_project", {"engagement": "E04"}, "PB", "Contract draft v1")

    def engagements(m: tm.Model) -> None:
        for e, (project, contract, start) in ENGAGEMENTS.items():
            c = {"engagement": e}
            set_(m, "engagement_project", c, project)
            set_(m, "engagement_contract", c, contract)
            set_(m, "engagement_start", c, start)

    def terms_draft(m: tm.Model) -> None:
        set_(m, "price_rate", {"line": "L05"}, 0.4, "Contract draft v1")

    def terms(m: tm.Model) -> None:
        for ln, (engagement, milestone, offset, basis, rate) in LINES.items():
            c = {"line": ln}
            set_(m, "line_engagement", c, engagement)
            set_(m, "date_milestone", c, milestone)
            set_(m, "date_offset_days", c, offset)
            set_(m, "price_basis", c, basis)
            if rate is not None:
                set_(m, "price_rate", c, rate)

    # in the order they happened: each author's commit, with the time it got
    yield "modeler", "the model: dimensions, items and checks", define
    yield "project-manager", "project details from the project file", project_details
    yield "project-manager", "programme rev 0: handover date", programme_rev0
    yield "forecast-agent", "unit mix from the sales brief rev 1", brief_rev1
    yield "project-manager", "programme rev 1: handover date", programme_rev1
    yield "forecast-agent", "engagements from contract draft v1", engagements_draft
    yield "forecast-agent", "payment terms from contract draft v1", terms_draft
    yield "project-manager", "key dates from the programme", programme
    yield "forecast-agent", "unit mix from the sales brief", unit_mix
    yield "project-manager", "sale prices, revision 2", prices
    yield "forecast-agent", "contracts from the signed contracts", contracts
    yield "forecast-agent", "engagements from the signed contracts", engagements
    yield "forecast-agent", "payment terms from the signed contracts", terms


DIMENSIONS_PROJECTS = [c for c, _, _ in DIMENSIONS["project"]]

# Views for the grid. A model table holds the items that share their dimensions, so it comes from the model;
# a saved view is a layout over items from the tables, and holds no data of its own.
TABLES = [
    {"id": "quantities", "title": "Project quantities", "rows": ["project", "unit_type"], "levels": True},
    {"id": "milestones", "title": "Milestones", "rows": ["project", "milestone"]},
    {"id": "contracts", "title": "Contracts", "rows": ["contract"]},
    {"id": "engagements", "title": "Engagements", "rows": ["engagement"]},
    {"id": "lines", "title": "Payment lines", "rows": ["line"]},
]
VIEWS = [
    {"id": "cashflow", "title": "Cash flow", "items": ["payment_amount"],
     "preset": {"rows": ["line"], "columns": ["payment_date.dt.year", "payment_date.dt.month"],
                "dense": {"rows": True, "columns": True}}},
    {"id": "unitmix", "title": "Unit mix", "items": ["unit_count", "area_total", "sale_value"],
     "preset": {"rows": ["project"], "columns": ["unit_type", "__items"]}},
    {"id": "commitments", "title": "Contract commitments", "items": ["contract_value", "contract_committed"],
     "preset": {"rows": ["contract"], "columns": ["__items"]}},
]


def build(clock: Callable[[], str] | None = None) -> tm.Repo:
    """A repo holding the sample model, committed as a short history. The returned repo is `main`'s."""
    times = iter(f"2026-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:00+00:00" for mo, d, h, mi in [
        (9, 1, 9, 0), (9, 2, 9, 30), (9, 2, 10, 0), (9, 20, 9, 0), (9, 28, 9, 0), (10, 5, 8, 0), (10, 5, 9, 0),
        (10, 9, 10, 5), (10, 12, 9, 14), (10, 14, 11, 40), (10, 16, 8, 30), (10, 16, 8, 40), (10, 16, 8, 50)])
    def history_then_now() -> str:  # the sample's own times first; later commits, made in the studio, get the real time
        return next(times, None) or datetime.now(UTC).isoformat(timespec="seconds")

    repo = tm.Repo.memory("modeler", clock=clock or history_then_now)
    for author, message, change in _commits():
        worker = repo.as_author(author)
        change(worker.model)
        worker.commit(message)
    return repo

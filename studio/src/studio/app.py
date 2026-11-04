"""The studio's API and the page that uses it.

    GET  /api/model      the definition: dimensions, items, checks and the views the grid offers
    GET  /api/cells      every cell's value, and which have changes not yet committed
    GET  /api/cell       one cell with its source and history, or its formula and what it read
    GET  /api/checks     each check with the cells its failing runs read
    PUT  /api/value      change a cell (an uncommitted change)
    POST /api/discard    drop the uncommitted changes
    POST /api/commit     commit them with a message
    GET  /api/log        the commits
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import tracemodel as tm
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import sample
from .service import Studio

STATIC = Path(__file__).parent / "static"


class ValueIn(BaseModel):
    item: str
    coords: dict[str, str]
    value: float | int | str | None = None
    nil: bool = False  # entered as nil: the source shows nothing


class CommitIn(BaseModel):
    message: str


class MemberIn(BaseModel):
    dim: str
    code: str
    description: str = ""
    values: dict[str, str] = {}   # items of this dimension alone to give the new label, e.g. a pointer to its parent


class ItemIn(BaseModel):
    name: str
    dims: list[str]
    dtype: str = "float"          # float, int, str, date or ref
    ref: str | None = None        # the dimension a pointer points at
    unit: str = ""
    description: str = ""
    formula: str = ""             # "lambda x: ..." for a calculated item


class ItemPatch(BaseModel):
    description: str | None = None
    unit: str | None = None
    formula: str | None = None    # "" removes the formula; leave it out to keep it


def sample_studio(author: str = "you") -> Studio:
    """The studio over the sample model, worked on by `author`."""
    repo = sample.build().as_author(author)
    return Studio(repo, sample.TITLE, sample.VIEWS)


SETTINGS = "studio.json"   # beside the repo's log: {"title": ..., "views": [...]}; the studio's own, not the model's


def open_studio(path: str | Path, author: str = "you") -> Studio:
    """The studio over the repo in directory `path` (made if it doesn't exist), worked on by `author`.

    Its title and saved views come from `studio.json` in that directory; without one the title is
    the directory's name and there are no views.
    """
    path = Path(path)
    repo = tm.Repo.open(path, author)
    settings = path / SETTINGS
    data = json.loads(settings.read_text()) if settings.exists() else {}
    return Studio(repo, data.get("title") or path.resolve().name, data.get("views", []))


def create_app(studio: Studio | None = None) -> FastAPI:
    """The app over `studio` (the sample model by default; later, a model the user loads)."""
    studio = studio or sample_studio()
    app = FastAPI(title="tracemodel studio")

    @app.exception_handler(tm.TraceModelError)
    async def model_error(_request: Any, e: tm.TraceModelError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.get("/api/model")
    def model() -> dict[str, Any]:
        return studio.definition()

    @app.get("/api/cells")
    def cells() -> dict[str, Any]:
        return studio.cells()

    @app.get("/api/cell")
    def cell(item: str, coords: str = Query(description="JSON object: dimension to label")) -> dict[str, Any]:
        try:
            return studio.cell(item, json.loads(coords))
        except (json.JSONDecodeError, KeyError, tm.UnknownItem, tm.UnknownCoord) as e:
            raise HTTPException(404, f"no such cell: {e}") from None

    @app.get("/api/checks")
    def checks() -> list[dict[str, Any]]:
        return studio.checks()

    @app.put("/api/value")
    def put_value(body: ValueIn) -> dict[str, bool]:
        studio.set_value(body.item, body.coords, body.value, body.nil)
        return {"ok": True}

    @app.post("/api/discard")
    def discard() -> dict[str, bool]:
        studio.discard()
        return {"ok": True}

    @app.post("/api/commit")
    def commit(body: CommitIn) -> dict[str, Any]:
        return studio.commit(body.message)

    @app.post("/api/members")
    def add_member(body: MemberIn) -> dict[str, bool]:
        studio.add_member(body.dim, body.code, body.description, body.values)
        return {"ok": True}

    @app.delete("/api/members")
    def remove_member(dim: str, code: str) -> dict[str, bool]:
        studio.remove_member(dim, code)
        return {"ok": True}

    @app.get("/api/impact/member")
    def member_impact(dim: str, code: str) -> dict[str, Any]:
        return studio.member_impact(dim, code)

    @app.post("/api/items")
    def add_item(body: ItemIn) -> dict[str, bool]:
        studio.add_item(body.model_dump())
        return {"ok": True}

    @app.patch("/api/items/{name}")
    def patch_item(name: str, body: ItemPatch) -> dict[str, bool]:
        studio.update_item(name, body.description, body.unit, body.formula, "formula" in body.model_fields_set)
        return {"ok": True}

    @app.delete("/api/items/{name}")
    def remove_item(name: str) -> dict[str, bool]:
        studio.remove_item(name)
        return {"ok": True}

    @app.get("/api/impact/item")
    def item_impact(name: str) -> dict[str, Any]:
        return studio.item_impact(name)

    @app.get("/api/log")
    def log() -> list[dict[str, Any]]:
        return studio.log()

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app

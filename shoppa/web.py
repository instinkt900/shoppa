"""FastAPI web layer.

`create_app()` is a factory: it loads config, opens the database (running
migrations), resolves the display timezone, and defines every route as a closure
capturing `cfg`/`db`/`tz` — no module globals, so the app is trivial to build in a
test. Storage stays UTC epoch seconds; conversion to the configured timezone
happens only in this file.

The phone UI is HTMX-driven: mutations POST a form and get back the re-rendered
`partials/board.html` fragment to swap in. The same endpoints answer JSON when the
request isn't from HTMX, and `GET /api/items` / `GET /api/pending` are the stable
JSON seam anything else (a Home Assistant card, a shell script) would poll.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from shoppa import __version__, items as items_logic
from shoppa.config import load_config
from shoppa.db import Database
from shoppa.models import Item

_HERE = Path(__file__).parent

# A tile has one line for both fields, so an essay in the note box would just be
# clipped by CSS. Cut it at the source instead, where the limit is at least honest.
MAX_NAME = 80
MAX_NOTE = 120

# How many suggestions the add form offers. The box hangs over the previous list
# while the keyboard is up, so more than about this many would cover the screen
# with the thing you were trying to avoid reading.
SUGGEST_LIMIT = 6

log = logging.getLogger(__name__)


def create_app(config_path: str) -> FastAPI:
    cfg = load_config(config_path)
    tz = ZoneInfo(cfg.web.timezone)
    db = Database(cfg.database.path)
    db.init_db()

    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    # Every template stamps `?v={{ v }}` onto its asset URLs, so a release always
    # reaches clients even if something is holding an old copy.
    templates.env.globals["v"] = __version__
    app = FastAPI(title="Shoppa", version=__version__)
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    @app.middleware("http")
    async def revalidate_static(request: Request, call_next):
        """Force the browser to revalidate every static asset.

        StaticFiles sends ETag/Last-Modified but no Cache-Control, which leaves
        Chrome free to reuse a heuristically-fresh copy without asking. Android
        PWAs hit this hard: a reinstalled app kept running JS it had cached before
        the deploy, and because the service worker's own `fetch()` reads the same
        HTTP cache, bumping the SW cache version just re-pinned the stale file.
        `no-cache` still allows caching — it only requires an ETag check first, so
        the usual answer is a cheap 304.
        """
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # --- helpers (closures over cfg/db/tz) ----------------------------------

    def now_epoch() -> int:
        return int(time.time())

    def local_str(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz).strftime("%a %d %b, %H:%M")

    def item_json(i: Item, now: int) -> dict:
        # `since` is measured from whichever stamp put the item in the list it's
        # currently in, so the phrase means the same thing in both lists: how long
        # it has been sitting where it is.
        anchor = i.added_at if i.pending else (i.done_at or i.created_at)
        return {
            "id": i.id,
            "name": i.name,
            "note": i.note,
            "pending": i.pending,
            "created_at": i.created_at,
            "added_at": i.added_at,
            "done_at": i.done_at,
            "times_added": i.times_added,
            "since_seconds": max(now - anchor, 0),
            "ago": items_logic.humanize_ago(max(now - anchor, 0)),
            "local": local_str(anchor),
        }

    def board_view(now: int) -> dict:
        b = items_logic.board(
            db.list_items(), cfg.web.previous_limit, cfg.web.previous_max_age, now
        )
        return {
            "pending": [item_json(i, now) for i in b["pending"]],
            "previous": [item_json(i, now) for i in b["previous"]],
        }

    def wants_partial(request: Request) -> bool:
        return request.headers.get("HX-Request") == "true"

    def board_partial(request: Request) -> Response:
        now = now_epoch()
        return templates.TemplateResponse(
            request, "partials/board.html", {"board": board_view(now)}
        )

    # --- pages ---------------------------------------------------------------

    @app.get("/")
    def index(request: Request):
        now = now_epoch()
        return templates.TemplateResponse(
            request, "phone/index.html", {"board": board_view(now)}
        )

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "pending": db.count_pending()}

    @app.get("/manifest.webmanifest")
    def manifest():
        return JSONResponse(
            {
                "name": "Shoppa",
                "short_name": "Shoppa",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#12141a",
                "theme_color": "#12141a",
                "icons": [
                    {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
                    {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512",
                     "type": "image/png", "purpose": "maskable"},
                ],
            },
            media_type="application/manifest+json",
        )

    @app.get("/sw.js")
    def service_worker():
        # Served from root so its scope covers the whole app; never cached.
        return FileResponse(
            _HERE / "static" / "sw.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    # --- read APIs -----------------------------------------------------------

    @app.get("/api/items")
    def api_items():
        """Every entry, both lists — and unlike the phone board, the previous list
        isn't trimmed to `previous_limit` here. A consumer that wants the whole
        history shouldn't have to care what fits on a screen."""
        now = now_epoch()
        b = items_logic.board(db.list_items())
        return {
            "now": now,
            "pending": [item_json(i, now) for i in b["pending"]],
            "previous": [item_json(i, now) for i in b["previous"]],
        }

    @app.get("/api/pending")
    def api_pending():
        now = now_epoch()
        b = items_logic.board(db.list_items())
        return {"now": now, "pending": [item_json(i, now) for i in b["pending"]]}

    @app.get("/api/suggest")
    def api_suggest(request: Request, name: str = ""):
        """Previous entries matching a half-typed name — the add form's dropdown.

        Deliberately server-side rather than filtered out of the tiles already on
        the page: the board's previous list is cut to `previous_limit`, and the
        whole point of typing a name is to reach the entry that scrolled off the
        bottom months ago. Ranking and matching are `items.suggest`'s, so the box
        can't disagree with the dedupe key about what counts as the same thing.

        The parameter is `name` so the input can send its own value untouched
        (`hx-params="name"`) instead of htmx having to rewrite it.
        """
        matches = items_logic.suggest(db.list_items(), name, SUGGEST_LIMIT)
        if wants_partial(request):
            return templates.TemplateResponse(
                request, "partials/suggestions.html", {"suggestions": matches}
            )
        now = now_epoch()
        return {"now": now, "suggestions": [item_json(i, now) for i in matches]}

    # --- write APIs ----------------------------------------------------------

    @app.post("/api/items")
    def create_item(request: Request, name: str = Form(...), note: str = Form("")):
        """Add an entry to the pending list.

        An entry that already exists (same name and note, normalised) is revived
        rather than duplicated — see `db.add_item` — so typing something you buy
        every week behaves exactly like tapping it in the previous list.
        """
        name = name.strip()[:MAX_NAME]
        note = note.strip()[:MAX_NOTE]
        if not name:
            return JSONResponse({"error": "name is required"}, status_code=400)

        item = db.add_item(name, note, now_epoch())
        if wants_partial(request):
            return board_partial(request)
        return item_json(item, now_epoch())

    @app.post("/api/items/{item_id}/done")
    def done_item(request: Request, item_id: int):
        """Tick an item off: it leaves the pending list for the previous one.

        No confirmation anywhere in the UI for this — it's one tap to undo from the
        list it lands in, so asking "are you sure?" would cost more than the
        mistake does.
        """
        item = db.set_pending(item_id, False, now_epoch())
        if item is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if wants_partial(request):
            return board_partial(request)
        return item_json(item, now_epoch())

    @app.post("/api/items/{item_id}/repeat")
    def repeat_item(request: Request, item_id: int):
        """Put a previous entry back on the list — the other direction of the tap."""
        item = db.set_pending(item_id, True, now_epoch())
        if item is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if wants_partial(request):
            return board_partial(request)
        return item_json(item, now_epoch())

    @app.patch("/api/items/{item_id}")
    async def patch_item(request: Request, item_id: int):
        """Edit an entry — what the long-press editor saves.

        Accepts either shape: the editor posts a form (htmx sends PATCH bodies
        form-encoded), an API client posts JSON. Every field is optional, so a
        caller can nudge one thing without restating the rest.
        """
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("application/json"):
            raw = await request.json()
        else:
            raw = dict(await request.form())

        existing = db.get_item(item_id)
        if existing is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        touched = False
        if "name" in raw or "note" in raw:
            # Name and note share a dedupe key, so they're rewritten together —
            # sending one alone means "leave the other as it is", not "blank it".
            name = str(raw.get("name", existing.name)).strip()[:MAX_NAME]
            note = str(raw.get("note", existing.note)).strip()[:MAX_NOTE]
            if not name:
                return JSONResponse({"error": "name is required"}, status_code=400)
            try:
                db.rename_item(item_id, name, note)
            except sqlite3.IntegrityError:
                return JSONResponse(
                    {"error": "you already have an entry with that name and note"},
                    status_code=409,
                )
            touched = True

        if "pending" in raw:
            db.set_pending(item_id, _truthy(raw["pending"]), now_epoch())
            touched = True

        if not touched:
            return JSONResponse({"error": "nothing to update"}, status_code=400)

        if wants_partial(request):
            return board_partial(request)
        return item_json(db.get_item(item_id), now_epoch())

    @app.delete("/api/items/{item_id}")
    def delete_item(request: Request, item_id: int):
        """Permanent removal — the long-press menu's other option."""
        ok = db.delete_item(item_id)
        if wants_partial(request):
            return board_partial(request)
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    @app.post("/api/previous/clear")
    def clear_previous(request: Request):
        """Empty the history, leaving whatever is currently on the list alone."""
        removed = db.clear_previous()
        if wants_partial(request):
            return board_partial(request)
        return {"removed": removed}

    return app


def _truthy(value: object) -> bool:
    """Form checkboxes and JSON booleans, read the same way.

    A form can only ever send strings, so `"0"`/`"false"` have to be spelled out —
    `bool("false")` is True, which would make un-ticking impossible.
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def run_serve(config_path: str) -> int:
    """Blocking entrypoint used by `shoppa serve` — wires uvicorn to the app."""
    import uvicorn

    cfg = load_config(config_path)
    app = create_app(config_path)
    uvicorn.run(app, host=cfg.web.host, port=cfg.web.port, log_level="info")
    return 0

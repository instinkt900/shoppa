# CLAUDE.md — Shoppa

Context for working on this repo. Product **display name** is "Shoppa"; the
**code/package/CLI/container** name is `shoppa` (don't rename those).

## Purpose

A small, self-contained phone-first **shopping list**. Pending items sit at the top
as one compact tile each (name + note, "Chicken" / "Breast. 600g"); **tapping** a
tile ticks it off — it isn't deleted, it moves into **Previous entries** below the
add form, and **tapping it there puts it back on the list**. **Long-pressing** any
tile opens a menu offering *Edit* or *Delete permanently*.

Modelled closely on the sibling apps *Naggy* (`~/Development/naggy`) and *Sugar
Daddy* (`~/Development/sugardaddy`) — reuse their idioms.

## Stack

- Python **3.11+**. Web: **FastAPI + Jinja2 + HTMX**. Storage: **SQLite** (stdlib
  `sqlite3`, WAL). Runs under **uvicorn**. Phone UI is HTMX-driven; static assets
  (htmx) vendored in `shoppa/static/`, **no build step**.
- Tests are **plain-assert** files runnable with bare `python` (see
  `tests/test_items.py`), also pytest-compatible.
- Deliberately **no Web Push** — unlike Naggy, nothing here falls due, so there is
  nothing to be notified about. The PWA shell (manifest, service worker, icons) is
  still there, and `navigator.setAppBadge` puts the pending count on the icon
  straight from the page. Don't port `notify.py` over without a reason.

## Layout

```
shoppa/
  cli.py         entrypoint: serve | init-db | list | add
  __main__.py    `python -m shoppa`
  web.py         FastAPI app factory (create_app); all routes as closures; tz helpers
  db.py          SQLite schema + Database wrapper (short-lived connections)
  models.py      dataclass: Item
  items.py       PURE logic: normalize, entry_key, board, suggest, humanize_ago
  config.py      TOML loader; dataclasses; _known() rejects unknown keys
  templates/     base.html, phone/index.html, partials/{board,suggestions}.html
  static/        style.css, phone.js, sw.js, htmx.min.js (vendored), icons/
docker/          Dockerfile, docker-compose.yml
deploy/          install-server.sh
tests/           plain-assert tests (test_items.py pure, test_db.py storage)
config.example.toml   the only tracked config; real config.toml is gitignored
```

## Data model conventions (important)

- **Timestamps are UTC epoch seconds (int) everywhere internally.** Timezone
  conversion happens only in the web layer. Nothing is scheduled, so `[web]
  timezone` is display-only.
- **One row per entry; `pending` is which list it's in.** Ticking off sets
  `pending = 0` and stamps `done_at`; re-adding sets `pending = 1`, stamps
  `added_at` and bumps `times_added`. Nothing but an explicit `DELETE` (or
  `clear_previous`) removes a row — the history *is* the previous-entries list,
  which is what makes it tappable as a catalogue.
- **Identity is (name, note), both normalised** — `items.entry_key`, enforced by
  the `idx_items_entry` UNIQUE index, so dedupe is decided *inside* the upsert
  rather than by a read-then-write two phones could race through. Same name with a
  different note is a **different entry** on purpose ("Chicken / Breast" vs
  "Chicken / Thighs"). Re-adding an existing pair revives that row and updates its
  display text to whatever was typed most recently.
- `db.rename_item` derives the keys itself, so an edit can never leave a row whose
  dedupe key disagrees with its display text. Renaming onto an existing entry
  raises `sqlite3.IntegrityError` → **409** in `web.py`; never silently merge two
  rows, that loses a note.
- **Keep `items.py` pure** (no I/O, no wall clock, no config — `now` is passed in).
  New ordering/matching/phrasing logic goes there and gets covered in `tests/`.
- **Both lists are ordered A–Z** on `items._alpha_key` — the normalised name, then
  the normalised note, then `id` — so display order agrees with the dedupe key and
  is total. Alphabetical because both lists are read by *hunting* for a name; a
  recency order reshuffles under the thumb on every tap. The history's two cuts
  are about **use**, not names, and both run *before* the sort — `previous_max_age`
  (seconds; drops entries whose `_last_used` is older, and `board` raises if it's
  given without `now`, since items.py owns no clock) then `previous_limit` (default
  10; keeps the N with the highest `times_added`, most recently used first among
  equals — so one-offs only fill slots the regulars leave free). Sorting decides where a row sits,
  never whether it's shown; cutting the alphabet would amputate the history past
  whatever letter the limit reached. Both only hide rows — nothing there deletes.
  The shopping list is never trimmed by either.
- `items.suggest(items, query, limit)` backs the add form's dropdown: previous
  entries only (a pending one is already a visible tile), matched on the
  normalised name, prefix hits ranked above substring hits, A–Z within each rank,
  empty query → nothing.
- Column additions stay guarded by `_has_column`; **row rewrites need a
  `PRAGMA user_version` bump** (`_USER_VERSION` in `db.py`) since they leave no
  trace to detect. `_migrate_data` is empty scaffolding at v1 — that's deliberate.

## Web routes (all in `web.py`)

- Pages: `GET /`, `GET /healthz`, `GET /manifest.webmanifest`, `GET /sw.js`
- Read APIs: `GET /api/items` (both lists, history **untrimmed**),
  `GET /api/pending`, `GET /api/suggest?name=…` (add-form dropdown; returns
  `partials/suggestions.html` to HTMX, JSON otherwise)
- Write APIs: `POST /api/items`, `POST /api/items/{id}/done`,
  `POST /api/items/{id}/repeat`, `PATCH /api/items/{id}`, `DELETE /api/items/{id}`,
  `POST /api/previous/clear`
- HTMX form posts return the re-rendered `partials/board.html` fragment (swap
  target `#board`); the same endpoints answer JSON when `HX-Request` is absent.
- `PATCH` takes a form body (what the editor sends — htmx form-encodes PATCH) or a
  JSON one, whichever the `Content-Type` says. `name` and `note` share a dedupe key
  so they're rewritten **together**: sending one alone means "leave the other as it
  is", not "blank it".
- **Only deletion confirms.** Ticking off and re-adding are one tap each with no
  dialog — each is undone by one tap in the other list, so a prompt would cost more
  than the mistake. Delete (and `clear`) can't be undone, so those do ask.
- The add form sits **between** the pending and previous lists, but stays outside
  the swapped fragment: `#board` is `display: contents` so its two sections become
  flex items of `main` and CSS `order` interleaves the form between them. Moving
  the form into `partials/board.html` instead would look identical and then throw
  away half-typed input on every tap — don't. Any new direct child of `main` needs
  an explicit `order` (the default 0 sorts before all of them).
- The long-press menu and the editor are **two `<dialog>`s** in `phone/index.html`,
  outside `#board` so a swap can't yank them away mid-edit. A long press fills them
  from the pressed tile's `data-*` attributes (no round trip) and `phone.js` fires
  the PATCH/DELETE through `htmx.ajax`. Neither can use `hx-patch`/`hx-delete`:
  htmx bakes the URL in when it processes the element, and the item id isn't known
  until a tile is pressed.
- The long press sets `suppressClick`, and a **capture-phase** click handler eats
  the release — without it a long press would also tick the item off.
- The name field's suggestion box is HTMX (`hx-get="/api/suggest"`,
  `hx-params="name"`, `hx-target="#suggestions"`), so the filtering lives in
  `items.suggest` and not in JS. It's **server-side on purpose**: the board's
  previous list is cut to `previous_limit`, and the point of typing a name is to
  reach the entry that scrolled off months ago. `#suggestions` is absolutely
  positioned against `#add-form` so the box hangs *below* the note field and Add
  button and over the previous list — it must cover history you can spare, never
  the two controls you may want next, and it must not shove them around mid-type
  by taking up flow. Picking a row
  fills **both** fields (a suggestion is a whole entry; a stale note would build a
  third row out of two that already exist) and doesn't auto-submit — the quantity
  usually wants a nudge first. Dismissal is `pointerdown` anywhere but the name
  field and `#suggestions`, never `blur`: on Android the tap that picks a row
  blurs the input first, and the box would go before the click landed.
- **The add form's `hx-on::after-request` must test `event.target === this`.**
  htmx events bubble, and the name field fires its own request on every keystroke,
  so without the guard each one reaches the form's handler as a successful request
  and `this.reset()` wipes the field mid-word. Any handler on a container that
  holds another hx- element needs the same check.
- `sw.js` is **network-first**, so code/template/static changes roll out on reload.

## Commands

```
shoppa serve   -c config.toml
shoppa init-db -c config.toml
shoppa list    -c config.toml [--json] [--all]
shoppa add     -c config.toml NAME [NOTE]
```

## Configuration

One TOML (`config.toml`; template `config.example.toml`). Sections `[database]`
(path) and `[web]` (host/port/timezone/previous_limit/previous_max_age_days —
`WebConfig.previous_max_age` is a *property* converting the days to seconds or
None, deliberately not a field so `_known()` still rejects it in the file). `config.py` uses
dataclasses + `_known()` so an **unknown key/section fails loudly**. There are no
secrets today; if one is ever needed it comes from the **environment only**, and
since it wouldn't be a field of any section `_known()` rejects it automatically if
someone pastes it into the file.

## Deployment

Docker; code is **baked in via COPY**, so any code/template/static change needs an
**image rebuild** (restart alone won't pick it up). Compose project/container/image
are `shoppa`; container listens on **8080 internally**, host port
`${SHOPPA_PORT:-8091}` — chosen to clear Sugar Daddy (8080) and Naggy (8090) on the
same host. Data is a named volume `shoppa-data` at `/data`; `docker/config.toml`
bind-mounted read-only (set `database.path = "/data/shoppa.db"`).
Deploy: `git pull --ff-only && cd docker && docker compose up -d --build`
(wrapped by `deploy/install-server.sh`). Verify: `curl :<port>/healthz`.

## Goals / roadmap (designed for, not yet built)

- **Categories / aisle order** — sorting the list the way the supermarket is laid
  out. Would be a guarded `ALTER TABLE` for a `category` column plus a sort key in
  `items.board` — category first, then the existing `_alpha_key` within each.
- **Quantities** — the note field carries them informally today ("600g"). A real
  quantity column would want its own tile slot rather than more note text.
- **Sharing between phones** — there's no auth or per-user data; today it's one
  list served on a private network.

## Conventions & guardrails

- **Match the sibling apps' style**: docstrings explain the *why*;
  `from __future__ import annotations` + `X | None`; app factory of closures;
  short-lived DB connections; per-model `*_json()` serializers.
- Keep `items.py` pure and covered by `tests/`.
- Tiles are **one line**: name, note, then age. Anything new competing for that
  row has to earn it — the list is read at arm's length in an aisle.
- Secrets/data are gitignored (`config.toml`, `*.db`, `docker/.env`); only
  `*.example` files are tracked.
- Icons (`static/icons/`, green square + white basket) are generated by
  `tools/make_icons.py` (Pillow); re-run it to change them. Bump `__version__`
  when static assets change — it names the SW cache and stamps every asset URL,
  so clients pick them up. Regeneration rewrites PNG metadata even when the
  pixels are unchanged; `git checkout` the icons you didn't mean to touch.
- Personal project; deploy when the user asks.

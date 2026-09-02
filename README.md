# Shoppa

A small, self-contained **phone-first shopping list**. Whatever you need is at the
top of the screen, one tile per item — a name and a short note, "Chicken" /
"Breast. 600g". **Tap a tile** and it's ticked off: not deleted, just moved down
into **Previous entries**, where **tapping it again** puts it straight back on the
list. Buying the same twelve things every week costs twelve taps and no typing.

**Long-press** any tile to edit it or delete it for good.

Built in the same mold as its sibling apps *Sugar Daddy* and *Naggy*: FastAPI +
Jinja2 + HTMX, SQLite, a network-first PWA, no frontend build step.

## Stack

- Python **3.11+**. Web: **FastAPI + Jinja2 + HTMX**. Storage: **SQLite** (stdlib
  `sqlite3`, WAL). Runs under **uvicorn**.
- The phone UI is HTMX-driven — every mutation swaps a single re-rendered board
  partial. Static assets (htmx) are **vendored**; there is no build step.
- Tests are **plain-assert** files runnable with bare `python`.

## Quick start

```bash
cd ~/Development/shoppa
python -m venv .venv && . .venv/bin/activate
pip install -e .

cp config.example.toml config.toml     # edit timezone if you like
python -m shoppa init-db -c config.toml
python -m shoppa serve   -c config.toml  # open http://localhost:8080/
```

Type a name (and optionally a note) into the form between the two lists and hit
**Add** — or start typing a name and tap one of the previous entries it suggests,
which fills the note in too. The item files itself alphabetically into the
shopping list; tap it when it's in the trolley and it drops into Previous entries.
Next week, tap it there to put it back.

## Commands

```
shoppa serve   -c config.toml                # run the web app (uvicorn)
shoppa init-db -c config.toml                # create schema and exit
shoppa list    -c config.toml [--json] [--all]  # print the list and the history
shoppa add     -c config.toml NAME [NOTE]    # add without opening the app
```

## How the two lists work

There is **one database row per entry**, and a `pending` flag decides which list
it's in. Ticking something off never deletes it — that's the whole point: the
previous-entries list is a catalogue of everything you've ever bought, and it's
what the add form's suggestions search. Only the long-press **Delete** removes a
row, and `clear` under the Previous
entries heading empties the history while leaving the current list alone.

An entry is identified by its **name *and* its note**, both normalised (case,
spacing and accents folded — see `shoppa/items.py`). So:

- Adding "chicken / breast. 600g" when you already have "Chicken / Breast. 600g"
  **revives that entry** instead of duplicating it — typing it out behaves exactly
  like tapping it in the previous list, and the counter on the tile ticks up.
- "Chicken / Breast. 600g" and "Chicken / Thighs. 1kg" are **two entries**, because
  they're two things you might want on the list at once.

Ordering is deliberate and lives in `shoppa/items.py` (pure, unit-tested). **Both
lists are A–Z**, on the normalised name and then the note, so the same fold that
decides identity also decides where a tile sits. Both lists are read by hunting
for a name — "is bread on here?" in the aisle — and only a fixed order lets you
find one without reading every tile; a recency order reshuffles itself under your
thumb on every tap.

The history is **cut by time** before it's sorted, never by letter — trimming the
alphabet would silently amputate it at whatever letter the limit reached. Two
settings do the cutting, and both only hide rows:

- `[web] previous_max_age_days` drops entries you haven't bought in that long.
  This is the one that means "old": a thing last bought two years ago isn't a
  catalogue entry any more, however few entries sit above it. `0` (the default)
  turns it off.
- `[web] previous_limit` then caps what's left to the N most recently used, so a
  screen can't be buried whatever the age cutoff is.

Nothing is deleted by either. `GET /api/items`, `shoppa list --all` and the add
form's name suggestions all still see the whole history — only the long-press
**Delete** and `clear` remove rows.

## Name suggestions

Typing into the name field searches the history and drops matching **whole
entries** under it — name *and* note, because the note is the half you don't
remember and the half that decides whether the entry you already have gets revived
or a near-duplicate gets created. Tap one to fill both fields, then edit the
quantity before you hit Add.

Matching ignores case, spacing and accents, exactly like the dedupe key does.
Names that *start* with what you typed rank above names that merely contain it, so
"ap" offers Apples before Pineapple. Things already on the shopping list aren't
offered — they're a tile you can already see. The search runs server-side against
the **whole** history (`GET /api/suggest?name=…`), not just the previous entries
that fit on the screen.

Timestamps are UTC epoch seconds everywhere internally; timezone conversion
happens only in the web layer. Nothing here is scheduled, so `[web] timezone` only
decides how the ages and dates read.

## Web routes

- Pages: `GET /`, `GET /healthz`, `GET /manifest.webmanifest`, `GET /sw.js`
- Read APIs: `GET /api/items`, `GET /api/pending`, `GET /api/suggest?name=…`
- Write APIs: `POST /api/items`, `POST /api/items/{id}/done`,
  `POST /api/items/{id}/repeat`, `PATCH /api/items/{id}`, `DELETE /api/items/{id}`,
  `POST /api/previous/clear`

HTMX form posts get back the re-rendered board fragment; the same endpoints answer
JSON when the request isn't from HTMX. `PATCH` accepts either a form body or JSON
and every field on it is optional — editing a name into an entry that already
exists is a **409** rather than a silent merge.

## Deployment (Docker)

```bash
cd docker
cp ../config.example.toml config.toml   # set database.path = "/data/shoppa.db"
docker compose up -d --build            # host port ${SHOPPA_PORT:-8091} -> 8080
curl localhost:8091/healthz
```

Code is baked into the image, so a code/template/static change needs a rebuild.
`deploy/install-server.sh` wraps `git pull --ff-only && docker compose up -d --build`.
The default host port (8091) steps clear of its siblings on the same box — Sugar
Daddy on 8080, Naggy on 8090.

To install it on a phone, open the site and use Chrome ⋮ → *Add to Home screen*.
That needs **HTTPS**, so serve it behind a reverse proxy with a real certificate;
it works as a plain browser tab either way, just without the icon and the offline
cache.

## Tests

```bash
python tests/test_items.py    # pure list logic: identity, ordering, ages
python tests/test_db.py       # storage: dedupe, moving between the lists
```

Both are plain-assert files — no test runner required — and are pytest-compatible
if you'd rather `pytest tests/`.

## Notes

- The PWA icons (`shoppa/static/icons/`) — a green square with a white shopping
  basket — are generated by `python tools/make_icons.py` (needs `pip install
  pillow`). Edit that script and re-run to change the artwork.
- `config.toml`, `*.db`, and `docker/.env` are gitignored; only `*.example` files
  are tracked.

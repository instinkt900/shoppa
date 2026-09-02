"""PURE list logic — no I/O, no wall clock, no config (`now` is passed in).

Everything Shoppa decides for itself lives here so it can be unit-tested with a
bare interpreter: how two entries are judged to be the same thing, how the two
lists are ordered, which previous entries a half-typed name suggests, and how an
age reads. `db.py` calls `entry_key` when it writes and `web.py` calls
`board`/`suggest`/`humanize_ago` when it renders; neither reimplements any of it.
"""

from __future__ import annotations

import re
import unicodedata

from shoppa.models import Item

_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Fold a name or note down to a comparison key.

    Case, surrounding and repeated whitespace, and Unicode composition are all
    noise when deciding whether "  chicken " is the thing you already have as
    "Chicken" — so they're stripped. NFKD-then-drop-combining also means an entry
    typed "creme fraiche" matches one typed "crème fraîche"; on a phone keyboard
    that's much more likely to be the same item than two different ones.
    """
    folded = unicodedata.normalize("NFKD", text.strip().casefold())
    stripped = "".join(c for c in folded if not unicodedata.combining(c))
    return _SPACES.sub(" ", stripped)


def entry_key(name: str, note: str) -> tuple[str, str]:
    """The identity of an entry: its name *and* its note, both normalised.

    Deliberately not the name alone. "Chicken / Breast. 600g" and
    "Chicken / Thighs. 1kg" are two things you might want on the list at once and
    two things worth keeping side by side in the history, so they get a row each.
    Re-adding the exact same pair, though, revives the row it already has instead
    of stacking up a second copy — which is the case that actually happens every
    week.
    """
    return normalize(name), normalize(note)


def board(
    items: list[Item],
    previous_limit: int | None = None,
    max_age: int | None = None,
    now: int | None = None,
) -> dict:
    """Split the rows into the two lists the phone shows, both sorted A-Z.

    Alphabetical, not chronological: both lists are read by *hunting* — "is bread
    on here?" in the aisle, "where's the flour?" when tapping the week's shop back
    on — and only a stable order lets you find a name without reading every tile.
    A recency order re-shuffles itself under your thumb on every tap, which is the
    one thing a list you're scanning must not do.

    The sort key is the *normalised* name then note, so case and accents don't
    scatter related tiles ("Crème fraîche" files under c, next to "Chicken"), and
    the two "Chicken" entries sit together ordered by their notes. `id` breaks the
    last tie so the order is total and never depends on the row order SQL happens
    to return.

    Two cuts trim the history before any of that, and **both are about time, not
    about names** — sorting decides where an entry sits, never whether it's shown.
    Trimming the alphabet instead would silently amputate the history past
    whatever letter the limit reached.

    - `max_age` (seconds; needs `now`) drops entries not used in that long. This
      is the honest one: a thing you last bought two years ago isn't a catalogue
      entry any more, however few entries happen to sit above it.
    - `previous_limit` then caps what's left to the N most recently used, so a
      screen can't be buried whatever the age cutoff is set to.

    Either can be None for "no cut". Both only hide rows — nothing here deletes
    anything, and `GET /api/items` still returns the lot.

    Pending is never trimmed by either: a shopping list you can't see all of is
    worse than a long one, and an item you put on the list *is* current by
    definition however long it has sat there.
    """
    if max_age is not None and now is None:
        # Better a loud error than silently keeping everything: this module owns
        # no clock on purpose, so an age cut it can't measure is a caller's bug.
        raise ValueError("board(max_age=...) needs `now` — items.py has no clock")

    pending = sorted((i for i in items if i.pending), key=_alpha_key)
    previous = sorted(
        (i for i in items if not i.pending),
        # `done_at` is when a row left the pending list, i.e. when it was last
        # actually used — which is what both cuts and the recency sort mean by
        # "old". It's only None for rows that got here some other way (a direct
        # API PATCH, say); fall back to created_at rather than dropping them.
        # `id` breaks a tie so the count trim keeps the same rows every time.
        key=lambda i: (_last_used(i), i.id or 0),
        reverse=True,
    )
    if max_age is not None:
        previous = [i for i in previous if now - _last_used(i) <= max_age]
    if previous_limit is not None:
        previous = previous[:previous_limit]
    return {"pending": pending, "previous": sorted(previous, key=_alpha_key)}


def _last_used(item: Item) -> int:
    """When an entry was last in the trolley — the stamp both history cuts read."""
    return item.done_at or item.created_at


def _alpha_key(item: Item) -> tuple[str, str, int]:
    """Sort key for a tile: normalised name, then note, then id.

    Normalised so display order agrees with the identity the rest of the app
    uses — two tiles that would dedupe against each other can never sort apart.
    """
    return (*entry_key(item.name, item.note), item.id or 0)


def suggest(items: list[Item], query: str, limit: int = 6) -> list[Item]:
    """Previous entries whose name matches a half-typed one, best match first.

    The point of the box under the name field is to type "chi" and be handed back
    the *note* as well — "Chicken / Breast. 600g" — because the note is the half
    you don't remember and the half that decides whether the entry you already
    have gets revived or a near-duplicate gets created. So a hit is a whole row,
    not a name.

    Only previous entries are offered. Something already on the list isn't a thing
    you usefully add, and suggesting it would drop the one tile you can already
    see into the box you're typing into.

    Matching is on the normalised name, so it ignores case, spacing and accents
    exactly like the dedupe key does. Names that *start* with what's typed rank
    above names that merely contain it — typing "ap" wants Apples before
    Pineapple — and within each group it's the same A-Z order as the lists, so the
    box doesn't reshuffle itself as you type another letter.
    """
    q = normalize(query)
    if not q:
        return []
    hits = []
    for i in items:
        if i.pending:
            continue
        name = normalize(i.name)
        if name.startswith(q):
            hits.append((0, i))
        elif q in name:
            hits.append((1, i))
    hits.sort(key=lambda pair: (pair[0], _alpha_key(pair[1])))
    return [i for _, i in hits[:limit]]


def humanize_ago(seconds: int) -> str:
    """Render an age in seconds as a short phrase for a tile ("6d", "3w").

    Abbreviated on purpose: it sits at the right-hand end of a previous entry's
    single line, next to the note, and has to earn very little space. Anything
    under an hour is "now" — the exact minute you ticked something off is never
    the point.
    """
    if seconds < 3_600:
        return "now"
    if seconds < 86_400:
        return f"{seconds // 3_600}h"
    if seconds < 604_800:
        return f"{seconds // 86_400}d"
    if seconds < 2_592_000:
        return f"{seconds // 604_800}w"
    if seconds < 31_536_000:
        return f"{seconds // 2_592_000}mo"
    return f"{seconds // 31_536_000}y"

"""PURE list logic — no I/O, no wall clock, no config (`now` is passed in).

Everything Shoppa decides for itself lives here so it can be unit-tested with a
bare interpreter: how two entries are judged to be the same thing, how the two
lists are ordered, and how an age reads. `db.py` calls `entry_key` when it writes
and `web.py` calls `board`/`humanize_ago` when it renders; neither reimplements
any of it.
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


def board(items: list[Item], previous_limit: int | None = None) -> dict:
    """Split the rows into the two lists the phone shows, each in its own order.

    Pending is **newest first**: what you just typed lands at the top of the
    screen, where you're already looking, and the list reads as a stack of what's
    left. Previous is **most recently ticked off first**, so the things you buy
    often stay within thumb reach of the form and the one-offs sink — that list is
    a catalogue to tap from, not a log to read.

    `previous_limit` trims only the previous list (None = no limit); pending is
    never truncated, since a shopping list you can't see all of is worse than a
    long one.
    """
    pending = sorted(
        (i for i in items if i.pending),
        key=lambda i: (i.added_at, i.id or 0),
        reverse=True,
    )
    previous = sorted(
        (i for i in items if not i.pending),
        # `done_at` is set whenever a row leaves the pending list, so it's only
        # None for rows that got there some other way (a direct API PATCH, say).
        # Fall back to created_at rather than dropping them off the end.
        key=lambda i: (i.done_at or i.created_at, i.id or 0),
        reverse=True,
    )
    if previous_limit is not None:
        previous = previous[:previous_limit]
    return {"pending": pending, "previous": previous}


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

"""Row dataclasses shared across the db, web, and items layers.

Plain dataclasses (no pydantic). Required fields first, `id: int | None = None`
last so a row can be built in memory before it is persisted. All timestamps are
UTC epoch seconds (int); display conversion happens only at the web edge.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Item:
    """One shopping entry — a name and its note, e.g. "Chicken" / "Breast. 600g".

    There is exactly **one row per entry**, and `pending` is which of the two
    lists it currently sits in. Ticking an item off doesn't delete it, it just
    flips `pending` to 0, which is what makes the previous-entries list a catalogue
    you can tap to shop from again rather than a graveyard. Only an explicit delete
    removes the row.
    """

    name: str
    note: str = ""
    pending: bool = True
    created_at: int = 0
    # When it last entered the pending list — what the pending list sorts on.
    added_at: int = 0
    # When it last left it. NULL for an entry that has never been ticked off; the
    # previous list sorts on this, so it's only ever read for pending == 0 rows.
    done_at: int | None = None
    # How many times it has been put on the list. A cheap "you buy this a lot"
    # signal, shown on the tile once it's more than one.
    times_added: int = 1
    id: int | None = None

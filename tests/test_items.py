"""Plain-assert tests for the pure list logic.

Runnable with a bare interpreter (`python tests/test_items.py`) and also
pytest-compatible. No fixtures/mocks: `items.*` takes everything it needs as
arguments, so we just build `Item`s with pinned timestamps.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shoppa import items
from shoppa.models import Item

HOUR = 3_600
DAY = 86_400
WEEK = 604_800

NOW = 1_800_000_000


def _item(name, note="", *, pending=True, added=NOW, done=None, id=1, created=None):
    return Item(
        name=name, note=note, pending=pending,
        created_at=created if created is not None else added,
        added_at=added, done_at=done, id=id,
    )


# --- normalisation / identity -------------------------------------------------


def test_normalize_folds_case_and_whitespace():
    assert items.normalize("  Chicken  ") == "chicken"
    assert items.normalize("Spring   Onions") == "spring onions"
    assert items.normalize("CHICKEN") == items.normalize("chicken")


def test_normalize_ignores_accents():
    # Typed with and without accents on a phone keyboard: the same thing.
    assert items.normalize("crème fraîche") == items.normalize("creme fraiche")


def test_entry_key_covers_name_and_note():
    # Same name, different note -> different entries. You can want both at once.
    assert items.entry_key("Chicken", "Breast. 600g") != items.entry_key("Chicken", "Thighs. 1kg")
    # Same pair, typed sloppily -> the same entry, so re-adding revives it.
    assert items.entry_key(" chicken ", "BREAST. 600g") == items.entry_key("Chicken", "Breast. 600g")


def test_entry_key_distinguishes_note_from_no_note():
    assert items.entry_key("Milk", "") != items.entry_key("Milk", "2L")


# --- board split and ordering -------------------------------------------------


def test_board_splits_on_pending():
    rows = [
        _item("Chicken", pending=True, id=1),
        _item("Milk", pending=False, done=NOW, id=2),
    ]
    b = items.board(rows)
    assert [i.name for i in b["pending"]] == ["Chicken"]
    assert [i.name for i in b["previous"]] == ["Milk"]


def test_pending_is_newest_first():
    # What you just typed lands at the top of the screen.
    rows = [
        _item("Old", added=NOW - 2 * DAY, id=1),
        _item("New", added=NOW, id=2),
        _item("Middle", added=NOW - DAY, id=3),
    ]
    assert [i.name for i in items.board(rows)["pending"]] == ["New", "Middle", "Old"]


def test_previous_is_most_recently_ticked_off_first():
    # The things you buy often stay within thumb reach of the form.
    rows = [
        _item("Bread", pending=False, done=NOW - WEEK, id=1),
        _item("Milk", pending=False, done=NOW - HOUR, id=2),
        _item("Rice", pending=False, done=NOW - DAY, id=3),
    ]
    assert [i.name for i in items.board(rows)["previous"]] == ["Milk", "Rice", "Bread"]


def test_previous_without_done_at_falls_back_to_created():
    # A row can only get here via the API (a PATCH that sets pending=0 directly
    # always stamps done_at). It must still sort somewhere sane, not vanish.
    rows = [
        _item("Ghost", pending=False, done=None, created=NOW - DAY, id=1),
        _item("Milk", pending=False, done=NOW - HOUR, id=2),
    ]
    assert [i.name for i in items.board(rows)["previous"]] == ["Milk", "Ghost"]


def test_previous_limit_trims_only_history():
    rows = [_item(f"P{n}", added=NOW - n, id=n) for n in range(5)]
    rows += [_item(f"H{n}", pending=False, done=NOW - n, id=100 + n) for n in range(5)]
    b = items.board(rows, previous_limit=2)
    assert len(b["previous"]) == 2
    # The list itself is never truncated — a shopping list you can't see all of
    # is worse than a long one.
    assert len(b["pending"]) == 5


def test_previous_limit_zero_hides_history_entirely():
    rows = [_item("Milk", pending=False, done=NOW, id=1)]
    assert items.board(rows, previous_limit=0)["previous"] == []


def test_ties_break_on_id_not_arbitrarily():
    # Two things added in the same second (tap a previous entry, tap another)
    # must still come out in a stable, newest-last-inserted-first order.
    rows = [_item("First", added=NOW, id=1), _item("Second", added=NOW, id=2)]
    assert [i.name for i in items.board(rows)["pending"]] == ["Second", "First"]
    assert [i.name for i in items.board(list(reversed(rows)))["pending"]] == ["Second", "First"]


# --- age phrasing -------------------------------------------------------------


def test_humanize_ago_units():
    assert items.humanize_ago(0) == "now"
    assert items.humanize_ago(59 * 60) == "now"
    assert items.humanize_ago(3 * HOUR) == "3h"
    assert items.humanize_ago(2 * DAY) == "2d"
    assert items.humanize_ago(3 * WEEK) == "3w"
    assert items.humanize_ago(60 * DAY) == "2mo"
    assert items.humanize_ago(400 * DAY) == "1y"


def test_humanize_ago_stays_short():
    # It shares one line with the name and the note, so it has to earn its space.
    for seconds in range(0, 400 * DAY, DAY // 3):
        assert len(items.humanize_ago(seconds)) <= 4, (seconds, items.humanize_ago(seconds))


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()

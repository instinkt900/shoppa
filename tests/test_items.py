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


def _item(name, note="", *, pending=True, added=NOW, done=None, id=1, created=None, times=1):
    return Item(
        name=name, note=note, pending=pending,
        created_at=created if created is not None else added,
        added_at=added, done_at=done, times_added=times, id=id,
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


def test_pending_is_alphabetical():
    # You read this list hunting for a name, so the name is what orders it.
    rows = [
        _item("Rice", added=NOW, id=1),
        _item("Apples", added=NOW - 2 * DAY, id=2),
        _item("Milk", added=NOW - DAY, id=3),
    ]
    assert [i.name for i in items.board(rows)["pending"]] == ["Apples", "Milk", "Rice"]


def test_pending_order_ignores_when_it_was_added():
    # The whole point: adding something must not reshuffle what's above it.
    rows = [_item("Apples", added=NOW - WEEK, id=1), _item("Bread", added=NOW, id=2)]
    assert [i.name for i in items.board(rows)["pending"]] == ["Apples", "Bread"]


def test_sorting_folds_case_and_accents_like_the_dedupe_key():
    # Sorting on the raw string would file every capital ahead of every
    # lower-case name, and "Éclairs" past Z.
    rows = [
        _item("banana", id=1),
        _item("Apples", id=2),
        _item("Éclairs", id=3),
        _item("Cream", id=4),
    ]
    assert [i.name for i in items.board(rows)["pending"]] == [
        "Apples", "banana", "Cream", "Éclairs",
    ]


def test_same_name_sorts_by_note():
    # "Chicken / Breast" and "Chicken / Thighs" are two entries; they belong
    # together, in a fixed order.
    rows = [
        _item("Chicken", "Thighs. 1kg", id=1),
        _item("Chicken", "Breast. 600g", id=2),
        _item("Chicken", "", id=3),
    ]
    assert [i.note for i in items.board(rows)["pending"]] == ["", "Breast. 600g", "Thighs. 1kg"]


def test_previous_is_alphabetical_too():
    rows = [
        _item("Rice", pending=False, done=NOW - HOUR, id=1),
        _item("Bread", pending=False, done=NOW - WEEK, id=2),
        _item("Milk", pending=False, done=NOW - DAY, id=3),
    ]
    assert [i.name for i in items.board(rows)["previous"]] == ["Bread", "Milk", "Rice"]


def test_previous_without_done_at_falls_back_to_created():
    # A row can only get here via the API (a PATCH that sets pending=0 directly
    # always stamps done_at). Display is alphabetical either way; what done_at
    # still decides is the recency trim, so check it there — with a limit of 1 the
    # older row is the one cut, rather than the stampless row being treated as
    # infinitely old and cut regardless of when it was created.
    rows = [
        _item("Ghost", pending=False, done=None, created=NOW - HOUR, id=1),
        _item("Milk", pending=False, done=NOW - DAY, id=2),
    ]
    assert [i.name for i in items.board(rows, previous_limit=1)["previous"]] == ["Ghost"]


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
    # Two rows that sort equal (the UNIQUE index means they can't be an exact
    # pair, but a PATCH-built oddity could be) must not come out in whatever
    # order SQL happened to hand them over in.
    rows = [_item("Milk", added=NOW, id=1), _item("Milk", added=NOW, id=2)]
    assert [i.id for i in items.board(rows)["pending"]] == [1, 2]
    assert [i.id for i in items.board(list(reversed(rows)))["pending"]] == [1, 2]


def test_previous_limit_cuts_by_recency_then_sorts():
    # The two questions are separate: *which* entries are still worth a screen is
    # about time, *where* they go is about names. Cutting alphabetically would
    # amputate the history at whatever letter the limit reached — here it would
    # keep Apples and Bread and lose Milk, which was ticked off an hour ago.
    rows = [
        _item("Apples", pending=False, done=NOW - 40 * DAY, id=1),
        _item("Milk", pending=False, done=NOW - HOUR, id=2),
        _item("Bread", pending=False, done=NOW - 30 * DAY, id=3),
        _item("Rice", pending=False, done=NOW - DAY, id=4),
    ]
    # Milk and Rice survive the cut; A-Z decides how they're then shown.
    assert [i.name for i in items.board(rows, previous_limit=2)["previous"]] == ["Milk", "Rice"]


def test_previous_limit_keeps_the_most_used_before_the_most_recent():
    # The regulars are the point of the list: Bread, bought every week but
    # skipped this week, must outrank a one-off ticked off an hour ago. One-offs
    # only fill what's left, most recent first.
    rows = [
        _item("Bread", pending=False, done=NOW - 30 * DAY, times=12, id=1),
        _item("Candles", pending=False, done=NOW - HOUR, id=2),
        _item("Milk", pending=False, done=NOW - 40 * DAY, times=3, id=3),
        _item("Glue", pending=False, done=NOW - DAY, id=4),
        _item("Tape", pending=False, done=NOW - 2 * DAY, id=5),
    ]
    assert [i.name for i in items.board(rows, previous_limit=3)["previous"]] == ["Bread", "Candles", "Milk"]
    assert [i.name for i in items.board(rows, previous_limit=4)["previous"]] == ["Bread", "Candles", "Glue", "Milk"]


def test_previous_limit_ranks_by_count_not_just_more_than_once():
    rows = [
        _item("Eggs", pending=False, done=NOW - HOUR, times=2, id=1),
        _item("Milk", pending=False, done=NOW - 9 * DAY, times=20, id=2),
    ]
    assert [i.name for i in items.board(rows, previous_limit=1)["previous"]] == ["Milk"]


def test_previous_max_age_drops_things_not_used_in_a_while():
    # The cut that actually means "old": a thing last bought a year ago isn't a
    # catalogue entry any more, however few entries sit above it.
    rows = [
        _item("Milk", pending=False, done=NOW - DAY, id=1),
        _item("Sparklers", pending=False, done=NOW - 300 * DAY, id=2),
    ]
    b = items.board(rows, max_age=180 * DAY, now=NOW)
    assert [i.name for i in b["previous"]] == ["Milk"]


def test_previous_max_age_keeps_an_entry_right_on_the_boundary():
    rows = [_item("Milk", pending=False, done=NOW - 180 * DAY, id=1)]
    assert len(items.board(rows, max_age=180 * DAY, now=NOW)["previous"]) == 1


def test_previous_max_age_never_touches_the_shopping_list():
    # Something you put on the list is current by definition, however long it has
    # sat there unticked.
    rows = [_item("Milk", added=NOW - 300 * DAY, id=1)]
    assert [i.name for i in items.board(rows, max_age=DAY, now=NOW)["pending"]] == ["Milk"]


def test_previous_max_age_and_limit_apply_together():
    # Age first, then the count cap on what survived it.
    rows = [_item(f"Old{n}", pending=False, done=NOW - 300 * DAY, id=n) for n in range(5)]
    rows += [_item(f"New{n}", pending=False, done=NOW - n * DAY, id=100 + n) for n in range(4)]
    b = items.board(rows, previous_limit=2, max_age=180 * DAY, now=NOW)
    assert [i.name for i in b["previous"]] == ["New0", "New1"]


def test_no_max_age_means_no_age_cut():
    # The default, so upgrading doesn't silently shrink an existing history.
    rows = [_item("Sparklers", pending=False, done=NOW - 3000 * DAY, id=1)]
    assert len(items.board(rows)["previous"]) == 1


def test_max_age_without_now_is_an_error_not_a_silent_no_op():
    # items.py owns no clock on purpose; an age cut it can't measure is a bug in
    # the caller, and quietly keeping everything would hide it.
    try:
        items.board([], max_age=DAY)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


# --- suggestions --------------------------------------------------------------


def _previous(name, note="", id=1):
    return _item(name, note, pending=False, done=NOW, id=id)


def test_suggest_matches_on_name():
    rows = [_previous("Chicken", "Breast. 600g", 1), _previous("Rice", "", 2)]
    assert [i.name for i in items.suggest(rows, "chi")] == ["Chicken"]


def test_suggest_returns_whole_entries_so_the_note_comes_too():
    # The note is the half you don't remember, and the half that decides whether
    # the entry you already have is revived or a near-duplicate is created.
    rows = [_previous("Chicken", "Breast. 600g", 1), _previous("Chicken", "Thighs. 1kg", 2)]
    hits = items.suggest(rows, "chicken")
    assert [(i.name, i.note) for i in hits] == [
        ("Chicken", "Breast. 600g"), ("Chicken", "Thighs. 1kg"),
    ]


def test_suggest_ignores_case_spacing_and_accents():
    rows = [_previous("Crème fraîche", "", 1)]
    assert items.suggest(rows, "  CREME  ")[0].name == "Crème fraîche"


def test_suggest_prefers_prefix_matches():
    # Typing "ap" wants Apples before Pineapple.
    rows = [_previous("Pineapple", "", 1), _previous("Apples", "", 2)]
    assert [i.name for i in items.suggest(rows, "ap")] == ["Apples", "Pineapple"]


def test_suggest_is_alphabetical_within_a_rank():
    # So the box doesn't reshuffle itself as you type another letter.
    rows = [_previous("Beans", "", 1), _previous("Bread", "", 2), _previous("Bacon", "", 3)]
    assert [i.name for i in items.suggest(rows, "b")] == ["Bacon", "Beans", "Bread"]


def test_suggest_skips_things_already_on_the_list():
    # It's already a tile on the screen; offering it as something to add is noise.
    rows = [_item("Milk", pending=True, id=1), _previous("Milk", "2L", 2)]
    assert [i.note for i in items.suggest(rows, "milk")] == ["2L"]


def test_suggest_on_empty_query_offers_nothing():
    # An empty field means the box isn't open, not that the whole history is.
    rows = [_previous("Milk", "", 1)]
    assert items.suggest(rows, "") == []
    assert items.suggest(rows, "   ") == []


def test_suggest_honours_its_limit():
    rows = [_previous(f"Bread {n}", "", n) for n in range(20)]
    assert len(items.suggest(rows, "bread", limit=3)) == 3


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

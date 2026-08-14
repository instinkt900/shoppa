"""Plain-assert tests for the storage layer.

Runnable with a bare interpreter (`python tests/test_db.py`) and also
pytest-compatible. `Database` only needs a path, so each test gets a throwaway
SQLite file in a temp dir — no fixtures, no mocks, stdlib only.

The pure ordering/matching maths is covered in `test_items.py`; what's checked
here is the part that only the database can decide: the dedupe rule, and that
ticking an item off moves it between the lists rather than losing it.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shoppa import items as items_logic
from shoppa.db import Database

NOW = 1_800_000_000
DAY = 86_400


def _db() -> Database:
    tmp = tempfile.mkdtemp(prefix="shoppa-test-")
    db = Database(Path(tmp) / "shoppa.db")
    db.init_db()
    return db


def _names(rows) -> list[str]:
    return [i.name for i in rows]


# --- adding -------------------------------------------------------------------


def test_add_puts_an_item_on_the_list():
    db = _db()
    item = db.add_item("Chicken", "Breast. 600g", NOW)
    assert item.id is not None
    assert (item.name, item.note, item.pending, item.times_added) == \
        ("Chicken", "Breast. 600g", True, 1)


def test_init_db_is_idempotent():
    db = _db()
    db.add_item("Chicken", "", NOW)
    db.init_db()          # every startup runs it again
    db.init_db()
    assert len(db.list_items()) == 1


def test_readding_the_same_entry_revives_one_row():
    db = _db()
    first = db.add_item("Chicken", "Breast. 600g", NOW)
    db.set_pending(first.id, False, NOW + DAY)         # ticked off
    again = db.add_item("chicken", "breast. 600g", NOW + 2 * DAY)   # typed sloppily
    assert again.id == first.id, "should revive the entry, not duplicate it"
    assert again.pending is True
    assert again.times_added == 2
    assert len(db.list_items()) == 1
    # Display text follows what was typed most recently.
    assert (again.name, again.note) == ("chicken", "breast. 600g")


def test_readding_something_already_pending_does_not_count_twice():
    db = _db()
    first = db.add_item("Milk", "", NOW)
    again = db.add_item("Milk", "", NOW + 60)
    assert again.id == first.id
    assert again.times_added == 1, "re-tapping a pending item isn't a second purchase"
    assert again.added_at == NOW + 60, "but it does float back to the top"


def test_same_name_different_note_are_separate_entries():
    db = _db()
    breast = db.add_item("Chicken", "Breast. 600g", NOW)
    thighs = db.add_item("Chicken", "Thighs. 1kg", NOW)
    assert breast.id != thighs.id
    assert len(db.list_items()) == 2


# --- moving between the lists -------------------------------------------------


def test_done_moves_it_to_previous_without_deleting():
    db = _db()
    item = db.add_item("Chicken", "Breast. 600g", NOW)
    done = db.set_pending(item.id, False, NOW + DAY)
    assert done.pending is False
    assert done.done_at == NOW + DAY
    b = items_logic.board(db.list_items())
    assert _names(b["pending"]) == []
    assert _names(b["previous"]) == ["Chicken"]


def test_repeat_moves_it_back_and_counts_the_add():
    db = _db()
    item = db.add_item("Milk", "2L", NOW)
    db.set_pending(item.id, False, NOW + DAY)
    back = db.set_pending(item.id, True, NOW + 2 * DAY)
    assert back.pending is True
    assert back.added_at == NOW + 2 * DAY
    assert back.times_added == 2
    # The done stamp is left behind as history rather than cleared.
    assert back.done_at == NOW + DAY


def test_set_pending_on_a_missing_row_returns_none():
    assert _db().set_pending(999, False, NOW) is None


# --- editing and deleting ----------------------------------------------------


def test_rename_moves_the_dedupe_key_with_the_text():
    db = _db()
    item = db.add_item("Chikcen", "", NOW)          # typo, fixed in the editor
    db.rename_item(item.id, "Chicken", "Breast. 600g")
    # Re-adding under the corrected spelling must find the row we just fixed,
    # which only works if the stored key moved with the display text.
    again = db.add_item("chicken", "Breast. 600g", NOW + DAY)
    assert again.id == item.id
    assert len(db.list_items()) == 1


def test_rename_into_an_existing_entry_is_refused():
    import sqlite3

    db = _db()
    db.add_item("Chicken", "Breast. 600g", NOW)
    other = db.add_item("Chicken", "Thighs. 1kg", NOW)
    try:
        db.rename_item(other.id, "Chicken", "Breast. 600g")
    except sqlite3.IntegrityError:
        pass   # the web layer turns this into a 409
    else:
        raise AssertionError("editing one entry onto another should not silently merge them")
    assert len(db.list_items()) == 2


def test_rename_missing_row_returns_none():
    assert _db().rename_item(999, "Chicken", "") is None


def test_delete_is_permanent():
    db = _db()
    item = db.add_item("Chicken", "", NOW)
    assert db.delete_item(item.id) is True
    assert db.list_items() == []
    assert db.delete_item(item.id) is False


def test_clear_previous_keeps_the_current_list():
    db = _db()
    keep = db.add_item("Chicken", "", NOW)
    drop = db.add_item("Milk", "", NOW)
    db.set_pending(drop.id, False, NOW + DAY)
    assert db.clear_previous() == 1
    assert _names(db.list_items()) == ["Chicken"]
    assert db.get_item(keep.id) is not None


def test_count_pending_only_counts_the_list():
    db = _db()
    db.add_item("Chicken", "", NOW)
    milk = db.add_item("Milk", "", NOW)
    db.set_pending(milk.id, False, NOW + DAY)
    assert db.count_pending() == 1


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()

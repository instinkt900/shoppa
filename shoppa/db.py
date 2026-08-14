"""SQLite storage.

The `Database` wrapper holds only a path and hands out short-lived connections
(one per operation via `with self.connect()`), each with WAL and foreign keys on.
The whole schema is a single `_SCHEMA` string of `CREATE ... IF NOT EXISTS`
statements; `init_db()` runs it (plus any pending migrations — guarded
`ALTER TABLE`s for columns, a `PRAGMA user_version` bump for row rewrites) on
every startup, so it is idempotent and forward-only. Rows map to/from the `Item`
dataclass in `models.py` via the `_item` helper.

One row per entry, moved between the two lists by the `pending` flag: nothing here
deletes on completion, so the history is simply every row that isn't pending.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from shoppa.items import entry_key
from shoppa.models import Item

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    note        TEXT    NOT NULL DEFAULT '',
    name_key    TEXT    NOT NULL,                  -- normalised name, for dedupe
    note_key    TEXT    NOT NULL DEFAULT '',       -- normalised note, ditto
    pending     INTEGER NOT NULL DEFAULT 1,        -- 1 = on the list, 0 = previous entry
    created_at  INTEGER NOT NULL,
    added_at    INTEGER NOT NULL,                  -- last entered the pending list
    done_at     INTEGER,                           -- last left it; NULL if never
    times_added INTEGER NOT NULL DEFAULT 1
);
-- The dedupe rule, enforced by the database rather than by a read-then-write:
-- one row per (name, note) pair, so re-adding "Chicken / Breast. 600g" revives
-- the entry you already have while "Chicken / Thighs. 1kg" gets its own.
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_entry ON items(name_key, note_key);
CREATE INDEX IF NOT EXISTS idx_items_pending ON items(pending, added_at);
"""

# Schema version for data migrations that can't be detected by inspection. Bump
# it (and add a branch to `_migrate_data`) when a release has to rewrite rows.
_USER_VERSION = 1

# Columns a PATCH is allowed to touch (whitelist guards against arbitrary writes).
# `name`/`note` aren't here: they carry normalised keys that have to move with
# them, so they go through `rename_item`.
_UPDATABLE = {"pending", "added_at", "done_at"}


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self) -> None:
        """Create the schema (and run any pending migrations) — idempotent."""
        parent = Path(self.path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate_data(conn)

    def _migrate_data(self, conn: sqlite3.Connection) -> None:
        """Row rewrites, tracked by `PRAGMA user_version`.

        Column additions can be guarded by `_has_column` — a column either exists
        or it doesn't — but a data rewrite leaves no such trace, so it needs a
        version stamp of its own or it would run again on every startup. Nothing
        to do at version 1; the scaffolding is here so the first migration that
        needs it has somewhere to go.
        """
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= _USER_VERSION:
            return
        # Not parameterisable — PRAGMA takes a literal — hence the f-string over a
        # module constant rather than user input.
        conn.execute(f"PRAGMA user_version = {_USER_VERSION}")

    @staticmethod
    def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == column for row in rows)

    # --- reads ---------------------------------------------------------------

    def list_items(self) -> list[Item]:
        """Every row, both lists. Ordering is `items.board`'s job, not SQL's."""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM items").fetchall()
        return [_item(r) for r in rows]

    def get_item(self, item_id: int) -> Item | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return _item(row) if row else None

    def count_pending(self) -> int:
        with self.connect() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM items WHERE pending = 1"
            ).fetchone()[0])

    # --- writes --------------------------------------------------------------

    def add_item(self, name: str, note: str, now: int) -> Item:
        """Put an entry on the pending list, reviving the existing row if there is
        one for this exact (name, note) pair.

        Written as an upsert rather than a SELECT-then-INSERT so two phones adding
        the same thing at once can't race past each other into a duplicate — the
        unique index decides, inside the statement. Re-adding something already
        pending is not an error; it just floats back to the top of the list.

        `name`/`note` are stored as typed but matched normalised, so re-adding
        "chicken" updates the display text of the "Chicken" row to what you last
        typed instead of arguing with you about the capital.
        """
        name_key, note_key = entry_key(name, note)
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO items
                     (name, note, name_key, note_key, pending, created_at, added_at, times_added)
                   VALUES (?, ?, ?, ?, 1, ?, ?, 1)
                   ON CONFLICT(name_key, note_key) DO UPDATE SET
                     name        = excluded.name,
                     note        = excluded.note,
                     pending     = 1,
                     added_at    = excluded.added_at,
                     -- Only count it as a fresh add if it wasn't already on the
                     -- list; re-tapping a pending item isn't a second purchase.
                     times_added = items.times_added + (CASE WHEN items.pending = 1 THEN 0 ELSE 1 END)""",
                (name.strip(), note.strip(), name_key, note_key, now, now),
            )
            row = conn.execute(
                "SELECT * FROM items WHERE name_key = ? AND note_key = ?",
                (name_key, note_key),
            ).fetchone()
        return _item(row)

    def set_pending(self, item_id: int, pending: bool, now: int) -> Item | None:
        """Move an entry between the two lists — the tap, in both directions.

        Ticking off stamps `done_at` (which orders the previous list); putting it
        back stamps `added_at` and counts another add. Returns the updated item, or
        None if the row is gone.
        """
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                return None
            if pending:
                conn.execute(
                    """UPDATE items
                          SET pending = 1, added_at = ?,
                              times_added = times_added + (CASE WHEN pending = 1 THEN 0 ELSE 1 END)
                        WHERE id = ?""",
                    (now, item_id),
                )
            else:
                conn.execute(
                    "UPDATE items SET pending = 0, done_at = ? WHERE id = ?", (now, item_id)
                )
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return _item(row)

    def rename_item(self, item_id: int, name: str, note: str) -> Item | None:
        """Edit an entry's text — what the long-press editor saves.

        The normalised keys are derived here rather than passed in, so an edit can
        never leave a row whose dedupe key disagrees with its display text. Editing
        one entry into another that already exists would break the unique index, so
        it's refused (`sqlite3.IntegrityError` reaches the caller, which turns it
        into a 409) rather than silently merging two rows and losing a note.
        """
        name_key, note_key = entry_key(name, note)
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE items SET name = ?, note = ?, name_key = ?, note_key = ? WHERE id = ?",
                (name.strip(), note.strip(), name_key, note_key, item_id),
            )
            if cur.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return _item(row)

    def update_item(self, item_id: int, fields: dict) -> bool:
        """Whitelisted column writes for the JSON API (`pending` and its stamps)."""
        cols = {k: v for k, v in fields.items() if k in _UPDATABLE and v is not None}
        if not cols:
            return False
        if "pending" in cols:
            cols["pending"] = int(bool(cols["pending"]))
        assignments = ", ".join(f"{k} = ?" for k in cols)
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE items SET {assignments} WHERE id = ?", (*cols.values(), item_id)
            )
            return cur.rowcount > 0

    def delete_item(self, item_id: int) -> bool:
        """Permanent — the only thing in Shoppa that actually loses data."""
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
            return cur.rowcount > 0

    def clear_previous(self) -> int:
        """Drop the whole history, keeping anything currently on the list."""
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM items WHERE pending = 0")
            return cur.rowcount


def _item(row: sqlite3.Row) -> Item:
    return Item(
        name=row["name"],
        note=row["note"],
        pending=bool(row["pending"]),
        created_at=row["created_at"],
        added_at=row["added_at"],
        done_at=row["done_at"],
        times_added=row["times_added"],
        id=row["id"],
    )

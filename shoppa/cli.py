"""Shoppa command-line entrypoint.

Subcommands: serve (web app), init-db (create schema and exit), list (print both
lists, text or JSON), add (put something on the list without opening the app).
Imports are lazy per branch so a quick command doesn't drag in uvicorn/fastapi.
"""

from __future__ import annotations

import argparse
import json
import logging

from shoppa import __version__


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shoppa", description=__doc__)
    parser.add_argument("--version", action="version", version=f"shoppa {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the web app")
    p_serve.add_argument("-c", "--config", required=True)

    p_init = sub.add_parser("init-db", help="create the database schema and exit")
    p_init.add_argument("-c", "--config", required=True)

    p_list = sub.add_parser("list", help="print the shopping list and recent history")
    p_list.add_argument("-c", "--config", required=True)
    p_list.add_argument("--json", action="store_true", help="emit JSON instead of text")
    p_list.add_argument("--all", action="store_true",
                        help="show the whole history, not just [web] previous_limit of it")

    p_add = sub.add_parser("add", help="put an entry on the list")
    p_add.add_argument("-c", "--config", required=True)
    p_add.add_argument("name")
    p_add.add_argument("note", nargs="?", default="", help='e.g. "Breast. 600g"')

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.command == "serve":
        from shoppa.web import run_serve
        return run_serve(args.config)

    if args.command == "init-db":
        from shoppa.config import load_config
        from shoppa.db import Database
        cfg = load_config(args.config)
        Database(cfg.database.path).init_db()
        print(f"initialized database at {cfg.database.path}")
        return 0

    if args.command == "list":
        return _list(args.config, as_json=args.json, show_all=args.all)

    if args.command == "add":
        return _add(args.config, args.name, args.note)

    return 1


def _open(config_path: str):
    """Load config and hand back an initialised database — every command's start."""
    from shoppa.config import load_config
    from shoppa.db import Database

    cfg = load_config(config_path)
    db = Database(cfg.database.path)
    db.init_db()
    return cfg, db


def _add(config_path: str, name: str, note: str) -> int:
    import time

    _cfg, db = _open(config_path)
    item = db.add_item(name, note, int(time.time()))
    suffix = f" — {item.note}" if item.note else ""
    print(f"added: {item.name}{suffix}")
    return 0


def _list(config_path: str, *, as_json: bool, show_all: bool) -> int:
    import time

    from shoppa import items as items_logic

    cfg, db = _open(config_path)
    now = int(time.time())
    # --all means the whole history: neither cut applies, the same way the JSON
    # API never applies them.
    limit = None if show_all else cfg.web.previous_limit
    max_age = None if show_all else cfg.web.previous_max_age
    b = items_logic.board(db.list_items(), limit, max_age, now)

    def row(i, anchor):
        return {
            "id": i.id,
            "name": i.name,
            "note": i.note,
            "times_added": i.times_added,
            "ago": items_logic.humanize_ago(max(now - anchor, 0)),
        }

    if as_json:
        print(json.dumps(
            {"now": now,
             "pending": [row(i, i.added_at) for i in b["pending"]],
             "previous": [row(i, i.done_at or i.created_at) for i in b["previous"]]},
            indent=2,
        ))
        return 0

    def line(i, anchor):
        note = f" — {i.note}" if i.note else ""
        return f"  • {i.name}{note}  [{items_logic.humanize_ago(max(now - anchor, 0))}]"

    print(f"Shopping list ({len(b['pending'])}):")
    for i in b["pending"]:
        print(line(i, i.added_at))
    if not b["pending"]:
        print("  (empty)")

    print(f"\nPrevious entries ({len(b['previous'])}):")
    for i in b["previous"]:
        print(line(i, i.done_at or i.created_at))
    if not b["previous"]:
        print("  (none yet)")
    return 0

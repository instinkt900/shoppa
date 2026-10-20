"""TOML configuration.

One file (`config.toml`; the tracked template is `config.example.toml`) holds all
runtime settings. We use nested dataclasses plus `_known()` so an unknown TOML key
fails loudly rather than being silently dropped — a typo'd setting should be a
startup error, not a mystery. Shoppa has no secrets today; if it ever grows one it
comes from the environment only, and since it wouldn't be a field of any section
`_known()` rejects it automatically if someone pastes it into the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+ ships tomllib; fall back to the tomli backport otherwise.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


class ConfigError(RuntimeError):
    """Raised for any malformed or disallowed configuration."""


@dataclass
class DatabaseConfig:
    path: str = "shoppa.db"


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    # Display only — nothing in Shoppa is scheduled, so this just decides how
    # "2 days ago" and the dates in the JSON API read.
    timezone: str = "UTC"
    # How many previous entries the phone shows: the most often added, then the
    # most recent. The rest stay in the database, in `GET /api/items` and in the
    # add form's suggestions; they're only cut from the screen.
    previous_limit: int = 10
    # Drop previous entries not used in this many days. 0 = no age cut, which is
    # the default so an existing install's history doesn't shrink on upgrade.
    # This is the cut that actually means "old"; `previous_limit` is only a cap on
    # how much of what's left can bury a screen.
    previous_max_age_days: int = 0

    @property
    def previous_max_age(self) -> int | None:
        """The age cut in seconds, or None for "no cut" — what `items.board` takes.

        A property, not a field, so `_known()` still rejects it in the TOML: days
        are what a person writes in a config file, seconds are what the rest of
        the app counts in, and only one of the two is a setting.
        """
        return self.previous_max_age_days * 86_400 or None


@dataclass
class Config:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    web: WebConfig = field(default_factory=WebConfig)


def _known(section: dict, cls) -> dict:
    """Keep only keys that map to a field on `cls`, rejecting anything else so a
    misspelled key surfaces immediately instead of being ignored."""
    allowed = {f.name for f in cls.__dataclass_fields__.values()}
    unknown = set(section) - allowed
    if unknown:
        raise ConfigError(
            f"unknown keys in [{cls.__name__.replace('Config', '').lower()}]: {sorted(unknown)}"
        )
    return {k: v for k, v in section.items() if k in allowed}


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    with p.open("rb") as fh:
        raw = tomllib.load(fh)

    unknown_sections = set(raw) - {"database", "web"}
    if unknown_sections:
        raise ConfigError(f"unknown config sections: {sorted(unknown_sections)}")

    database = DatabaseConfig(**_known(raw.get("database", {}), DatabaseConfig))
    web = WebConfig(**_known(raw.get("web", {}), WebConfig))

    if web.previous_limit < 0:
        raise ConfigError("[web] previous_limit must be >= 0")
    if web.previous_max_age_days < 0:
        raise ConfigError("[web] previous_max_age_days must be >= 0 (0 = no age cut)")

    return Config(database=database, web=web)

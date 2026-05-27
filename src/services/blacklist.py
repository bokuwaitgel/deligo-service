"""Driver blacklist.

Entry points that fan out to Deligo + Google APIs should short-circuit when the
driver_id is on this list — no Deligo fetch, no geocoding, no DB writes.

Defaults are hardcoded below. Add more at runtime via the DRIVER_BLACKLIST_IDS
env var (comma-separated driver IDs).
"""
from __future__ import annotations

import os

_DEFAULT_DRIVER_BLACKLIST: set[str] = {"1704422069423385"}


def _load_blacklist() -> set[str]:
    result = set(_DEFAULT_DRIVER_BLACKLIST)
    raw = os.getenv("DRIVER_BLACKLIST_IDS", "").strip()
    if raw:
        result.update(part.strip() for part in raw.split(",") if part.strip())
    return result


DRIVER_BLACKLIST: set[str] = _load_blacklist()


def is_driver_blacklisted(driver_id: object) -> bool:
    if driver_id is None:
        return False
    return str(driver_id) in DRIVER_BLACKLIST

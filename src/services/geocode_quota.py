"""Per-driver daily quota on geocode attempts.

Caps Google API spend driven by any single driver. When a driver hits
DAILY_GEOCODE_LIMIT in one day, subsequent geocode requests for them are
skipped — the local order row is still created, just without a
customer_location; it can be geocoded on a future day when quota resets.

Defaults to 200 geocode attempts per driver per day. Override via the
DRIVER_DAILY_GEOCODE_LIMIT env var.

Counter is in-process and resets at Mongolia (Asia/Ulaanbaatar) midnight,
matching the business day. With multiple uvicorn workers, each worker tracks
its own counts (so the effective limit becomes N_workers * DAILY_GEOCODE_LIMIT).
Move to Redis if a strict cross-worker cap is required.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from threading import Lock
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DAILY_GEOCODE_LIMIT = int(os.getenv("DRIVER_DAILY_GEOCODE_LIMIT", "200"))

# Quota day rolls over at Mongolia midnight, not server-local midnight.
_MN_TZ = ZoneInfo("Asia/Ulaanbaatar")


def _mn_today() -> date:
    return datetime.now(_MN_TZ).date()


_counts: dict[str, tuple[date, int]] = {}
_lock = Lock()


def consume_geocode_quota(driver_id: object) -> bool:
    """Atomically check and consume one geocode credit for this driver.

    Returns True if the call is allowed (and the credit has been consumed),
    False if today's quota for this driver is already exhausted.

    A None/empty driver_id is treated as "no quota check" (returns True) —
    background or anonymous flows are never rate-limited.
    """
    if not driver_id:
        return True
    key = str(driver_id)
    today = _mn_today()
    with _lock:
        entry = _counts.get(key)
        if entry is None or entry[0] != today:
            _counts[key] = (today, 1)
            return True
        if entry[1] >= DAILY_GEOCODE_LIMIT:
            return False
        new_count = entry[1] + 1
        _counts[key] = (today, new_count)
        if new_count == DAILY_GEOCODE_LIMIT:
            logger.warning(
                "Driver %s hit daily geocode limit (%d) — further geocodes skipped today",
                key, DAILY_GEOCODE_LIMIT,
            )
        return True


def get_geocode_usage(driver_id: object) -> int:
    """Return today's geocode count for this driver (0 if unused or stale)."""
    if not driver_id:
        return 0
    key = str(driver_id)
    today = _mn_today()
    with _lock:
        entry = _counts.get(key)
        if entry is None or entry[0] != today:
            return 0
        return entry[1]

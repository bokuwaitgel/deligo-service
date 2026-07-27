"""Resolve and authorize the driver behind an ``X-Driver-Token`` header.

Before Package 1 the mere *presence* of ``X-Driver-Token`` was enough to take the
driver code path in ``POST /api/delivery/location`` — the token was never
validated and never checked against the order. Anyone who could reach the API
with the shared ``X-API-Key`` could therefore move the pin of any order.

This module turns that header into a verified identity: the token is exchanged
for the Deligo user record, and the resulting driver id must match the driver the
order is assigned to.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

from src.services.deligo_user_proxy import DeligoApiError, pick_driver_id, user_info

logger = logging.getLogger(__name__)


class DriverAuthError(Exception):
    """The supplied driver token is missing, invalid, or not for this order."""


# Verifying costs one Deligo round-trip. A driver re-pinning an address triggers
# a couple of saves in a row, so cache the resolved identity briefly. Keyed by a
# hash of the token — the raw JWT never enters the cache key or any log line.
_IDENTITY_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_IDENTITY_CACHE_TTL = int(os.getenv("DRIVER_IDENTITY_CACHE_TTL", "120"))
_IDENTITY_CACHE_MAX = 500


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _cached(token: str) -> Optional[Dict[str, Any]]:
    if _IDENTITY_CACHE_TTL <= 0:
        return None
    hit = _IDENTITY_CACHE.get(_token_key(token))
    if not hit:
        return None
    cached_at, user = hit
    if time.time() - cached_at > _IDENTITY_CACHE_TTL:
        _IDENTITY_CACHE.pop(_token_key(token), None)
        return None
    return user


def _store(token: str, user: Dict[str, Any]) -> None:
    if _IDENTITY_CACHE_TTL <= 0:
        return
    if len(_IDENTITY_CACHE) >= _IDENTITY_CACHE_MAX:
        # Cheap eviction: drop the oldest entry rather than tracking an LRU.
        oldest = min(_IDENTITY_CACHE, key=lambda k: _IDENTITY_CACHE[k][0])
        _IDENTITY_CACHE.pop(oldest, None)
    _IDENTITY_CACHE[_token_key(token)] = (time.time(), user)


def pick_driver_name(user: Dict[str, Any]) -> Optional[str]:
    """Best-effort display name for the audit log."""
    for key in ("driver_name", "name", "full_name", "username", "email"):
        value = user.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    first = str(user.get("first_name") or "").strip()
    last = str(user.get("last_name") or "").strip()
    combined = " ".join(part for part in (first, last) if part)
    return combined or None


def resolve_driver(token: Optional[str]) -> Dict[str, Any]:
    """Exchange a driver token for the Deligo user record.

    Raises DriverAuthError when the token is absent or Deligo rejects it.
    """
    clean = (token or "").strip()
    if not clean:
        raise DriverAuthError("Жолоочийн эрх шаардлагатай.")

    cached = _cached(clean)
    if cached is not None:
        return cached

    try:
        user = user_info(clean)
    except DeligoApiError as exc:
        logger.info("Driver token rejected by Deligo: %s", exc)
        raise DriverAuthError("Жолоочийн эрх хүчингүй. Дахин нэвтэрнэ үү.") from exc

    if not isinstance(user, dict) or not pick_driver_id(user):
        raise DriverAuthError("Жолоочийн эрх хүчингүй. Дахин нэвтэрнэ үү.")

    _store(clean, user)
    return user


def authorize_driver_for_order(
    token: Optional[str],
    order_driver_id: Optional[str],
) -> Tuple[str, Optional[str]]:
    """Verify the token AND that it belongs to the order's assigned driver.

    Returns ``(driver_id, driver_name)``.

    An order with no assigned driver is not a driver's to edit — that case is the
    customer/shop path and must go through the normal editability gate instead.
    """
    user = resolve_driver(token)
    driver_id = pick_driver_id(user)
    if driver_id is None:
        raise DriverAuthError("Жолоочийн эрх хүчингүй. Дахин нэвтэрнэ үү.")

    if not order_driver_id:
        raise DriverAuthError("Захиалга жолоочид хуваарилагдаагүй байна.")

    if str(order_driver_id).strip() != str(driver_id).strip():
        logger.info(
            "Driver %s attempted to edit an order assigned to driver %s",
            driver_id, order_driver_id,
        )
        raise DriverAuthError("Энэ захиалга танд хуваарилагдаагүй байна.")

    return str(driver_id), pick_driver_name(user)

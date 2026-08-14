"""Delivery-status catalog — colour, icon and label for every wfm status.

**This is a stand-in.** Deligo (`api.deligo.mn`) does not expose a status
catalog yet; the only status decoration it sends today is `status_color`
("lime", "red", …) glued onto each order in `POST /api/sales/integration`,
which is a CSS colour name, has no icon, and is not a list you can enumerate.
Until they publish one, this module *is* the catalog: it serves the same shape
we asked them for (`docs/STATUS-CATALOG-API.md`), so switching over later is a
URL change, not a refactor.

Three ways the catalog can be resolved, first one that works wins:

1. ``DELIGO_STATUS_CATALOG_URL`` — the real upstream, once it exists. Fetched
   with the service token, cached for ``STATUS_CATALOG_TTL`` seconds.
2. ``STATUS_CATALOG_JSON`` — a full catalog pasted into env. Lets ops try
   Deligo's payload (or fix one colour) without a deploy.
3. ``_DUMMY_CATALOG`` below — the built-in fallback. Always present, so the
   endpoint never fails and the frontend never has to render a blank legend.

The response always says which one answered (``source``), because "the colours
look wrong" and "we are still on the dummy" are the same symptom.

Colours are duplicated on purpose: ``color``/``pin_color`` are hex because the
map marker is built with inline styles, ``legacy_color`` is Deligo's own token
so we can diff their wording against ours when they do ship the catalog.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

CATALOG_VERSION = "2026-08-14"
_HTTP_TIMEOUT = 10.0

STATUS_CATALOG_URL = os.getenv("DELIGO_STATUS_CATALOG_URL", "").strip()
STATUS_CATALOG_TTL = int(os.getenv("STATUS_CATALOG_TTL", "900"))  # seconds; 0 = no cache

# Status groups. The frontend colours by status, but filters and counts by
# group — "хойшилсон" is five wfm ids that mean the same thing to an operator.
GROUP_PENDING = "pending"     # not on a driver yet
GROUP_ACTIVE = "active"       # assigned / on the road
GROUP_SUCCESS = "success"     # delivered / exchanged
GROUP_DEFERRED = "deferred"   # attempted, will retry
GROUP_FAILED = "failed"       # will not be delivered

GROUP_LABELS: Dict[str, str] = {
    GROUP_PENDING: "Хүлээгдэж буй",
    GROUP_ACTIVE: "Хүргэлтэд",
    GROUP_SUCCESS: "Амжилттай",
    GROUP_DEFERRED: "Хойшилсон",
    GROUP_FAILED: "Амжилтгүй",
}


def _entry(
    wfm_status_id: int,
    status_code: str,
    label: str,
    label_en: str,
    description: str,
    icon: str,
    color: str,
    pin_color: str,
    group: str,
    sort_order: int,
    *,
    legacy_color: Optional[str] = None,
    is_closed: bool = False,
    is_driver_only: bool = False,
    customer_visible: bool = True,
    requires_proof: bool = False,
) -> Dict[str, Any]:
    return {
        "wfm_status_id": wfm_status_id,
        "status_code": status_code,
        "label": label,
        "label_en": label_en,
        "description": description,
        "icon": icon,
        "icon_set": "material-symbols",
        "color": color,
        "pin_color": pin_color,
        "legacy_color": legacy_color,
        "group": group,
        "group_label": GROUP_LABELS[group],
        "is_closed": is_closed,
        "is_driver_only": is_driver_only,
        "customer_visible": customer_visible,
        "requires_proof": requires_proof,
        "sort_order": sort_order,
    }


# Colours/icons mirror `delivery-map-frontend/lib/status-visuals.ts` — that file
# stays the offline fallback, this one is what the API serves. They must agree;
# `GET /api/status/catalog?check=1` reports drift once the real upstream lands.
_DUMMY_STATUSES: List[Dict[str, Any]] = [
    _entry(1, "salesNew", "Хүлээгдэж байна", "New",
           "Жолоочид хуваарилагдаагүй шинэ захиалга",
           "schedule", "#f59e0b", "#fbbf24", GROUP_PENDING, 10,
           legacy_color="orange"),
    _entry(5, "salesDelivery", "Хуваарилсан", "Assigned",
           "Жолоочид хуваарилсан, хараахан хүлээж аваагүй",
           "assignment_ind", "#0ea5e9", "#38bdf8", GROUP_ACTIVE, 20,
           legacy_color="lime"),
    _entry(8, "salesDriverDone", "Жолооч хүлээн авсан", "Picked up",
           "Жолооч хүргэлтэд гарсан",
           "local_shipping", "#2563eb", "#3b82f6", GROUP_ACTIVE, 30,
           legacy_color="blue"),
    _entry(3, "salesDone", "Хүргэсэн", "Delivered",
           "Хүргэлт амжилттай дууссан",
           "task_alt", "#059669", "#10b981", GROUP_SUCCESS, 40,
           legacy_color="green", is_closed=True),
    _entry(23, "exchanged", "Сольж авсан", "Exchanged",
           "Бараа солигдож хүргэгдсэн",
           "swap_horiz", "#7c3aed", "#a855f7", GROUP_SUCCESS, 50,
           legacy_color="purple", is_closed=True, is_driver_only=True),
    _entry(12, "deliveryCancel", "Авахаа больсон", "Cancelled",
           "Захиалагч авахаа больсон",
           "cancel", "#dc2626", "#ef4444", GROUP_FAILED, 60,
           legacy_color="red", is_closed=True, is_driver_only=True,
           requires_proof=True),
    _entry(13, "deliveryTomorrow", "Маргааш авна", "Tomorrow",
           "Хүргэлт маргаашлуулсан",
           "event_repeat", "#d97706", "#f59e0b", GROUP_DEFERRED, 70,
           legacy_color="orange", is_driver_only=True, requires_proof=True),
    _entry(14, "deliveryNophone", "Утсаа аваагүй", "No answer",
           "Захиалагч утсаа аваагүй",
           "phone_missed", "#c2410c", "#ea580c", GROUP_DEFERRED, 80,
           legacy_color="orange", is_driver_only=True, requires_proof=True),
    _entry(15, "deliveryNoCall", "Дугаар холбогдохгүй", "Unreachable",
           "Дугаар холбогдохгүй байна",
           "phone_disabled", "#9f1239", "#be123c", GROUP_DEFERRED, 90,
           legacy_color="red", is_driver_only=True, requires_proof=True),
    _entry(16, "deliveryGotoAddress", "Хаягаар очсон", "Visited",
           "Хаягаар очсон боловч хүргэж чадаагүй",
           "wrong_location", "#0d9488", "#14b8a6", GROUP_DEFERRED, 100,
           legacy_color="teal", is_driver_only=True, requires_proof=True),
    _entry(17, "deliveryNextBuy", "Дараа авна", "Later",
           "Захиалагч дараа авахаар хойшлуулсан",
           "more_time", "#475569", "#64748b", GROUP_DEFERRED, 110,
           legacy_color="grey", is_driver_only=True, requires_proof=True),
]

# Not a wfm status: `is_pay` is orthogonal (an order can be paid *and*
# delivered), so it renders as its own badge. Deligo should keep flags in a
# separate list for exactly this reason — folding them into `statuses` is what
# made "Хүргэгдсэн" and "Төлбөр төлөгдсөн" both green in the first place.
_DUMMY_FLAGS: List[Dict[str, Any]] = [
    {
        "key": "paid",
        "order_field": "is_pay",
        "label": "Төлбөр төлөгдсөн",
        "label_en": "Paid",
        "description": "Урьдчилан төлөгдсөн — жолооч мөнгө авахгүй",
        "icon": "paid",
        "icon_set": "material-symbols",
        "color": "#15803d",
        "pin_color": "#22c55e",
    },
    {
        "key": "countryside",
        "order_field": "is_country",
        "label": "Орон нутаг",
        "label_en": "Countryside",
        "description": "Аймаг руу явах унаанд өгөгдөнө — хаалганд хүргэхгүй",
        "icon": "local_shipping",
        "icon_set": "material-symbols",
        "color": "#7c3aed",
        "pin_color": "#a855f7",
    },
]

# Rendered when an order carries a wfm id we have never seen. The frontend has
# the same entry offline; it is in the payload so a new upstream status shows up
# as a grey "Тодорхойгүй (99)" chip instead of an empty marker.
_UNKNOWN_STATUS: Dict[str, Any] = _entry(
    0, "unknown", "Тодорхойгүй", "Unknown",
    "Системд бүртгэгдээгүй төлөв",
    "help", "#64748b", "#94a3b8", GROUP_PENDING, 9999,
)


class CatalogSource:
    """Where the served catalog came from. Surfaced in the payload + admin tab."""

    UPSTREAM = "deligo"
    ENV = "env"
    DUMMY = "local-dummy"


# (expires_at, payload) — only the upstream fetch is cached; env/dummy are free.
_CACHE: Optional[Tuple[float, Dict[str, Any]]] = None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _wrap(statuses: List[Dict[str, Any]], flags: List[Dict[str, Any]], source: str,
          *, version: str = CATALOG_VERSION, note: Optional[str] = None) -> Dict[str, Any]:
    return {
        "version": version,
        "source": source,
        "generated_at": _now_iso(),
        "icon_set": "material-symbols",
        "groups": [{"key": k, "label": v} for k, v in GROUP_LABELS.items()],
        "statuses": sorted(statuses, key=lambda s: s.get("sort_order", 9999)),
        "flags": flags,
        "unknown": _UNKNOWN_STATUS,
        "note": note,
    }


def _from_env() -> Optional[Dict[str, Any]]:
    raw = os.getenv("STATUS_CATALOG_JSON", "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("STATUS_CATALOG_JSON is not valid JSON — ignoring it")
        return None
    statuses = parsed.get("statuses")
    if not isinstance(statuses, list) or not statuses:
        logger.warning("STATUS_CATALOG_JSON has no `statuses` list — ignoring it")
        return None
    return _wrap(
        [_normalize_status(s) for s in statuses],
        parsed.get("flags") or _DUMMY_FLAGS,
        CatalogSource.ENV,
        version=str(parsed.get("version") or CATALOG_VERSION),
        note="STATUS_CATALOG_JSON-оос уншсан",
    )


def _normalize_status(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Fill the gaps in an upstream/env entry from our dummy of the same id.

    Deligo shipping ids and labels but no icons is the likeliest partial
    outcome, and a status with no icon renders as an empty box. Anything they
    do send wins; anything they omit falls back to ours.
    """
    wfm_id = _as_int(raw.get("wfm_status_id"))

    base = next((dict(s) for s in _DUMMY_STATUSES if s["wfm_status_id"] == wfm_id), None)
    if base is None:
        base = dict(_UNKNOWN_STATUS)
        base["wfm_status_id"] = wfm_id if wfm_id is not None else 0

    merged = {**base, **{k: v for k, v in raw.items() if v not in (None, "")}}
    group = merged.get("group")
    if group not in GROUP_LABELS:
        group = str(base["group"])
    merged["group"] = group
    merged["group_label"] = GROUP_LABELS[group]
    return merged


def _from_upstream(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not STATUS_CATALOG_URL:
        return None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.get(STATUS_CATALOG_URL, headers=headers, timeout=_HTTP_TIMEOUT)
        response.raise_for_status()
        body = response.json()
    except Exception as exc:  # network, auth, malformed JSON — all non-fatal
        logger.warning("Status catalog fetch failed (%s) — falling back", exc)
        return None

    # Accept both the bare catalog and Deligo's usual {"status","data"} envelope.
    raw_payload = body.get("data") if isinstance(body, dict) and "data" in body else body
    payload: Dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    statuses = payload.get("statuses")
    if not isinstance(statuses, list) or not statuses:
        logger.warning("Status catalog response had no `statuses` list — falling back")
        return None

    return _wrap(
        [_normalize_status(s) for s in statuses],
        payload.get("flags") or _DUMMY_FLAGS,
        CatalogSource.UPSTREAM,
        version=str(payload.get("version") or CATALOG_VERSION),
    )


def get_catalog(token: Optional[str] = None, *, refresh: bool = False) -> Dict[str, Any]:
    """The catalog to serve. Never raises, never returns an empty status list."""
    global _CACHE

    if not refresh and _CACHE and _CACHE[0] > time.monotonic():
        return _CACHE[1]

    catalog = _from_upstream(token) or _from_env()
    if catalog is None:
        catalog = _wrap(
            [dict(s) for s in _DUMMY_STATUSES],
            [dict(f) for f in _DUMMY_FLAGS],
            CatalogSource.DUMMY,
            note=(
                "Deligo талд төлвийн каталог API хараахан байхгүй тул түр зуурын "
                "жагсаалт. DELIGO_STATUS_CATALOG_URL тохируулмагц автоматаар "
                "тэндээс уншина."
            ),
        )

    if STATUS_CATALOG_TTL > 0 and catalog["source"] == CatalogSource.UPSTREAM:
        _CACHE = (time.monotonic() + STATUS_CATALOG_TTL, catalog)
    else:
        _CACHE = None
    return catalog


def apply_overrides(catalog: Dict[str, Any], overrides: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Lay the admin panel's edits over the resolved catalog.

    Overrides win over every source, including Deligo — an operator who fixes a
    colour at 9am should not have it fought back by the next upstream fetch.
    The cost is that a forgotten override silently masks Deligo's real value, so
    every touched entry carries `overridden: ["icon", …]` plus who/when, and the
    admin tab shows a revert button next to each. Nothing is hidden.

    Returns a copy — the served catalog can be cached, and the cached copy must
    stay override-free so a revert takes effect without a refetch.
    """
    if not overrides:
        # Shallow copy rather than the cached object: `override_count` must be
        # present (as 0) on every response so clients can render it without a
        # null check, and the cache must not gain the key.
        return {**catalog, "override_count": 0}

    merged = dict(catalog)
    statuses: List[Dict[str, Any]] = []

    for status in catalog.get("statuses", []):
        override = overrides.get(status.get("wfm_status_id"))
        if not override:
            statuses.append(status)
            continue

        entry = dict(status)
        applied: List[str] = []
        for field, value in override.items():
            if field.startswith("_") or value is None:
                continue
            entry[field] = value
            applied.append(field)

        if applied:
            entry["overridden"] = sorted(applied)
            entry["overridden_at"] = override.get("_updated_at")
            entry["overridden_by"] = override.get("_updated_by")
            # Keep what the source said, so the panel can show "Deligo: #059669
            # → та: #16a34a" instead of just the winning value.
            entry["source_values"] = {field: status.get(field) for field in applied}
        statuses.append(entry)

    merged["statuses"] = statuses
    merged["override_count"] = len([o for o in overrides.values() if any(
        not k.startswith("_") and v is not None for k, v in o.items()
    )])
    return merged


def find_status(catalog: Dict[str, Any], wfm_status_id: Optional[Any] = None,
                status_code: Optional[str] = None) -> Dict[str, Any]:
    """Look one status up by wfm id, then by code. Falls back to `unknown`."""
    try:
        wfm_id = int(wfm_status_id) if wfm_status_id is not None else None
    except (TypeError, ValueError):
        wfm_id = None

    for status in catalog.get("statuses", []):
        if wfm_id is not None and status.get("wfm_status_id") == wfm_id:
            return status
    if status_code:
        for status in catalog.get("statuses", []):
            if status.get("status_code") == status_code:
                return status

    unknown = dict(catalog.get("unknown") or _UNKNOWN_STATUS)
    if wfm_id is not None:
        unknown["label"] = f"{unknown['label']} ({wfm_id})"
        unknown["wfm_status_id"] = wfm_id
    return unknown

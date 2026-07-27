"""Server-managed notification content for the customer event stream.

Requirement 3.1.4: the title, body and link of every notification must be
controllable from the server, so wording can change without shipping a new
frontend build. The frontend renders whatever ``title`` / ``body`` / ``url`` the
event carries and never composes its own copy.

Templates are Python format strings resolved against the event payload. Any
individual template can be overridden at runtime with an environment variable
named ``NOTIFY_<EVENT_TYPE>_TITLE`` / ``_BODY`` (uppercase event type), and the
whole map can be replaced with a JSON blob in ``NOTIFY_TEMPLATES_JSON``.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Public tracking link the notification opens when clicked. Same prefix the
# backend already uses to build tracking_url.
TRACKING_URL_PREFIX = os.getenv("TRACKING_URL_PREFIX", "https://map.deligoalpha.mn/track/")


# event type -> {title, body, icon, urgency}
# `icon` is a Material Symbols name; the frontend maps it to both the browser
# notification icon and the in-app toast glyph.
_DEFAULT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "driver_accepted": {
        "title": "Жолооч захиалгыг хүлээн авлаа",
        "body": "Таны #{sales_number} захиалгыг жолооч хүлээн авч, хүргэлтэд бэлтгэж байна.",
        "icon": "local_shipping",
        "urgency": "normal",
    },
    "delivery_started": {
        "title": "Хүргэлт эхэллээ",
        "body": "Жолооч таны #{sales_number} захиалгатай замд гарлаа.",
        "icon": "navigation",
        "urgency": "high",
    },
    "driver_nearby": {
        "title": "Жолооч ойртож байна",
        "body": "Жолооч танаас ойролцоогоор {distance_text} зайд байна.",
        "icon": "near_me",
        "urgency": "high",
    },
    "driver_location": {
        "title": "Жолоочийн байршил шинэчлэгдлээ",
        "body": "Жолоочийн байршил газрын зураг дээр шинэчлэгдлээ.",
        "icon": "my_location",
        "urgency": "low",
    },
    "delivery_completed": {
        "title": "Хүргэлт амжилттай боллоо",
        "body": "Таны #{sales_number} захиалга хүргэгдлээ. Баярлалаа!",
        "icon": "task_alt",
        "urgency": "high",
    },
    "delivery_failed": {
        "title": "Хүргэлт амжилтгүй боллоо",
        "body": "Таны #{sales_number} захиалгын төлөв: {status_label}.",
        "icon": "error",
        "urgency": "high",
    },
    "order_cancelled": {
        "title": "Захиалга цуцлагдлаа",
        "body": "Таны #{sales_number} захиалга цуцлагдсан байна.",
        "icon": "cancel",
        "urgency": "high",
    },
    "address_updated": {
        "title": "Хүргэлтийн хаяг шинэчлэгдлээ",
        "body": "Хүргэлтийн байршлыг {changed_by_name} шинэчиллээ: {formatted_address}",
        "icon": "edit_location_alt",
        "urgency": "normal",
    },
    "status_changed": {
        "title": "Захиалгын төлөв өөрчлөгдлөө",
        "body": "Таны #{sales_number} захиалгын төлөв: {status_label}.",
        "icon": "info",
        "urgency": "normal",
    },
}


def _load_templates() -> Dict[str, Dict[str, str]]:
    templates = {key: dict(value) for key, value in _DEFAULT_TEMPLATES.items()}

    raw = os.getenv("NOTIFY_TEMPLATES_JSON", "").strip()
    if raw:
        try:
            override = json.loads(raw)
            if isinstance(override, dict):
                for event_type, fields in override.items():
                    if isinstance(fields, dict):
                        templates.setdefault(str(event_type), {}).update(
                            {k: str(v) for k, v in fields.items()}
                        )
        except ValueError:
            logger.warning("NOTIFY_TEMPLATES_JSON is not valid JSON — ignoring")

    for event_type, fields in templates.items():
        env_prefix = f"NOTIFY_{event_type.upper()}"
        for field in ("title", "body", "icon", "urgency"):
            value = os.getenv(f"{env_prefix}_{field.upper()}")
            if value:
                fields[field] = value

    return templates


# Resolved once per process. Changing wording is a config change plus a restart,
# which is the same lifecycle as every other setting in this service.
_TEMPLATES = _load_templates()


class _SafeDict(dict):
    """Leaves unknown ``{placeholders}`` untouched instead of raising KeyError."""

    def __missing__(self, key: str) -> str:  # pragma: no cover - trivial
        return ""


def _format(template: str, payload: Dict[str, Any]) -> str:
    try:
        return template.format_map(_SafeDict(payload)).strip()
    except Exception:
        logger.warning("Bad notification template: %r", template, exc_info=True)
        return template


def build_notification(
    event_type: str,
    sales_id: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Render the customer-facing notification for an event.

    Returns None for event types that have no customer-facing message, which is
    the signal for the stream to send the event as data-only (the page still
    refreshes, but nothing pops up).
    """
    template = _TEMPLATES.get(event_type)
    if not template:
        return None

    data = dict(payload or {})
    data.setdefault("sales_number", sales_id)
    tracking_code = str(data.get("sales_number") or sales_id)

    return {
        "title": _format(template.get("title", ""), data),
        "body": _format(template.get("body", ""), data),
        "icon": template.get("icon", "notifications"),
        "urgency": template.get("urgency", "normal"),
        "url": f"{TRACKING_URL_PREFIX}{tracking_code}",
        # One notification per order per event type replaces the previous one in
        # the OS tray instead of stacking (requirement 3.2.5).
        "tag": f"deligo-{sales_id}-{event_type}",
    }


# Customer-facing Mongolian label per Deligo wfm status id. Mirrors
# STATUS_LABEL_BY_WFM_ID in the frontend's lib/order-status.ts — kept here so the
# notification body is composed server-side (requirement 3.1.4) rather than
# reconstructed by the browser.
STATUS_LABEL_BY_WFM_ID: Dict[int, str] = {
    1: "Хүлээгдэж байна",
    3: "Хүргэсэн",
    5: "Хуваарилсан",
    8: "Жолооч хүлээн авсан",
    12: "Авахаа больсон",
    13: "Маргааш авна",
    14: "Утсаа аваагүй",
    15: "Дугаар холбогдохгүй",
    16: "Хаягаар очсон",
    17: "Дараа авна",
    23: "Сольж авсан",
}


# Deligo wfm status id -> the event type a customer should be notified about.
# Statuses absent from this map produce a generic `status_changed`.
WFM_STATUS_EVENT_TYPES: Dict[int, str] = {
    3: "delivery_completed",
    8: "driver_accepted",
    12: "order_cancelled",
    13: "delivery_failed",
    14: "delivery_failed",
    15: "delivery_failed",
    16: "delivery_failed",
    17: "delivery_failed",
    23: "status_changed",
}


def event_type_for_status(status_id: int) -> str:
    return WFM_STATUS_EVENT_TYPES.get(int(status_id), "status_changed")

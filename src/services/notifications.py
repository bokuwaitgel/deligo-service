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
import time
from typing import Any, Dict, Optional

from src.services import notification_icon

logger = logging.getLogger(__name__)

# Public tracking link the notification opens when clicked. Same prefix the
# backend already uses to build tracking_url.
TRACKING_URL_PREFIX = os.getenv("TRACKING_URL_PREFIX", "https://map.deligoalpha.mn/track/")


# event type -> {title, body, icon, urgency}
# `icon` is a Material Symbols name; the frontend maps it to both the browser
# notification icon and the in-app toast glyph.
_DEFAULT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "driver_accepted": {
        "title": "Жолооч барааг хүлээн авлаа",
        "body": "Таны хүргэлтийн барааг жолооч хүлээн авч, хүргэлтэд гарлаа.",
        "icon": "local_shipping",
        "urgency": "high",
    },
    # Fired once per order when the driver's remaining queue puts this delivery
    # `NOTIFY_QUEUE_ALERT_POSITION` stops away — see src/services/queue_alerts.py.
    # The count sits inside `{queue_position_text}` rather than being a literal
    # "2" so raising the threshold cannot make the sentence a lie, and so the
    # "no deliveries left before yours" case can be a different clause instead
    # of "0 хүргэлтийн дараа".
    "delivery_queue_near": {
        "title": "Таны ээлж ойртлоо",
        "body": "Таны захиалга {queue_position_text}. Утсаа нээлттэй байлгаарай.",
        "icon": "pending_actions",
        "urgency": "high",
    },
    # Same customer-facing moment as `driver_accepted`, reached by the other
    # path: the driver app starts a route through /api/delivery/{id}/start,
    # while a status set straight to wfm 8 comes through changestatus. Both mean
    # "the driver has your goods and has left", so both carry that sentence —
    # a customer who saw one of them must not later read a different story.
    "delivery_started": {
        "title": "Хүргэлтэд гарлаа",
        "body": "Таны хүргэлтийн барааг жолооч хүлээн авч, хүргэлтэд гарлаа.",
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
        "body": "Таны захиалга амжилттай хүргэгдлээ. Баярлалаа.",
        "icon": "task_alt",
        "urgency": "high",
    },
    # Every "хүргэгдээгүй" status the customer is told about by name, one
    # template each. They were a single generic `delivery_failed` before, which
    # meant the customer read "төлөв: Утсаа аваагүй" instead of a sentence that
    # says what actually happened and what happens next.
    "delivery_no_answer": {
        "title": "Утсаа аваагүй",
        "body": "Жолооч тантай холбогдохыг оролдсон боловч утсаа аваагүй байна.",
        "icon": "phone_missed",
        "urgency": "high",
    },
    "delivery_unreachable": {
        "title": "Дугаар холбогдохгүй",
        "body": "Таны бүртгэлтэй утасны дугаарт холбогдох боломжгүй байна.",
        "icon": "phone_disabled",
        "urgency": "high",
    },
    "delivery_tomorrow": {
        "title": "Маргааш хүргэгдэнэ",
        "body": "Таны захиалгыг маргааш хүлээн авахаар тэмдэглэлээ.",
        "icon": "event_repeat",
        "urgency": "normal",
    },
    "delivery_later": {
        "title": "Дараа хүргэгдэнэ",
        "body": "Таны захиалгыг дараа хүлээн авахаар тэмдэглэлээ.",
        "icon": "more_time",
        "urgency": "normal",
    },
    # The "бусад төлөв" catch-all: name the status the driver picked and append
    # their own note when there is one. `status_description_line` arrives
    # pre-punctuated (or empty) from the publisher — see `describe_status` —
    # because a template cannot express "only if non-empty".
    "delivery_failed": {
        "title": "Хүргэлт амжилтгүй боллоо",
        "body": "Таны захиалгын төлөв: {status_label}.{status_description_line}",
        "icon": "error",
        "urgency": "high",
    },
    "order_cancelled": {
        "title": "Захиалга цуцлагдлаа",
        "body": "Та захиалгаа хүлээн авахаас татгалзсан төлөв бүртгэгдлээ.",
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
        "body": "Таны захиалгын төлөв: {status_label}.{status_description_line}",
        "icon": "info",
        "urgency": "normal",
    },
    # Free-form message sent by an operator from the admin panel. The wording
    # lives entirely in the payload, so this "template" is two placeholders —
    # it exists so a manual message travels the same publish → SSE + push path
    # as every automatic one instead of needing a second delivery mechanism.
    "admin_message": {
        "title": "{admin_title}",
        "body": "{admin_body}",
        "icon": "campaign",
        "urgency": "high",
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


# The env layer, resolved once per process — env genuinely cannot change under a
# running process. Admin edits live in Postgres and are merged on top of this on
# every read; see `_overrides()`.
_TEMPLATES = _load_templates()


# How long a replica may serve notification overrides it read earlier. Every
# API worker keeps its own copy, so this is also the worst-case delay between
# an operator saving a template and it taking effect everywhere. Short, because
# the read is one indexed query against two tiny tables.
_OVERRIDE_TTL = int(os.getenv("NOTIFY_OVERRIDE_TTL", "30"))

# (fetched_at, templates, rules). Seeded empty so the defaults are the behaviour
# until the first successful read.
_override_cache: tuple[float, Dict[str, Dict[str, Any]], Dict[int, Dict[str, Any]]] = (
    0.0,
    {},
    {},
)


def _overrides() -> tuple[Dict[str, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    """Operator edits to wording and to status→notification rules.

    Reads through its own session rather than a request-scoped repository:
    `build_notification` runs on the web push sender's background thread, which
    has no request and therefore no dependency-injected session.

    Never raises. A database that is down must not stop notifications going
    out — callers fall back to the compiled-in defaults, which is exactly the
    behaviour before this feature existed.
    """
    global _override_cache
    fetched_at, templates, rules = _override_cache
    now = time.time()
    if now - fetched_at < _OVERRIDE_TTL:
        return templates, rules

    try:
        from src.dependencies import _get_session_factory
        from src.repositories.notification_override import (
            NotificationRuleOverrideRepository,
            NotificationTemplateOverrideRepository,
        )

        session = _get_session_factory()()
        try:
            templates = NotificationTemplateOverrideRepository(session).as_dict()
            rules = NotificationRuleOverrideRepository(session).as_dict()
        finally:
            session.close()
        _override_cache = (now, templates, rules)
    except Exception:
        # Keep serving the last known good copy (or the empty seed) and retry on
        # the next call rather than hammering a database that is struggling.
        _override_cache = (now, templates, rules)
        logger.warning("Could not load notification overrides — using defaults", exc_info=True)

    return templates, rules


# (fetched_at, {icon_id: origin}). Separate from the override cache because it
# is read on a different trigger — only when a template actually names an image
# — and a template edit must not force a re-read of the icon table.
_icon_origin_cache: tuple[float, Dict[str, str]] = (0.0, {})


def _icon_origins() -> Dict[str, str]:
    """Upload origin per stored icon, for building absolute URLs.

    Same constraints as `_overrides()`: runs on the push sender's background
    thread with no request, and never raises — an icon whose origin cannot be
    read falls back to PUBLIC_API_BASE_URL, and failing that to a relative path
    that at least still renders inside an open tab.
    """
    global _icon_origin_cache
    fetched_at, origins = _icon_origin_cache
    now = time.time()
    if now - fetched_at < _OVERRIDE_TTL:
        return origins

    try:
        from src.dependencies import _get_session_factory
        from src.repositories.notification_icon import NotificationIconRepository

        session = _get_session_factory()()
        try:
            origins = {
                row.id: (row.origin or "")
                for row in NotificationIconRepository(session).list_all(limit=200)
            }
        finally:
            session.close()
    except Exception:
        logger.warning("Could not load notification icon origins", exc_info=True)
    _icon_origin_cache = (now, origins)
    return origins


def invalidate_override_cache() -> None:
    """Drop this worker's cached overrides so the next read hits the database.

    Called by the admin endpoints after a write so the operator's own next
    request reflects the edit immediately. Other replicas still converge on
    their own TTL — there is no cross-process invalidation channel here, and
    `_OVERRIDE_TTL` is the bound on that.
    """
    global _override_cache, _icon_origin_cache
    _override_cache = (0.0, _override_cache[1], _override_cache[2])
    # An icon uploaded a second ago is referenced by the template saved in the
    # same breath, so the two caches expire together.
    _icon_origin_cache = (0.0, _icon_origin_cache[1])


def resolved_templates() -> Dict[str, Dict[str, Any]]:
    """Defaults + env + operator overrides — what will actually be sent."""
    templates, _ = _overrides()
    merged: Dict[str, Dict[str, Any]] = {
        key: dict(value) for key, value in _TEMPLATES.items()
    }
    for event_type, fields in templates.items():
        target = merged.setdefault(str(event_type), {})
        for field, value in fields.items():
            # `_updated_at` / `_updated_by` are provenance for the admin panel,
            # not template fields — they must not leak into the rendered copy.
            if not field.startswith("_") and value is not None:
                target[field] = value
    return merged


def template_provenance() -> Dict[str, Dict[str, Any]]:
    """Per event type: which fields an operator changed, and when.

    Lets the admin panel mark a field as edited-vs-inherited without diffing
    the rendered copy against a second request for the defaults.
    """
    templates, _ = _overrides()
    result: Dict[str, Dict[str, Any]] = {}
    for event_type, fields in templates.items():
        result[str(event_type)] = {
            "overridden": sorted(f for f in fields if not f.startswith("_")),
            "updated_at": fields.get("_updated_at"),
            "updated_by": fields.get("_updated_by"),
        }
    return result


def default_templates() -> Dict[str, Dict[str, str]]:
    """The compiled-in copy, before any operator edit — for the revert preview."""
    return {key: dict(value) for key, value in _DEFAULT_TEMPLATES.items()}


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


def all_templates() -> Dict[str, Dict[str, str]]:
    """Every template that will actually be sent, for the admin panel.

    Returns a copy: the map is process-wide state that callers must not mutate.
    """
    return resolved_templates()


def build_notification(
    event_type: str,
    sales_id: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Render the customer-facing notification for an event.

    Returns None for event types that have no customer-facing message, which is
    the signal for the stream to send the event as data-only (the page still
    refreshes, but nothing pops up). An operator muting a status in the admin
    panel produces the same None, and therefore the same data-only behaviour.
    """
    data = dict(payload or {})

    # Mute is enforced here rather than at the publish site so it covers both
    # delivery paths at once: the SSE stream and the web push sender each call
    # this function, and neither should announce a status the operator silenced.
    if _is_status_muted(data.get("wfm_status_id")):
        return None

    template = resolved_templates().get(event_type)
    if not template:
        return None
    data.setdefault("sales_number", sales_id)
    tracking_code = str(data.get("sales_number") or sales_id)

    # An uploaded image, if the publisher or the template names one. Resolved to
    # an absolute URL here rather than stored as one, so the id in the database
    # can only ever address our own icon route.
    image_id = data.get("notification_icon_image_id") or template.get("icon_image_id")
    image_url = (
        notification_icon.icon_url(image_id, _icon_origins().get(str(image_id)))
        if notification_icon.is_valid_icon_id(image_id)
        else None
    )

    return {
        "title": _format(template.get("title", ""), data),
        "body": _format(template.get("body", ""), data),
        # A publisher may override the glyph and priority per event — used by the
        # admin panel's manual message, where the operator picks both. Absent
        # from the payload (the normal case) the template's values stand.
        "icon": data.get("notification_icon") or template.get("icon", "notifications"),
        # Kept beside `icon`, never instead of it: the glyph is the fallback for
        # every surface that cannot show the image (iOS, the in-app toast before
        # the image loads, a failed fetch).
        "icon_url": image_url,
        "urgency": data.get("notification_urgency") or template.get("urgency", "normal"),
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
#
# The flow the customer sees is: driver_accepted ("хүргэлтэд гарлаа") →
# delivery_queue_near ("2 хүргэлтийн дараа") → delivery_completed, or one of the
# named "хүргэгдээгүй" reasons. Only the reasons Deligo has a distinct status for
# get their own template; 16 ("Хаягаар очсон") and anything unmapped fall to the
# generic templates, which print the status name plus the driver's note.
WFM_STATUS_EVENT_TYPES: Dict[int, str] = {
    3: "delivery_completed",
    8: "driver_accepted",
    12: "order_cancelled",
    13: "delivery_tomorrow",
    14: "delivery_no_answer",
    15: "delivery_unreachable",
    16: "delivery_failed",
    17: "delivery_later",
    23: "status_changed",
}


def describe_status(status_description: Any) -> str:
    """The trailing " Тайлбар: …" clause for the generic templates, or "".

    A template cannot say "include this only when it is set" — `_SafeDict` turns
    a missing key into an empty string but leaves the label and punctuation
    around it, so an order with no note would render "төлөв: Хаягаар очсон.
    Тайлбар:". Publishers therefore pass the whole clause, already punctuated.
    """
    text = str(status_description or "").strip()
    return f" Тайлбар: {text}" if text else ""


def describe_queue_position(position: int) -> str:
    """"2 хүргэлтийн дараа хүргэгдэхээр байна" — or the zero-deliveries wording.

    Same reason as `describe_status`: the sentence changes shape at 0, and a
    format string cannot branch. An order with nothing ahead of it is next, not
    "0 хүргэлтийн дараа".
    """
    if position <= 0:
        return "дараагийн хүргэлт байна"
    return f"{position} хүргэлтийн дараа хүргэгдэхээр байна"


def _is_status_muted(status_id: Any) -> bool:
    """True when an operator turned notifications off for this wfm status."""
    if status_id is None:
        return False
    try:
        key = int(status_id)
    except (TypeError, ValueError):
        return False
    _, rules = _overrides()
    rule = rules.get(key)
    return bool(rule and rule.get("muted"))


def event_type_for_status(status_id: int) -> str:
    """Which notification a status change announces.

    An operator's rule wins over ``WFM_STATUS_EVENT_TYPES``; a status nobody has
    touched keeps the compiled-in mapping, and one that is in neither falls back
    to the generic ``status_changed``.

    A muted status still resolves to an event type — the event is published
    either way so the tracking page refreshes; ``build_notification`` is what
    withholds the visible message.
    """
    key = int(status_id)
    _, rules = _overrides()
    rule = rules.get(key)
    if rule and rule.get("event_type"):
        return str(rule["event_type"])
    return WFM_STATUS_EVENT_TYPES.get(key, "status_changed")


def all_rules() -> Dict[int, Dict[str, Any]]:
    """Every status → notification mapping, defaults merged with operator edits.

    Keyed by wfm status id and covering every status the notification system
    knows a label for, so the admin table can list them all rather than only
    the ones somebody already edited.
    """
    _, rules = _overrides()
    result: Dict[int, Dict[str, Any]] = {}
    for status_id, label in STATUS_LABEL_BY_WFM_ID.items():
        default_event = WFM_STATUS_EVENT_TYPES.get(status_id, "status_changed")
        rule = rules.get(status_id) or {}
        result[status_id] = {
            "wfm_status_id": status_id,
            "status_label": label,
            "default_event_type": default_event,
            "event_type": rule.get("event_type") or default_event,
            "muted": bool(rule.get("muted")),
            "overridden": bool(rule.get("event_type")) or bool(rule.get("muted")),
            "updated_at": rule.get("_updated_at"),
            "updated_by": rule.get("_updated_by"),
        }
    return result

"""Web Push (RFC 8030 / VAPID) delivery for customer order notifications.

Why this exists on top of the SSE stream
----------------------------------------
``src/services/events.py`` + ``/api/events/order/{id}/stream`` only reach a
customer whose tracking tab is **open and awake**. Close the tab, lock the
phone, or switch apps and the ``EventSource`` dies — so "жолооч ойртож байна"
arrives to nobody. Web Push is delivered by the browser vendor's push service to
the registered service worker whether or not our page is running, which is the
only mechanism that survives a closed tab.

The two live side by side on purpose: the open page shows the toast instantly
via SSE, and the same event goes out as a push for whoever is not looking. Both
carry the same ``tag`` (``deligo-{sales_id}-{event_type}``), so when both land
the OS replaces rather than stacks them.

Sending model
-------------
``pywebpush`` is blocking (``requests``), and publishers are synchronous request
handlers that must not wait on a third-party push service. Every send is
therefore handed to a small thread pool and the caller returns immediately.
Failures are logged, never raised: a notification must not be able to fail an
address save or a status change.

Only the *publishing* worker sends. The Redis fan-out in ``events.py`` re-
delivers events to the other API workers for their SSE subscribers; hooking push
into that fan-out instead would send one push per worker.

Configuration
-------------
``VAPID_PUBLIC_KEY``   base64url raw P-256 public key (also handed to the browser)
``VAPID_PRIVATE_KEY``  base64url raw P-256 private key
``VAPID_SUBJECT``      ``mailto:`` or ``https:`` contact for the push service
``WEB_PUSH_TTL``       seconds the push service may hold an undelivered message

Generate a key pair with ``python scripts/generate_vapid_keys.py``. With no keys
configured every function here becomes a no-op and the rest of the service is
unaffected — the tracking page falls back to SSE-only, which is what it did
before this module existed.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "").strip()
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:admin@deligo.mn").strip()
WEB_PUSH_TTL = int(os.getenv("WEB_PUSH_TTL", "1800"))
# Small pool: sends are network-bound and the volume is one push per order event.
_MAX_SEND_WORKERS = int(os.getenv("WEB_PUSH_WORKERS", "4"))

# Event types that are pure page-refresh signals — the tracking page redraws the
# marker, but waking the phone for every GPS ping would be spam. They still go
# out over SSE.
PUSH_SUPPRESSED_EVENT_TYPES = {"driver_location"}

_executor: Optional[ThreadPoolExecutor] = None
_missing_config_logged = False


def is_configured() -> bool:
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)


def public_key() -> str:
    """The applicationServerKey the browser needs to create a subscription."""
    return VAPID_PUBLIC_KEY


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=_MAX_SEND_WORKERS, thread_name_prefix="webpush"
        )
        atexit.register(lambda: _executor and _executor.shutdown(wait=False))
    return _executor


def _warn_unconfigured_once() -> None:
    global _missing_config_logged
    if not _missing_config_logged:
        logger.info(
            "VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY not set — web push is disabled "
            "(the SSE stream still delivers to open tabs)"
        )
        _missing_config_logged = True


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


def _send_one(subscription_info: Dict[str, Any], payload: Dict[str, Any], urgency: str) -> int:
    """Deliver one payload. Returns the push service's HTTP status.

    Raises ``WebPushException`` — the caller translates 404/410 into "delete this
    subscription" and everything else into a soft failure.
    """
    from pywebpush import webpush  # type: ignore[import-not-found]  # lazy: optional dep

    response = webpush(
        subscription_info=subscription_info,
        # Bytes, not str: pywebpush >= 2 hands `data` straight to http_ece,
        # which concatenates it with a byte padding delimiter and raises
        # TypeError on a str. Mongolian copy makes the UTF-8 encoding explicit
        # rather than incidental.
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        vapid_private_key=VAPID_PRIVATE_KEY,
        vapid_claims={"sub": VAPID_SUBJECT},
        ttl=WEB_PUSH_TTL,
        headers={
            # Lets the push service defer low-priority messages while the device
            # is asleep instead of waking it for a location refresh.
            "Urgency": urgency if urgency in ("very-low", "low", "normal", "high") else "normal",
            # Same collapse key as the Notification `tag`: an undelivered
            # "delivery_started" is replaced by a newer one for the same order.
            "Topic": str(payload.get("tag", ""))[:32] or "deligo",
        },
    )
    return int(getattr(response, "status_code", 0) or 0)


def _settle_log(event_id: Optional[str], sent: int, failed: int) -> None:
    """Close out the admin panel's history row for one event.

    Until this lands the row reads "Хүлээгдэж байна..." — which is correct while
    a send is in flight and a bug once nothing more will happen. Every path that
    ends a send, including the ones that never start one, has to come through
    here. Opens its own session and never raises: a reporting gap must not take
    down the sender.
    """
    if not event_id:
        return
    try:
        from src.dependencies import _get_session_factory
        from src.repositories.notification_log import NotificationLogRepository

        session = _get_session_factory()()
        try:
            NotificationLogRepository(session).mark_push_result(event_id, sent, failed)
        finally:
            session.close()
    except Exception:
        logger.warning("Could not record push result for event %s", event_id, exc_info=True)


def settle_without_send(event_id: Optional[str]) -> None:
    """Mark a logged notification as finished with no push attempted.

    Used when push is switched off, or when the order has no registered device:
    SSE still delivered it to an open tab, so this is a settled zero, not a
    pending send.
    """
    _settle_log(event_id, 0, 0)


def _dispatch(
    sales_id: str,
    payload: Dict[str, Any],
    urgency: str,
    event_id: Optional[str] = None,
) -> None:
    """Runs on a pool thread. Owns its own DB session.

    ``event_id`` is only used to update the admin panel's history row with the
    delivery outcome; a send with no id still goes out normally.
    """
    from src.dependencies import _get_session_factory
    from src.repositories.push_subscription import PushSubscriptionRepository

    session = None
    sent = 0
    failed = 0
    try:
        session = _get_session_factory()()
        repo = PushSubscriptionRepository(session)
        subscriptions = repo.list_for_sales_id(sales_id)
        if not subscriptions:
            return

        from pywebpush import WebPushException  # type: ignore[import-not-found]

        for row in subscriptions:
            info = {
                "endpoint": row.endpoint,
                "keys": {"p256dh": row.p256dh, "auth": row.auth},
            }
            try:
                _send_one(info, payload, urgency)
                sent += 1
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                failed += 1
                if status in (404, 410):
                    # The browser dropped this subscription (cleared site data,
                    # revoked permission, uninstalled the PWA). It will never
                    # accept another message — stop trying.
                    repo.delete_endpoint(row.endpoint, sales_id)
                    logger.info(
                        "Pruned expired push subscription for sales_id=%s (HTTP %s)",
                        sales_id, status,
                    )
                else:
                    repo.mark_failure(row.id)
                    logger.warning(
                        "Web push send failed for sales_id=%s (HTTP %s)",
                        sales_id, status, exc_info=True,
                    )
            except Exception:
                failed += 1
                repo.mark_failure(row.id)
                logger.warning("Web push send raised for sales_id=%s", sales_id, exc_info=True)

        if sent:
            logger.info("Web push delivered to %d subscriber(s) for sales_id=%s", sent, sales_id)
    except Exception:
        logger.warning("Web push dispatch failed for sales_id=%s", sales_id, exc_info=True)
    finally:
        if session is not None:
            session.close()
        # Settled in `finally` so every exit closes the history row — no
        # subscriptions, a mid-loop crash, or a clean run all report what
        # actually happened instead of leaving the row pending forever. Uses its
        # own session because this one may be in a failed transaction.
        _settle_log(event_id, sent, failed)


def send_to_order(
    sales_id: str,
    notification: Dict[str, Any],
    *,
    event_id: Optional[str] = None,
    event_type: Optional[str] = None,
) -> None:
    """Queue a push of ``notification`` to every browser registered for the order.

    Returns immediately; never raises. ``notification`` is the dict produced by
    ``services.notifications.build_notification`` — the server owns every piece
    of visible copy, and the service worker renders it verbatim.
    """
    if not is_configured():
        _warn_unconfigured_once()
        settle_without_send(event_id)
        return
    sid = str(sales_id or "").strip()
    if not sid or not notification:
        settle_without_send(event_id)
        return

    payload = {
        "title": notification.get("title") or "Deligo",
        "body": notification.get("body") or "",
        "icon": notification.get("icon") or "notifications",
        # Absolute URL of an uploaded image, or None to let the service worker
        # fall back to the app logo. Must be absolute: the push service fetches
        # it from the customer's device, not from our page.
        "icon_url": notification.get("icon_url") or None,
        "urgency": notification.get("urgency") or "normal",
        "url": notification.get("url") or "",
        "tag": notification.get("tag") or f"deligo-{sid}",
        "sales_id": sid,
        # Carried so the service worker and the open page can recognise the same
        # event and avoid showing it twice.
        "event_id": event_id,
        "event_type": event_type,
    }
    try:
        _get_executor().submit(_dispatch, sid, payload, str(payload["urgency"]), event_id)
    except Exception:
        # Nothing was queued, so nothing will ever settle this row.
        logger.warning("Could not queue web push for sales_id=%s", sales_id, exc_info=True)
        settle_without_send(event_id)


def send_event(sales_id: str, event_type: str, event_id: str, data: Dict[str, Any]) -> None:
    """Push the customer-facing message for one order event, if it has one."""
    if not is_configured():
        # No push will ever be attempted for this event, so close its history row
        # now. Without this the admin panel shows a permanent "Хүлээгдэж
        # байна..." on every notification of a deployment that runs SSE-only.
        settle_without_send(event_id)
        return
    if event_type in PUSH_SUPPRESSED_EVENT_TYPES:
        # Not logged in the first place (see events._record_sent_notification),
        # so there is no row to settle.
        return

    from src.services.notifications import build_notification

    notification = build_notification(event_type, str(sales_id), data or {})
    if not notification:
        # No customer-facing copy for this event type — data-only, SSE handles
        # it, and nothing was logged either.
        return
    send_to_order(sales_id, notification, event_id=event_id, event_type=event_type)


def subscriptions_for(sales_id: str) -> List[Dict[str, Any]]:
    """Debug helper: what is registered for this order (no keys returned)."""
    from src.dependencies import _get_session_factory
    from src.repositories.push_subscription import PushSubscriptionRepository

    session = _get_session_factory()()
    try:
        rows = PushSubscriptionRepository(session).list_for_sales_id(str(sales_id))
        return [
            {
                "id": row.id,
                "endpoint_host": row.endpoint.split("/")[2] if "//" in row.endpoint else "",
                "user_agent": row.user_agent,
                "failure_count": row.failure_count,
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            }
            for row in rows
        ]
    finally:
        session.close()

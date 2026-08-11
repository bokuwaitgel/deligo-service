"""Web Push subscription management for the customer tracking page.

Public, like the rest of the tracking surface (``GET /api/delivery/tracking/
{sales_number}``, ``GET /api/events/order/{sales_id}/stream``): holding the
tracking link is the capability. Registering a push endpoint for an order the
caller can already read grants no extra access — the pushed content is the same
copy the SSE stream already sends them.

Flow the browser follows:

1. ``GET  /api/push/public-key``  → applicationServerKey for ``pushManager.subscribe``
2. ``POST /api/push/subscribe``   → store the endpoint against this order
3. ``POST /api/push/unsubscribe`` → drop it when the customer turns notifications off
4. ``POST /api/push/test/{sales_id}`` → end-to-end check with a real push
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.auth_utils import require_api_key
from src.dependencies import get_push_subscription_repository
from src.repositories.push_subscription import PushSubscriptionRepository
from src.services import webpush
from src.services.events import publish_order_event
from src.services.notifications import (
    STATUS_LABEL_BY_WFM_ID,
    TRACKING_URL_PREFIX,
    WFM_STATUS_EVENT_TYPES,
    all_templates,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/push", tags=["push"])


class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(..., description="Client public key from PushSubscription.getKey('p256dh')")
    auth: str = Field(..., description="Auth secret from PushSubscription.getKey('auth')")


class PushSubscriptionPayload(BaseModel):
    """The browser's ``PushSubscription.toJSON()``, passed through unchanged."""

    endpoint: str
    keys: PushSubscriptionKeys
    expirationTime: Optional[float] = None


class SubscribeRequest(BaseModel):
    sales_id: str = Field(..., description="Order the browser wants notifications for")
    subscription: PushSubscriptionPayload


class UnsubscribeRequest(BaseModel):
    endpoint: str
    # Omit to remove this browser from every order it registered for.
    sales_id: Optional[str] = None


@router.get("/public-key")
def get_public_key():
    """VAPID application server key, or ``enabled: false`` when push is off.

    The frontend must handle ``enabled: false`` by staying on the SSE-only path
    rather than erroring: a deployment without VAPID keys is a valid one.
    """
    return {
        "status": "ok",
        "data": {
            "enabled": webpush.is_configured(),
            "public_key": webpush.public_key(),
        },
    }


@router.post("/subscribe", status_code=201)
def subscribe(
    payload: SubscribeRequest,
    user_agent: str = Header(default="", alias="User-Agent"),
    repo: PushSubscriptionRepository = Depends(get_push_subscription_repository),
):
    """Register this browser's push endpoint for one order (idempotent)."""
    sales_id = payload.sales_id.strip()
    if not sales_id:
        raise HTTPException(status_code=400, detail="sales_id is required")
    if not webpush.is_configured():
        # Storing subscriptions we can never send to would just accumulate rows.
        raise HTTPException(status_code=503, detail="Web push is not configured on this server")

    endpoint = payload.subscription.endpoint.strip()
    if not endpoint.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="Invalid push endpoint")

    row = repo.upsert(
        sales_id=sales_id,
        endpoint=endpoint,
        p256dh=payload.subscription.keys.p256dh,
        auth=payload.subscription.keys.auth,
        user_agent=(user_agent or None),
    )
    logger.info("Push subscription registered for sales_id=%s", sales_id)
    return {"status": "ok", "data": {"id": row.id, "sales_id": row.sales_id}}


@router.post("/unsubscribe")
def unsubscribe(
    payload: UnsubscribeRequest,
    repo: PushSubscriptionRepository = Depends(get_push_subscription_repository),
):
    """Forget a push endpoint. Never 404s — unsubscribing twice is fine."""
    endpoint = payload.endpoint.strip()
    if not endpoint:
        raise HTTPException(status_code=400, detail="endpoint is required")
    removed = repo.delete_endpoint(endpoint, payload.sales_id)
    return {"status": "ok", "data": {"removed": removed}}


@router.post("/test/{sales_id}")
def send_test_push(sales_id: str):
    """Send a real push to every browser registered for this order.

    Unauthenticated for the same reason ``/api/events/order/{id}/test`` is: it
    only touches the single order whose tracking id the caller already holds and
    changes no stored state.
    """
    sid = str(sales_id).strip()
    if not sid:
        raise HTTPException(status_code=400, detail="sales_id is required")
    if not webpush.is_configured():
        raise HTTPException(status_code=503, detail="Web push is not configured on this server")

    webpush.send_to_order(
        sid,
        {
            "title": "Deligo туршилтын мэдэгдэл",
            "body": "Мэдэгдэл амжилттай ажиллаж байна.",
            "icon": "notifications",
            "urgency": "normal",
            "url": f"{TRACKING_URL_PREFIX}{sid}",
            "tag": f"deligo-{sid}-test",
        },
        event_type="push_test",
    )
    return {"status": "ok", "data": {"queued": True}}


@router.get("/subscriptions/{sales_id}", dependencies=[Depends(require_api_key)])
def list_subscriptions(sales_id: str):
    """Operator view of what is registered for an order. Keys are never returned."""
    return {"status": "ok", "data": webpush.subscriptions_for(str(sales_id))}


# ---------------------------------------------------------------------------
# Admin panel — notification tab
# ---------------------------------------------------------------------------


@router.get("/overview", dependencies=[Depends(require_api_key)])
def overview(
    limit: int = Query(25, ge=1, le=200),
    repo: PushSubscriptionRepository = Depends(get_push_subscription_repository),
):
    """Health of the notification system, for the admin panel's tab.

    Answers the two questions an operator actually has: is push switched on at
    all, and which orders can currently be reached.
    """
    return {
        "status": "ok",
        "data": {
            "push_enabled": webpush.is_configured(),
            "vapid_public_key": webpush.public_key(),
            "ttl_seconds": webpush.WEB_PUSH_TTL,
            "tracking_url_prefix": TRACKING_URL_PREFIX,
            "total_subscriptions": repo.count(),
            "orders": repo.recent_orders(limit=limit),
        },
    }


@router.get("/templates", dependencies=[Depends(require_api_key)])
def list_templates():
    """The server-side copy for every event type.

    Read-only on purpose. Wording is configuration (``NOTIFY_<EVENT>_TITLE`` /
    ``NOTIFY_TEMPLATES_JSON``, resolved once per process), so an editable panel
    here would either lie about what is live on the other API workers or need a
    write path that survives a restart. The panel shows what will be sent and
    names the environment variable to change it.
    """
    templates = all_templates()
    return {
        "status": "ok",
        "data": {
            "templates": [
                {
                    "event_type": event_type,
                    # `admin_message` is excluded below — it is the manual-send
                    # passthrough, not an automatic notification, and its
                    # "{admin_title}" placeholders would read as broken copy.
                    "title": fields.get("title", ""),
                    "body": fields.get("body", ""),
                    "icon": fields.get("icon", "notifications"),
                    "urgency": fields.get("urgency", "normal"),
                    # The env var that overrides this entry, spelled out so the
                    # operator does not have to guess the casing.
                    "env_prefix": f"NOTIFY_{event_type.upper()}",
                }
                for event_type, fields in sorted(templates.items())
                if event_type != "admin_message"
            ],
            "status_labels": STATUS_LABEL_BY_WFM_ID,
            "status_event_types": WFM_STATUS_EVENT_TYPES,
        },
    }


class AdminMessageRequest(BaseModel):
    sales_id: str
    title: str = Field(..., min_length=1, max_length=120)
    body: str = Field(..., min_length=1, max_length=400)
    icon: str = Field(default="campaign", max_length=64)
    urgency: str = Field(default="high", pattern="^(low|normal|high)$")


@router.post("/send", dependencies=[Depends(require_api_key)])
def send_admin_message(
    payload: AdminMessageRequest,
    repo: PushSubscriptionRepository = Depends(get_push_subscription_repository),
):
    """Send an operator-written message to one order's customer.

    Goes out through ``publish_order_event`` rather than calling the push sender
    directly, so it takes the exact same road as every automatic notification:
    the open tracking tab shows it over SSE, and the closed one gets it as a
    push. ``admin_message`` is the template that renders the two free-text
    fields (see services/notifications.py).
    """
    sales_id = payload.sales_id.strip()
    if not sales_id:
        raise HTTPException(status_code=400, detail="sales_id is required")

    devices = repo.count_for_sales_id(sales_id)
    publish_order_event(
        sales_id,
        "admin_message",
        {
            "sales_number": sales_id,
            "admin_title": payload.title.strip(),
            "admin_body": payload.body.strip(),
            "notification_icon": payload.icon.strip() or "campaign",
            "notification_urgency": payload.urgency,
        },
    )
    logger.info("Admin notification published for sales_id=%s (%d device(s))", sales_id, devices)
    return {
        "status": "ok",
        "data": {
            "sales_id": sales_id,
            # How many browsers the push half can reach. Zero is not an error:
            # an open tracking tab still receives it over SSE.
            "push_devices": devices,
            "push_enabled": webpush.is_configured(),
        },
    }

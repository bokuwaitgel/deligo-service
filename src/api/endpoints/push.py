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
import os
import re
from string import Formatter
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from src.api.auth_utils import require_api_key
from src.dependencies import (
    get_notification_icon_repository,
    get_notification_log_repository,
    get_notification_rule_override_repository,
    get_notification_template_override_repository,
    get_push_subscription_repository,
)
from src.repositories.notification_icon import NotificationIconRepository
from src.repositories.notification_log import NotificationLogRepository
from src.repositories.notification_override import (
    EDITABLE_TEMPLATE_FIELDS,
    VALID_URGENCIES,
    NotificationRuleOverrideRepository,
    NotificationTemplateOverrideRepository,
)
from src.repositories.push_subscription import PushSubscriptionRepository
from src.services import notification_icon, webpush
from src.services.events import publish_order_event
from src.services.notifications import (
    STATUS_LABEL_BY_WFM_ID,
    TRACKING_URL_PREFIX,
    WFM_STATUS_EVENT_TYPES,
    all_rules,
    all_templates,
    default_templates,
    invalidate_override_cache,
    template_provenance,
)

# Placeholders a template may reference. Anything else renders as an empty
# string at send time (`_SafeDict.__missing__`), so it is rejected on save
# instead — a silently blank sentence is worse than a rejected edit.
#
# Kept in sync with what the publishers actually put in the payload:
# `sales_number` is set by build_notification itself; the status fields come
# from auth.py's status-change publish; distance from the driver_nearby check;
# the address pair from the address-update publish; admin_* from manual sends.
# How long sent-notification history is kept. Long enough to answer "what did
# you tell my customer last week", short enough that the table stays small
# without a scheduled job.
NOTIFICATION_LOG_RETENTION_DAYS = int(os.getenv("NOTIFICATION_LOG_RETENTION_DAYS", "30"))

KNOWN_PLACEHOLDERS = {
    "sales_number",
    "status_label",
    "status_description",
    "wfm_status_id",
    "distance_text",
    "formatted_address",
    "changed_by_name",
    "admin_title",
    "admin_body",
}

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
# Uploaded notification icons
# ---------------------------------------------------------------------------


def _icon_row_json(row, *, url: Optional[str] = None) -> dict:
    """Metadata for the admin picker. The image bytes are never inlined here."""
    return {
        "id": row.id,
        "url": url if url is not None else notification_icon.icon_url(row.id, row.origin),
        "label": row.label,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "width": row.width,
        "height": row.height,
        "uploaded_by": row.uploaded_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("/icons", status_code=201, dependencies=[Depends(require_api_key)])
async def upload_icon(
    request: Request,
    file: UploadFile = File(..., description="PNG, JPEG or WebP image"),
    repo: NotificationIconRepository = Depends(get_notification_icon_repository),
):
    """Store an image an operator picked, normalised to a 192px PNG.

    The upload is decoded and re-encoded before it is stored — see
    ``services/notification_icon.py`` for why that is a security requirement
    and not a convenience. The returned id is the sha256 of the stored bytes,
    so uploading the same logo twice yields the same row and the same URL.
    """
    raw = await file.read()
    try:
        icon_id, png, width, height = notification_icon.normalize_upload(
            raw, filename=file.filename
        )
    except notification_icon.IconValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    row = repo.store(
        icon_id=icon_id,
        data=png,
        content_type=notification_icon.STORED_CONTENT_TYPE,
        width=width,
        height=height,
        label=notification_icon.safe_label(file.filename),
        origin=str(request.base_url).rstrip("/"),
        uploaded_by=None,
    )
    logger.info("Notification icon stored: id=%s %dx%d %dB", row.id, width, height, len(png))
    return {"status": "ok", "data": _icon_row_json(row)}


@router.get("/icons", dependencies=[Depends(require_api_key)])
def list_icons(
    limit: int = Query(60, ge=1, le=200),
    repo: NotificationIconRepository = Depends(get_notification_icon_repository),
):
    """Everything available in the admin panel's icon picker, newest first."""
    return {"status": "ok", "data": {"icons": [_icon_row_json(r) for r in repo.list_all(limit)]}}


@router.get("/icons/{icon_id}")
def get_icon(
    icon_id: str,
    repo: NotificationIconRepository = Depends(get_notification_icon_repository),
):
    """Serve one stored icon.

    Public, and necessarily so: the fetcher is the customer's browser (or their
    push service) rendering a notification, which carries no API key. The bytes
    are the operator's own uploaded logo, and the row id has to be known to ask
    for it.

    Immutable rows mean this can be cached hard, which keeps repeat
    notifications from re-fetching the same image. The headers are the security
    half of the re-encoding done at upload: an exact ``Content-Type`` with
    ``nosniff`` so nothing can be coaxed into being interpreted as a document,
    and a CSP that neuters the response even if it somehow were.
    """
    if not notification_icon.is_valid_icon_id(icon_id):
        raise HTTPException(status_code=404, detail="Icon not found")
    row = repo.get(icon_id.strip())
    if row is None:
        raise HTTPException(status_code=404, detail="Icon not found")

    return Response(
        content=row.data,
        media_type=row.content_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "Content-Disposition": "inline",
        },
    )


@router.delete("/icons/{icon_id}", dependencies=[Depends(require_api_key)])
def delete_icon(
    icon_id: str,
    repo: NotificationIconRepository = Depends(get_notification_icon_repository),
    template_repo: NotificationTemplateOverrideRepository = Depends(
        get_notification_template_override_repository
    ),
):
    """Delete an icon, and drop it from any template still pointing at it.

    Clearing the references is the point: a template left naming a deleted icon
    would resolve to a URL that 404s, and the customer would get a notification
    with no image and no glyph to fall back to.
    """
    if not notification_icon.is_valid_icon_id(icon_id):
        raise HTTPException(status_code=404, detail="Icon not found")
    sid = icon_id.strip()

    cleared = []
    for row in template_repo.list_all():
        if row.icon_image_id == sid:
            template_repo.upsert(row.event_type, {"icon_image_id": None})
            cleared.append(row.event_type)

    removed = repo.delete(sid)
    if cleared:
        invalidate_override_cache()
        logger.info("Icon %s deleted; cleared from templates %s", sid, cleared)
    return {"status": "ok", "data": {"removed": removed, "cleared_templates": cleared}}


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
def list_templates(
    icon_repo: NotificationIconRepository = Depends(get_notification_icon_repository),
):
    """The copy that will actually be sent, for every event type.

    Three layers are resolved before this returns: the compiled-in defaults,
    the ``NOTIFY_*`` environment overrides, and the operator's edits stored in
    Postgres. ``defaults`` and ``overridden`` are carried alongside so the panel
    can mark a field as edited and offer a revert without a second request.
    """
    templates = all_templates()
    defaults = default_templates()
    provenance = template_provenance()
    # One read for every icon a template names, rather than one per template.
    icon_urls = {row.id: notification_icon.icon_url(row.id, row.origin) for row in icon_repo.list_all(200)}
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
                    # The uploaded image, if this template names one. `icon`
                    # above stays the fallback glyph and is never replaced.
                    "icon_image_id": fields.get("icon_image_id"),
                    "icon_url": icon_urls.get(fields.get("icon_image_id")),
                    "urgency": fields.get("urgency", "normal"),
                    # The env var that overrides this entry, spelled out so the
                    # operator does not have to guess the casing. Note a saved
                    # edit outranks it — see notifications.resolved_templates.
                    "env_prefix": f"NOTIFY_{event_type.upper()}",
                    "default": defaults.get(event_type, {}),
                    "overridden": provenance.get(event_type, {}).get("overridden", []),
                    "updated_at": provenance.get(event_type, {}).get("updated_at"),
                    "updated_by": provenance.get(event_type, {}).get("updated_by"),
                }
                for event_type, fields in sorted(templates.items())
                if event_type != "admin_message"
            ],
            "status_labels": STATUS_LABEL_BY_WFM_ID,
            "status_event_types": WFM_STATUS_EVENT_TYPES,
        },
    }


# Material Symbols names only — the frontend renders this straight into a
# `material-symbols-outlined` span, so anything else silently draws nothing.
_ICON_RE = re.compile(r"^[a-z0-9_]{1,64}$")


class TemplateOverrideRequest(BaseModel):
    """A partial edit. Absent field = leave alone, null/blank = revert to default."""

    title: Optional[str] = Field(default=None, max_length=200)
    body: Optional[str] = Field(default=None, max_length=600)
    icon: Optional[str] = Field(default=None, max_length=64)
    # sha256 of an uploaded icon, or blank to go back to the glyph alone. The
    # id is checked against the icons table in the handler — a template naming
    # an image that is not there would send a broken URL to every customer.
    icon_image_id: Optional[str] = Field(default=None, max_length=64)
    urgency: Optional[str] = Field(default=None)
    updated_by: Optional[str] = Field(default=None, max_length=120)

    @field_validator("icon")
    @classmethod
    def _check_icon(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value.strip() == "":
            return value
        name = value.strip()
        if not _ICON_RE.match(name):
            raise ValueError(
                "icon must be a Material Symbols name — lowercase letters, digits "
                "and underscores only (e.g. local_shipping)"
            )
        return name

    @field_validator("urgency")
    @classmethod
    def _check_urgency(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value.strip() == "":
            return value
        urgency = value.strip()
        if urgency not in VALID_URGENCIES:
            raise ValueError(f"urgency must be one of {', '.join(VALID_URGENCIES)}")
        return urgency

    @field_validator("title", "body")
    @classmethod
    def _check_placeholders(cls, value: Optional[str]) -> Optional[str]:
        """Reject a template that would raise or render as literal braces.

        An operator typing `{sales_number` (unclosed) or `{price}` (never in the
        payload) would otherwise only find out when a real customer gets the
        broken copy — `_format` swallows both at send time.
        """
        if value is None or value.strip() == "":
            return value
        text = value.strip()
        try:
            fields = {f for _, f, _, _ in Formatter().parse(text) if f}
        except ValueError as exc:
            raise ValueError(f"unbalanced {{}} in the template: {exc}") from exc
        unknown = sorted(f for f in fields if f.split(".")[0].split("[")[0] not in KNOWN_PLACEHOLDERS)
        if unknown:
            raise ValueError(
                f"unknown placeholder(s): {', '.join('{' + u + '}' for u in unknown)}. "
                f"Available: {', '.join('{' + k + '}' for k in sorted(KNOWN_PLACEHOLDERS))}"
            )
        return text


@router.put("/templates/{event_type}", dependencies=[Depends(require_api_key)])
def upsert_template_override(
    event_type: str,
    payload: TemplateOverrideRequest,
    repo: NotificationTemplateOverrideRepository = Depends(
        get_notification_template_override_repository
    ),
    icon_repo: NotificationIconRepository = Depends(get_notification_icon_repository),
):
    """Rewrite one notification's copy, for every replica, without a deploy.

    Rejects event types the service does not send: an override for a type that
    will never fire is invisible dead config, and the likeliest cause is a typo.
    """
    known = set(default_templates())
    if event_type not in known:
        raise HTTPException(
            status_code=404,
            detail=f"unknown event_type {event_type!r} (known: {', '.join(sorted(known))})",
        )

    # `exclude_unset` preserves the absent-vs-null distinction: only fields the
    # caller actually sent are touched.
    sent = payload.model_dump(exclude_unset=True)
    updated_by = sent.pop("updated_by", None)
    fields = {k: v for k, v in sent.items() if k in EDITABLE_TEMPLATE_FIELDS}
    if not fields:
        raise HTTPException(status_code=400, detail="No editable fields in the request")

    # Blank means "back to the glyph"; anything else has to be an icon we hold,
    # so a typo fails here rather than as a 404 image on a customer's phone.
    image_id = (fields.get("icon_image_id") or "").strip() if "icon_image_id" in fields else None
    if image_id:
        if not notification_icon.is_valid_icon_id(image_id) or not icon_repo.exists(image_id):
            raise HTTPException(status_code=400, detail=f"unknown icon_image_id {image_id!r}")
        fields["icon_image_id"] = image_id

    repo.upsert(event_type, fields, updated_by=updated_by)
    invalidate_override_cache()
    logger.info(
        "Notification template override saved: event=%s fields=%s by=%s",
        event_type, sorted(fields), updated_by or "unknown",
    )
    return {
        "status": "ok",
        "data": {
            "event_type": event_type,
            "template": all_templates().get(event_type, {}),
            "overridden": template_provenance().get(event_type, {}).get("overridden", []),
        },
    }


@router.delete("/templates/{event_type}", dependencies=[Depends(require_api_key)])
def delete_template_override(
    event_type: str,
    repo: NotificationTemplateOverrideRepository = Depends(
        get_notification_template_override_repository
    ),
):
    """Drop every edit for one event type — back to the compiled-in copy."""
    removed = repo.delete(event_type)
    invalidate_override_cache()
    return {
        "status": "ok",
        "data": {
            "removed": removed,
            "event_type": event_type,
            "template": all_templates().get(event_type, {}),
        },
    }


@router.get("/log", dependencies=[Depends(require_api_key)])
def notification_log(
    limit: int = Query(50, ge=1, le=200),
    sales_id: Optional[str] = Query(default=None),
    repo: NotificationLogRepository = Depends(get_notification_log_repository),
):
    """What the service actually told customers, newest first.

    The copy is stored as sent, not re-rendered: templates are editable, so
    recomposing an old message would show today's wording for something that
    went out last week.

    Pruning runs here rather than on a schedule — this service has no cron, an
    operator opens this view rarely, and the alternative is a table that grows
    without limit.
    """
    repo.prune(NOTIFICATION_LOG_RETENTION_DAYS)
    # Same reasoning as the prune above: no scheduler exists, and a row left
    # pending by a killed worker would otherwise never close.
    repo.settle_stale()
    return {
        "status": "ok",
        "data": {
            "entries": repo.recent(limit=limit, sales_id=(sales_id or "").strip() or None),
            "retention_days": NOTIFICATION_LOG_RETENTION_DAYS,
        },
    }


# ---------------------------------------------------------------------------
# Төлөв → мэдэгдэл: which notification a driver's status change sends.
# ---------------------------------------------------------------------------


@router.get("/rules", dependencies=[Depends(require_api_key)])
def list_rules():
    """Every wfm status and the notification it currently fires."""
    rules = all_rules()
    return {
        "status": "ok",
        "data": {
            "rules": [rules[key] for key in sorted(rules)],
            # The event types an operator may choose between. `admin_message` is
            # excluded: it renders payload placeholders a status change never
            # carries, so picking it here would send an empty notification.
            "event_types": sorted(k for k in default_templates() if k != "admin_message"),
        },
    }


class RuleOverrideRequest(BaseModel):
    """Absent field = leave alone. ``event_type: null`` = back to the default."""

    event_type: Optional[str] = Field(default=None)
    muted: Optional[bool] = Field(default=None)
    updated_by: Optional[str] = Field(default=None, max_length=120)


@router.put("/rules/{wfm_status_id}", dependencies=[Depends(require_api_key)])
def upsert_rule_override(
    wfm_status_id: int,
    payload: RuleOverrideRequest,
    repo: NotificationRuleOverrideRepository = Depends(
        get_notification_rule_override_repository
    ),
):
    """Point one status at a different notification, or silence it entirely."""
    if wfm_status_id not in STATUS_LABEL_BY_WFM_ID:
        raise HTTPException(
            status_code=404,
            detail=(
                f"wfm_status_id {wfm_status_id} is not a known delivery status "
                f"({sorted(STATUS_LABEL_BY_WFM_ID)})"
            ),
        )

    sent = payload.model_dump(exclude_unset=True)
    updated_by = sent.pop("updated_by", None)
    if not sent:
        raise HTTPException(status_code=400, detail="No editable fields in the request")

    set_event_type = "event_type" in sent
    event_type = sent.get("event_type")
    if set_event_type and event_type:
        choosable = {k for k in default_templates() if k != "admin_message"}
        if event_type not in choosable:
            raise HTTPException(
                status_code=400,
                detail=f"unknown event_type {event_type!r} (choose from {', '.join(sorted(choosable))})",
            )

    repo.upsert(
        wfm_status_id,
        event_type=event_type,
        muted=sent.get("muted"),
        updated_by=updated_by,
        set_event_type=set_event_type,
    )
    invalidate_override_cache()
    logger.info(
        "Notification rule saved: wfm=%s event=%s muted=%s by=%s",
        wfm_status_id, event_type, sent.get("muted"), updated_by or "unknown",
    )
    return {"status": "ok", "data": {"rule": all_rules().get(wfm_status_id)}}


@router.delete("/rules/{wfm_status_id}", dependencies=[Depends(require_api_key)])
def delete_rule_override(
    wfm_status_id: int,
    repo: NotificationRuleOverrideRepository = Depends(
        get_notification_rule_override_repository
    ),
):
    """Drop the rule for one status — back to WFM_STATUS_EVENT_TYPES."""
    removed = repo.delete(wfm_status_id)
    invalidate_override_cache()
    return {
        "status": "ok",
        "data": {"removed": removed, "rule": all_rules().get(wfm_status_id)},
    }


class AdminMessageRequest(BaseModel):
    sales_id: str
    title: str = Field(..., min_length=1, max_length=120)
    body: str = Field(..., min_length=1, max_length=400)
    icon: str = Field(default="campaign", max_length=64)
    # Optional uploaded image for this one message. Same contract as the
    # template field: an id we hold, or nothing.
    icon_image_id: Optional[str] = Field(default=None, max_length=64)
    urgency: str = Field(default="high", pattern="^(low|normal|high)$")


@router.post("/send", dependencies=[Depends(require_api_key)])
def send_admin_message(
    payload: AdminMessageRequest,
    repo: PushSubscriptionRepository = Depends(get_push_subscription_repository),
    icon_repo: NotificationIconRepository = Depends(get_notification_icon_repository),
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

    image_id = (payload.icon_image_id or "").strip()
    if image_id and (
        not notification_icon.is_valid_icon_id(image_id) or not icon_repo.exists(image_id)
    ):
        raise HTTPException(status_code=400, detail=f"unknown icon_image_id {image_id!r}")

    devices = repo.count_for_sales_id(sales_id)
    publish_order_event(
        sales_id,
        "admin_message",
        {
            "sales_number": sales_id,
            "admin_title": payload.title.strip(),
            "admin_body": payload.body.strip(),
            "notification_icon": payload.icon.strip() or "campaign",
            "notification_icon_image_id": image_id or None,
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

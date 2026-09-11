"""The one endpoint Deligo calls to tell a customer something.

``POST /api/notifications/send`` takes one of: a wfm status (``status_id`` —
the same template the driver's status change would fire), a template name
(``event_type``, plus the ``params`` its placeholders need), or free copy
(``title`` + ``body``) — and returns the notification exactly as the customer
sees it. Deligo writes that echo onto its own order timeline — which is why
there is no receiver on their side any more (``DELIGO_NOTIFY_PATH`` is off by default).

Delivery is the same road every automatic notification takes:
``publish_order_event`` → SSE to an open tracking tab, web push to a closed
one, one row in the notification log. Nothing here talks to the push sender
directly.

Everything else under ``/api/push/*`` (subscribe, templates, rules, log) stays
as it is for the tracking page and the admin panel; it is simply not part of
the integration surface.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from src.api.auth_utils import require_api_key
from src.dependencies import get_delivery_repository, get_push_subscription_repository
from src.repositories.delivery import DeliveryRepository
from src.repositories.notification_override import VALID_URGENCIES
from src.repositories.push_subscription import PushSubscriptionRepository
from src.services import webpush
from src.services.events import publish_order_event
from src.services.notifications import (
    KNOWN_PLACEHOLDERS,
    STATUS_LABEL_BY_WFM_ID,
    build_notification,
    choosable_event_types,
    enrich_params,
    event_type_for_status,
    missing_placeholders,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

# Material Symbols names only — the frontend renders this straight into a
# `material-symbols-outlined` span, so anything else silently draws nothing.
_ICON_RE = re.compile(r"^[a-z0-9_]{1,64}$")

# Keys a caller may put in `params`. The derived ones (`*_line`, `*_text`,
# `status_label`) are accepted too so a caller can override our wording, but
# the admin_* pair is not — that is what custom mode is for.
_PARAM_KEYS = KNOWN_PLACEHOLDERS - {"sales_number", "admin_title", "admin_body"}


class SendNotificationRequest(BaseModel):
    """Exactly one of: ``status_id`` (status), ``event_type`` (template), or ``title`` + ``body`` (custom)."""

    sales_id: str = Field(..., description="Deligo sales id — the order the customer is tracking")
    sales_number: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Human-facing order code for the tracking link and {sales_number}. Defaults to sales_id.",
    )

    # ── status mode ──
    status_id: Optional[int] = Field(
        default=None,
        description="Deligo wfm status id. Sends whatever a driver setting this status would send.",
    )
    status_description: Optional[str] = Field(
        default=None,
        max_length=400,
        description="Status mode only: the note appended by the generic templates.",
    )

    # ── template mode ──
    event_type: Optional[str] = Field(default=None, max_length=64)
    params: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Values for the template's placeholders, e.g. {\"status_description\": \"...\"}",
    )

    # ── custom mode ──
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    body: Optional[str] = Field(default=None, min_length=1, max_length=400)

    # ── both ──
    icon: Optional[str] = Field(default=None, max_length=64)
    urgency: Optional[str] = Field(default=None)

    @field_validator("sales_id", "sales_number", "event_type", "title", "body", "icon", "urgency", "status_description")
    @classmethod
    def _strip(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("icon")
    @classmethod
    def _check_icon(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _ICON_RE.match(value):
            raise ValueError(
                "icon must be a Material Symbols name — lowercase letters, digits "
                "and underscores only (e.g. local_shipping)"
            )
        return value

    @field_validator("urgency")
    @classmethod
    def _check_urgency(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in VALID_URGENCIES:
            raise ValueError(f"urgency must be one of {', '.join(VALID_URGENCIES)}")
        return value

    @field_validator("params")
    @classmethod
    def _check_params(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not value:
            return None
        unknown = sorted(k for k in value if k not in _PARAM_KEYS)
        if unknown:
            raise ValueError(
                f"unknown params key(s): {', '.join(unknown)}. "
                f"Accepted: {', '.join(sorted(_PARAM_KEYS))}"
            )
        return value

    @model_validator(mode="after")
    def _one_mode(self) -> "SendNotificationRequest":
        if not self.sales_id:
            raise ValueError("sales_id is required")
        status_mode = self.status_id is not None
        template_mode = self.event_type is not None
        custom_mode = self.title is not None or self.body is not None
        if status_mode + template_mode + custom_mode != 1:
            raise ValueError(
                "send exactly one of: status_id (status), event_type (template), or title+body (custom)"
            )
        if custom_mode and (self.title is None or self.body is None):
            raise ValueError("custom mode needs both title and body")
        if self.params and custom_mode:
            raise ValueError("params does not apply to custom mode (title+body)")
        if self.status_description is not None and not status_mode:
            raise ValueError("status_description only applies to status mode (status_id)")
        return self

    @property
    def is_status(self) -> bool:
        return self.status_id is not None

    @property
    def is_template(self) -> bool:
        return self.event_type is not None


@router.post("/send", dependencies=[Depends(require_api_key)])
def send_notification(
    payload: SendNotificationRequest,
    repo: PushSubscriptionRepository = Depends(get_push_subscription_repository),
    orders: Optional[DeliveryRepository] = Depends(get_delivery_repository),
):
    """Send one notification to one order's customer and echo what they saw.

    Status mode is the driver's status change without the status change: the
    same rule lookup (operator overrides included) and the same payload shape
    as ``/api/auth/orders/changestatus``, so the customer reads the same
    sentence either way — and a muted status is honoured the same way (409
    here, since a caller deserves to know nothing went out).

    Template mode renders the named template against ``params`` and refuses
    (422) rather than send a sentence with a blank where a value should be.
    Custom mode goes out as ``admin_message``, the free-text passthrough.
    Those two ignore the mute rules: mute silences *status-change side
    effects*, and a caller asking by name is intent, not a side effect.
    """
    sales_id = payload.sales_id
    sales_number = payload.sales_number
    if not sales_number and orders is not None:
        # Deligo only has to send sales_id; the tracking link still gets the
        # human code when we already know the order.
        try:
            order = orders.get_by_sales_id(sales_id)
            sales_number = (order.sales_number if order else None) or None
        except Exception:
            logger.warning("Could not look up sales_number for %s", sales_id, exc_info=True)
    sales_number = sales_number or sales_id

    if payload.is_status:
        status_id = int(payload.status_id or 0)
        event_type = event_type_for_status(status_id)
        data: Dict[str, Any] = enrich_params(
            {
                "wfm_status_id": status_id,
                # Unknown status → generic wording, same fallback changestatus uses.
                "status_label": STATUS_LABEL_BY_WFM_ID.get(status_id, "Шинэчлэгдсэн"),
                "status_description": payload.status_description or "",
                **(payload.params or {}),
            }
        )
    elif payload.is_template:
        event_type = payload.event_type or ""
        choosable = choosable_event_types()
        if event_type not in choosable:
            raise HTTPException(
                status_code=404,
                detail=f"unknown event_type {event_type!r} (known: {', '.join(choosable)})",
            )
        data = enrich_params(payload.params or {})
        missing = missing_placeholders(event_type, data)
        if missing:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"template {event_type!r} needs params for: {', '.join(missing)}"
                ),
            )
    else:
        event_type = "admin_message"
        data = {"admin_title": payload.title, "admin_body": payload.body}
        # Custom copy has no template defaults worth inheriting; match what
        # the admin panel has always sent.
        payload.icon = payload.icon or "campaign"
        payload.urgency = payload.urgency or "high"

    data["sales_number"] = sales_number
    if payload.icon:
        data["notification_icon"] = payload.icon
    if payload.urgency:
        data["notification_urgency"] = payload.urgency

    # Rendered here, with the same function the log and the push sender use,
    # so the echo is byte-identical to what goes out. Never None: the type is
    # known and no wfm_status_id is set unless the caller chose to pass one
    # for {status_label} — in which case a mute on that status *does* apply,
    # which is the one honest reading of "send delivery_failed for status 16".
    notification = build_notification(event_type, sales_id, data)
    if notification is None:
        raise HTTPException(
            status_code=409,
            detail=f"wfm status {data.get('wfm_status_id')} is muted by an operator; nothing was sent",
        )

    event_id = uuid.uuid4().hex
    sent_at = datetime.now(timezone.utc).isoformat()
    devices = repo.count_for_sales_id(sales_id)
    publish_order_event(sales_id, event_type, data, event_id=event_id)
    logger.info(
        "Notification sent via API: sales_id=%s event=%s devices=%d id=%s",
        sales_id, event_type, devices, event_id,
    )
    return {
        "status": "ok",
        "data": {
            "event_id": event_id,
            "sales_id": sales_id,
            "sales_number": sales_number,
            "event_type": event_type,
            "title": notification["title"],
            "body": notification["body"],
            "icon": notification["icon"],
            "urgency": notification["urgency"],
            "tracking_url": notification["url"],
            # Browsers the push half can reach. Zero is not an error: an open
            # tracking tab still receives it over SSE.
            "push_devices": devices,
            "push_enabled": webpush.is_configured(),
            "sent_at": sent_at,
        },
    }

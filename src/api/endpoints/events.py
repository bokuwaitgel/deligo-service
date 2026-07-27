"""Server-Sent Events stream powering the customer's browser notifications.

SSE rather than WebSocket on purpose:

* it is plain HTTP, so it passes through the existing Caddy reverse proxy with
  no upgrade handling or per-route config;
* ``EventSource`` reconnects on its own and replays ``Last-Event-ID``, which is
  exactly requirement 3.2.4 with no client-side retry loop to get wrong;
* the traffic is strictly server → client, so the extra duplex capability of a
  WebSocket would buy nothing.

The stream is public and scoped to a single ``sales_id``, matching the existing
public tracking endpoints (``GET /api/delivery/tracking/{sales_number}``,
``GET /api/drivers/{id}/location/public``) that the tracking page already relies
on — the tracking link itself is the capability.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.dependencies import get_delivery_repository
from src.repositories.delivery import DeliveryRepository
from src.services.events import (
    publish_order_event,
    replay_order_events,
    subscribe_order,
    unsubscribe_order,
)
from src.services.notifications import build_notification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"])

# Comment frames keep the connection alive through proxies that close idle
# connections, and let the client notice a dead link quickly.
HEARTBEAT_SECONDS = 20
# Client-side reconnect delay advertised in the stream.
RETRY_MS = 3000


def _format_sse(event: dict) -> str:
    """Render one event in the text/event-stream wire format.

    ``id:`` is what the browser echoes back as ``Last-Event-ID`` after a drop,
    and what the client uses to discard duplicates.
    """
    notification = build_notification(
        str(event.get("type", "")),
        str(event.get("sales_id", "")),
        event.get("data") or {},
    )
    body = {
        "id": event.get("id"),
        "sales_id": event.get("sales_id"),
        "type": event.get("type"),
        "ts": event.get("ts"),
        "data": event.get("data") or {},
        "notification": notification,
    }
    return (
        f"id: {event.get('id')}\n"
        f"event: order\n"
        f"data: {json.dumps(body, ensure_ascii=False)}\n\n"
    )


@router.get("/order/{sales_id}/stream")
async def stream_order_events(
    sales_id: str,
    request: Request,
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
):
    """Live event stream for one order."""
    sid = str(sales_id).strip()
    if not sid:
        raise HTTPException(status_code=400, detail="sales_id is required")

    queue = subscribe_order(sid)
    missed = replay_order_events(sid, last_event_id)

    async def event_source() -> AsyncIterator[str]:
        try:
            yield f"retry: {RETRY_MS}\n\n"
            # Tell the client it is live before anything else, so the UI can drop
            # its "connecting" state without waiting for the first real event.
            yield "event: ready\ndata: {}\n\n"

            for event in missed:
                yield _format_sse(event)

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield _format_sse(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Order event stream failed for sales_id=%s", sid, exc_info=True)
        finally:
            unsubscribe_order(sid, queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Caddy does not buffer by default, but nginx (and some CDNs) do —
            # buffering an SSE stream delays every notification indefinitely.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/order/{sales_id}/test", dependencies=[])
async def publish_test_event(
    sales_id: str,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Emit a harmless `status_changed` event — used to verify the stream end to end.

    Kept unauthenticated for the same reason the stream is: it only affects the
    single order whose tracking id the caller already holds, and it changes no
    stored state.
    """
    order = repo.get_by_sales_id(str(sales_id))
    if not order:
        raise HTTPException(status_code=404, detail="Delivery order not found")
    publish_order_event(
        str(sales_id),
        "status_changed",
        {"sales_number": order.sales_number, "status_label": "Туршилтын мэдэгдэл"},
    )
    return {"status": "ok"}

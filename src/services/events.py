"""Real-time order event bus behind the customer notification stream.

Package 3 needs a driver-side write (status change, route start, pin move, GPS
update) to reach the customer's open browser within a few seconds. The write
happens in a *synchronous* request handler, possibly in a different process from
the one holding the customer's SSE connection: production runs
``API_REPLICAS × API_WORKERS`` uvicorn processes behind Caddy round-robin, so an
in-process ``asyncio.Queue`` fan-out alone would only reach the ~1/8 of customers
who happen to be connected to the publishing process.

Design
------
* Publishers call the plain-sync :func:`publish_order_event`. It never awaits and
  never raises — a notification must not be able to fail an address save.
* Delivery is local first (in-process subscribers) and then, when ``REDIS_URL``
  is configured, over a Redis pub/sub channel so every other worker delivers to
  its own subscribers too.
* Each event carries a monotonic per-order ``id``. Recent events are kept in a
  small ring buffer so a browser reconnecting with ``Last-Event-ID`` can replay
  what it missed instead of silently losing it (requirement 3.2.4), and so a
  client that receives the same event twice can drop the duplicate (3.2.5).

Without ``REDIS_URL`` everything still works — the bus degrades to a single
process, which is exactly right for local development and a single-replica
deploy.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "").strip()
REDIS_CHANNEL = os.getenv("ORDER_EVENTS_CHANNEL", "deligo:order-events")
# How many recent events per order to keep for Last-Event-ID replay.
REPLAY_BUFFER_SIZE = int(os.getenv("ORDER_EVENTS_REPLAY_SIZE", "50"))
# Drop replay entries older than this so a browser left open overnight doesn't
# wake up and fire a burst of stale notifications.
REPLAY_MAX_AGE_SECONDS = int(os.getenv("ORDER_EVENTS_REPLAY_MAX_AGE", "900"))
# Bound each subscriber's queue: a client that stops reading must not grow the
# server's memory without limit.
SUBSCRIBER_QUEUE_MAX = 100


OrderEvent = Dict[str, Any]


class _EventBus:
    def __init__(self) -> None:
        self._subscribers: Dict[str, Set["asyncio.Queue[OrderEvent]"]] = {}
        self._replay: Dict[str, Deque[OrderEvent]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._redis: Any = None
        self._redis_task: Optional[asyncio.Task[None]] = None
        self._lock = asyncio.Lock()

    # ---- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if not REDIS_URL:
            logger.info(
                "Order event bus running in single-process mode "
                "(REDIS_URL not set — notifications will not cross API replicas)"
            )
            return
        try:
            import redis.asyncio as aioredis  # type: ignore[import-not-found]
        except ImportError:
            logger.warning(
                "REDIS_URL is set but the `redis` package is not installed — "
                "falling back to single-process event delivery"
            )
            return
        try:
            self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            await self._redis.ping()
        except Exception:
            logger.warning(
                "Could not connect to Redis at %s — falling back to single-process "
                "event delivery", REDIS_URL.split("@")[-1], exc_info=True,
            )
            self._redis = None
            return
        self._redis_task = asyncio.create_task(self._consume_redis())
        logger.info("Order event bus connected to Redis channel %s", REDIS_CHANNEL)

    async def stop(self) -> None:
        if self._redis_task is not None:
            self._redis_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._redis_task
            self._redis_task = None
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None

    async def _consume_redis(self) -> None:
        """Re-deliver events published by other API workers to local subscribers.

        Reconnects with backoff forever: a Redis blip must not permanently
        silence notifications on this worker.
        """
        backoff = 1.0
        while True:
            pubsub = None
            try:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(REDIS_CHANNEL)
                backoff = 1.0
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    try:
                        event = json.loads(message["data"])
                    except (TypeError, ValueError):
                        continue
                    if isinstance(event, dict):
                        self._deliver_local(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Redis pub/sub loop failed; retrying", exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                if pubsub is not None:
                    with contextlib.suppress(Exception):
                        await pubsub.aclose()

    # ---- publish -------------------------------------------------------

    def publish(self, event: OrderEvent) -> None:
        """Thread-safe, non-blocking publish. Safe to call from sync handlers."""
        loop = self._loop
        if loop is None or loop.is_closed():
            # No running app loop (e.g. a script or a unit test importing the
            # service directly) — nothing is listening, so this is a no-op.
            return
        try:
            loop.call_soon_threadsafe(self._publish_on_loop, event)
        except RuntimeError:
            logger.debug("Event loop unavailable; dropping order event", exc_info=True)

    def _publish_on_loop(self, event: OrderEvent) -> None:
        self._deliver_local(event)
        if self._redis is not None:
            asyncio.create_task(self._publish_redis(event))

    async def _publish_redis(self, event: OrderEvent) -> None:
        try:
            await self._redis.publish(REDIS_CHANNEL, json.dumps(event))
        except Exception:
            logger.warning("Failed to publish order event to Redis", exc_info=True)

    def _deliver_local(self, event: OrderEvent) -> None:
        sales_id = str(event.get("sales_id", ""))
        if not sales_id:
            return

        buffer = self._replay.setdefault(sales_id, deque(maxlen=REPLAY_BUFFER_SIZE))
        # Redis echoes our own publish back to us; keep the buffer (and the
        # subscribers) free of that duplicate.
        if any(existing.get("id") == event.get("id") for existing in buffer):
            return
        buffer.append(event)

        for queue in list(self._subscribers.get(sales_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Subscriber is not draining — drop the oldest to make room so a
                # stalled tab loses old news rather than the latest status.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

    # ---- subscribe -----------------------------------------------------

    def subscribe(self, sales_id: str) -> "asyncio.Queue[OrderEvent]":
        queue: "asyncio.Queue[OrderEvent]" = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
        self._subscribers.setdefault(str(sales_id), set()).add(queue)
        return queue

    def unsubscribe(self, sales_id: str, queue: "asyncio.Queue[OrderEvent]") -> None:
        subscribers = self._subscribers.get(str(sales_id))
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(str(sales_id), None)

    def replay_since(self, sales_id: str, last_event_id: Optional[str]) -> List[OrderEvent]:
        """Events newer than ``last_event_id`` for this order, oldest first.

        An unknown / absent id replays nothing: a fresh page load should see the
        order's current state from its own fetch, not a burst of history.
        """
        if not last_event_id:
            return []
        buffer = self._replay.get(str(sales_id))
        if not buffer:
            return []
        events = list(buffer)
        cutoff = time.time() - REPLAY_MAX_AGE_SECONDS
        for index, event in enumerate(events):
            if event.get("id") == last_event_id:
                return [e for e in events[index + 1:] if float(e.get("ts", 0)) >= cutoff]
        return []


_BUS = _EventBus()


async def start_event_bus() -> None:
    await _BUS.start()


async def shutdown_event_bus() -> None:
    await _BUS.stop()


def publish_order_event(
    sales_id: str | int,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Announce something that happened to an order.

    Never raises: the caller is always in the middle of a business write that
    must succeed whether or not anyone is listening.
    """
    try:
        sid = str(sales_id).strip()
        if not sid:
            return
        event: OrderEvent = {
            "id": uuid.uuid4().hex,
            "sales_id": sid,
            "type": event_type,
            "ts": time.time(),
            "data": payload or {},
        }
        _BUS.publish(event)

        # History for the admin panel. Recorded on the publish side for the same
        # reason the push is: `_deliver_local` runs on every worker, so logging
        # there would write one row per replica for a single notification.
        #
        # MUST come before the push is queued. The sender runs on a background
        # thread and writes the delivery outcome back onto this row by event id;
        # if it got there first the row would not exist yet, the outcome would be
        # dropped, and the admin panel would show "Хүлээгдэж байна..." forever.
        _record_sent_notification(sid, event_type, str(event["id"]), event["data"])

        # Web Push reaches the customers whose tab is closed — the ones the SSE
        # stream above can never touch. Sent here, on the publish side only:
        # `_deliver_local` also runs on every other worker via the Redis fan-out,
        # so hooking it there would send one push per worker. The call queues
        # onto a background thread and never raises.
        from src.services.webpush import send_event as _send_push_event

        _send_push_event(sid, event_type, str(event["id"]), event["data"])
    except Exception:
        logger.warning("Failed to publish order event %s", event_type, exc_info=True)


def _record_sent_notification(
    sales_id: str,
    event_type: str,
    event_id: str,
    data: Dict[str, Any],
) -> None:
    """Log what the customer was told, if anything.

    Never raises and never blocks the publish: an unrecorded notification is a
    reporting gap, while a failed publish is a customer who is not told their
    driver arrived.
    """
    try:
        from src.services import webpush
        from src.services.notifications import build_notification

        # Same suppression the push sender applies: driver_location fires on
        # every GPS ping and shows nothing, so it is movement, not a message.
        if event_type in webpush.PUSH_SUPPRESSED_EVENT_TYPES:
            return

        notification = build_notification(event_type, sales_id, data or {})
        if not notification:
            # No customer-facing copy, or an operator muted this status. Nothing
            # was shown, so there is nothing to report as sent.
            return

        from src.dependencies import _get_session_factory
        from src.repositories.notification_log import NotificationLogRepository
        from src.repositories.push_subscription import PushSubscriptionRepository

        session = _get_session_factory()()
        try:
            devices = PushSubscriptionRepository(session).count_for_sales_id(sales_id)
            NotificationLogRepository(session).record(
                event_id=event_id,
                sales_id=sales_id,
                event_type=event_type,
                notification=notification,
                push_devices=devices,
            )
        finally:
            session.close()
    except Exception:
        logger.warning("Could not record notification history for %s", sales_id, exc_info=True)


def subscribe_order(sales_id: str) -> "asyncio.Queue[OrderEvent]":
    return _BUS.subscribe(sales_id)


def unsubscribe_order(sales_id: str, queue: "asyncio.Queue[OrderEvent]") -> None:
    _BUS.unsubscribe(sales_id, queue)


def replay_order_events(sales_id: str, last_event_id: Optional[str]) -> List[OrderEvent]:
    return _BUS.replay_since(sales_id, last_event_id)

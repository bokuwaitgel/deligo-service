"""The "your order is N deliveries away" notification.

The customer flow the notification set is built around is:

    driver_accepted → delivery_queue_near → delivery_completed / a failure reason

Only the middle step has no status change of its own to hang off — nothing in
Deligo fires when an order becomes third in line. It becomes third because a
*different* order ahead of it closed. So this module is called after every write
that can shorten a driver's queue, recomputes the queue, and notifies the one
order that has just crossed the threshold.

Two properties matter and neither is free:

* **Exactly once per order.** The trigger is re-evaluated on every status change,
  so without a persisted marker the same customer would be told again each time
  another order behind them closed. ``delivery_orders.queue_alert_sent_at`` is
  that marker; it is a column rather than an in-memory set because every API
  replica runs this same evaluation and would otherwise each send one.
* **Never breaks the caller.** Like ``publish_order_event``, this runs inside a
  status-change request that must succeed whether or not a notification does.
  Every failure path here logs and returns.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from src.repositories.delivery import DeliveryRepository
from src.services.deligo_integration import OPEN_STATUS_CODES
from src.services.events import publish_order_event
from src.services.notifications import describe_queue_position

logger = logging.getLogger(__name__)

# How many deliveries ahead of the customer trigger the heads-up. 2 is the
# number in the spec; the template prints this value rather than a literal "2",
# so raising it here keeps the sentence true.
QUEUE_ALERT_POSITION = int(os.getenv("NOTIFY_QUEUE_ALERT_POSITION", "2"))


def _queue_position(order, ordered_sales_ids: list[str]) -> Optional[int]:
    """How many open deliveries the driver has before this one, or None."""
    try:
        return ordered_sales_ids.index(str(order.sales_id))
    except ValueError:
        return None


def notify_queue_positions(repo: DeliveryRepository, driver_id: Optional[str]) -> None:
    """Announce to any order that just became ``QUEUE_ALERT_POSITION`` stops away.

    Call after anything that closes an order or reorders a driver's route.
    """
    if not driver_id:
        return
    try:
        open_orders = [
            order
            for order in repo.get_active_by_driver_id(str(driver_id))
            if order.status in OPEN_STATUS_CODES
        ]
    except Exception:
        logger.warning(
            "Could not load driver %s queue for position alerts", driver_id, exc_info=True
        )
        return

    # The driver's own confirmed sequence. Orders with no sort_order yet sink to
    # the end rather than being dropped: an unsorted order is genuinely last in
    # the queue as far as anyone can tell, and it still gets its alert once the
    # driver sorts the route.
    open_orders.sort(
        key=lambda o: (o.sort_order is None, o.sort_order or 0, str(o.sales_id))
    )
    ordered_sales_ids = [str(o.sales_id) for o in open_orders]

    for order in open_orders:
        if order.queue_alert_sent_at is not None:
            continue
        position = _queue_position(order, ordered_sales_ids)
        # `>` and not `==`: a driver who closes two orders in quick succession,
        # or a route re-sort that moves an order forward, can skip the exact
        # position. Without this the customer nearest to being delivered — the
        # one the heads-up is for — is the one who never gets it.
        if position is None or position > QUEUE_ALERT_POSITION:
            continue

        # Mark first, publish second. A crash between the two costs one
        # notification; the reverse order costs the customer a duplicate on
        # every subsequent status change, which is the worse failure.
        try:
            repo.update_partial(
                str(order.sales_id),
                {"queue_alert_sent_at": datetime.now(timezone.utc)},
            )
        except Exception:
            logger.warning(
                "Could not mark queue alert for sales_id=%s", order.sales_id, exc_info=True
            )
            continue

        publish_order_event(
            str(order.sales_id),
            "delivery_queue_near",
            {
                "sales_number": order.sales_number,
                "driver_id": str(driver_id),
                "queue_position": position,
                "queue_position_text": describe_queue_position(position),
            },
        )
        logger.info(
            "Queue alert published for sales_id=%s (position=%s)", order.sales_id, position
        )

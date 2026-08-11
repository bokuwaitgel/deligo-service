from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PushSubscription(Base):
    """One Web Push endpoint registered by one browser for one order.

    The customer tracking link is per order, so a subscription is scoped to a
    ``sales_id`` — a browser tracking two orders registers the same push
    endpoint twice, once per order. That keeps the send path a plain lookup by
    ``sales_id`` with no join, and lets an order's subscriptions be dropped as a
    unit once it is delivered.

    ``endpoint`` is the push service URL the browser handed us; it is the
    identity of the subscription as far as the push service is concerned, which
    is why (endpoint, sales_id) is unique rather than just endpoint.
    """

    __tablename__ = "push_subscriptions"

    __table_args__ = (
        UniqueConstraint("endpoint", "sales_id", name="uq_push_endpoint_sales"),
        Index("ix_push_sales_id", "sales_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    sales_id: Mapped[str] = mapped_column(String, nullable=False)
    # Push service URLs are long (FCM endpoints run ~200 chars, Mozilla's more)
    # and have no documented cap — Text avoids an arbitrary truncation point.
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    # The two keys from PushSubscription.getKey(); together they are what
    # encrypts the payload for this browser.
    p256dh: Mapped[str] = mapped_column(String, nullable=False)
    auth: Mapped[str] = mapped_column(String, nullable=False)
    user_agent: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Bumped on every resubscribe so stale rows can be pruned by age.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    # Consecutive send failures that were NOT a hard 404/410 (those delete the
    # row outright). Lets a push service having a bad hour be told apart from a
    # subscription that is really gone.
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

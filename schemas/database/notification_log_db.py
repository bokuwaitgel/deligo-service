from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class NotificationLog(Base):
    """One row per customer-facing notification the service sent.

    Answers the question the admin panel could not: *what did we actually tell
    this customer, and did it arrive?* Until now a send left only a log line
    inside a container, so "the customer says they got nothing" had no evidence
    either way.

    Written at publish time with the rendered copy, then updated by the web push
    sender once it knows how many devices accepted it. The two halves are joined
    on ``event_id`` — the same id the SSE stream and the push payload carry, so
    a row here can be matched against what the browser received.

    Not written for every event: ``driver_location`` fires on every GPS ping and
    is push-suppressed by design, so logging it would bury the real messages.
    Only events that produced a visible notification are recorded.
    """

    __tablename__ = "notification_log"

    # The event id from publish_order_event — already unique per event.
    event_id: Mapped[str] = mapped_column(String, primary_key=True)

    sales_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)

    # The copy exactly as the customer saw it, after template resolution and
    # placeholder substitution. Stored rather than recomposed: templates are
    # editable now, so re-rendering later would show today's wording for a
    # message sent last week.
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    icon: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    urgency: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # How many browsers were registered when the event was published. Zero
    # means SSE-only: an open tab still got it, a closed one got nothing.
    push_devices: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Filled in by the sender thread once the push service has answered.
    push_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    push_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # True once the sender has reported back; distinguishes "0 delivered" from
    # "not finished yet", which look identical in the counters alone.
    push_settled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        # The admin view is always "newest first, optionally for one order".
        Index("ix_notification_log_sales_created", "sales_id", "created_at"),
    )

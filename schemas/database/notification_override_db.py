from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class NotificationTemplateOverride(Base):
    """An operator's edit to the wording of one notification.

    **Sparse, like the status catalog overrides.** Every column except the key
    is nullable and NULL means "no opinion — use the compiled-in default (or
    whatever ``NOTIFY_*`` env still sets)". Rewriting only the body leaves the
    icon and urgency tracking the defaults, so a later change to those still
    reaches production without somebody re-saving this row.

    Lives in Postgres rather than env because it has to be writable from the
    admin panel: ``notifications._load_templates`` reads env once per process,
    so an edit there is invisible to the other API workers until a restart.

    Keyed by ``event_type`` — the same string ``build_notification`` looks up
    and the event carries on the wire.
    """

    __tablename__ = "notification_template_overrides"

    event_type: Mapped[str] = mapped_column(String, primary_key=True)

    # NULL = inherit from _DEFAULT_TEMPLATES / NOTIFY_* env.
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    icon: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    urgency: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # An uploaded image to show instead of the Material Symbols glyph, as the
    # notification_icons row id. Stored as an id rather than a URL so an
    # operator can never point a customer's browser at an arbitrary host: the
    # server derives the URL from the id it can find in its own table.
    # `icon` stays alongside it as the fallback — iOS ignores notification
    # images entirely, and the in-app toast still needs a glyph.
    icon_image_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Free text from the admin panel; there are no user accounts on this surface.
    updated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class NotificationRuleOverride(Base):
    """Which notification the customer gets when a driver sets one wfm status.

    The admin panel's "Төлөв → мэдэгдэл" table. Defaults live in
    ``notifications.WFM_STATUS_EVENT_TYPES``; a row here replaces the default
    for one status id.

    ``muted`` is deliberately a separate column rather than a magic
    ``event_type`` value: "send nothing for this status" and "send the generic
    status_changed" are different intentions, and an operator who mutes a
    status should not lose the event type they had chosen before — unmuting
    restores it instead of making them pick again.
    """

    __tablename__ = "notification_rule_overrides"

    wfm_status_id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # NULL = inherit the default mapping for this status.
    event_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # True = the customer is told nothing when an order reaches this status.
    # The event is still published, so the tracking page still refreshes; only
    # the notification is suppressed.
    muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    updated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

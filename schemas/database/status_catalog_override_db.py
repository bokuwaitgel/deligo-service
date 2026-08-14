from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class StatusCatalogOverride(Base):
    """An operator's edit to one delivery status' presentation.

    **Sparse on purpose.** Every column except the key is nullable, and NULL
    means "no opinion — use whatever the source says". Overriding just the icon
    leaves the colour still tracking Deligo, so the day their real catalog
    lands, only the fields somebody deliberately changed stop following it.

    Lives in Postgres rather than env (the notification templates' approach)
    because this one has to be writable from the admin panel: env is read once
    per process, so an edit would be invisible to the other API workers until a
    restart. A row here is seen by every replica on its next request.

    Keyed by ``wfm_status_id`` — the same id Deligo puts on every order, and the
    only status identifier guaranteed to survive a label or status_code change
    upstream.
    """

    __tablename__ = "status_catalog_overrides"

    wfm_status_id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # NULL = inherit from the source catalog (Deligo → env → local dummy).
    icon: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    pin_color: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    label: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Who last touched it, so "why is this status purple" has an answer. Free
    # text from the admin panel; there are no user accounts on this surface.
    updated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

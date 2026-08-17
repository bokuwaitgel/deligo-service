from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, LargeBinary, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class NotificationIcon(Base):
    """An image an operator uploaded to use as a notification icon.

    **Stored in Postgres rather than on disk on purpose.** The API runs from a
    container with no persistent volume and more than one replica, so a file
    written to the local filesystem would vanish on the next deploy and would
    only ever be visible to the worker that received the upload. The images are
    normalised to a 192x192 PNG before they get here (see
    ``services/notification_icon.py``), which puts them in the tens of
    kilobytes — small enough that serving them through the app is cheaper than
    introducing object storage for this one feature.

    Rows are immutable: an edit is a new upload. That is what lets
    ``GET /api/push/icons/{id}`` be served with a far-future cache header, which
    matters because the fetcher is the customer's push service, not our page.
    """

    __tablename__ = "notification_icons"

    # Content hash, hex. Doubles as the dedupe key: the same logo uploaded from
    # three admin sessions is one row and one cacheable URL.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)

    # The uploader's filename, kept only so the admin picker can label the tile.
    # Never used to build a path or a Content-Disposition — see the endpoint.
    label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # Origin the upload arrived on, used to build an absolute URL when
    # PUBLIC_API_BASE_URL is not set. A push notification's icon is fetched by
    # the customer's browser, so a relative path would resolve against the
    # frontend, which does not serve this route.
    origin: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    uploaded_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

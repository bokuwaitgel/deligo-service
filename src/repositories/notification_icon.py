from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy.orm import Session, load_only

from schemas.database.notification_icon_db import NotificationIcon

logger = logging.getLogger(__name__)


class NotificationIconRepository:
    """Uploaded notification icons, keyed by content hash."""

    def __init__(self, db_session: Session):
        self.db_session = db_session

    def get(self, icon_id: str) -> Optional[NotificationIcon]:
        return (
            self.db_session.query(NotificationIcon)
            .filter(NotificationIcon.id == str(icon_id))
            .first()
        )

    def exists(self, icon_id: str) -> bool:
        """Cheap existence check that never loads the image bytes."""
        return (
            self.db_session.query(NotificationIcon.id)
            .filter(NotificationIcon.id == str(icon_id))
            .first()
            is not None
        )

    def list_all(self, limit: int = 60) -> List[NotificationIcon]:
        """Newest first, without the bytes — this feeds the admin picker.

        ``load_only`` matters here: the picker lists dozens of icons and pulling
        every image body to render a list of names would move megabytes for no
        reason. The bytes are fetched one at a time by the serving endpoint.
        """
        return (
            self.db_session.query(NotificationIcon)
            .options(
                load_only(
                    NotificationIcon.id,
                    NotificationIcon.content_type,
                    NotificationIcon.size_bytes,
                    NotificationIcon.width,
                    NotificationIcon.height,
                    NotificationIcon.label,
                    NotificationIcon.origin,
                    NotificationIcon.uploaded_by,
                    NotificationIcon.created_at,
                )
            )
            .order_by(NotificationIcon.created_at.desc())
            .limit(limit)
            .all()
        )

    def store(
        self,
        *,
        icon_id: str,
        data: bytes,
        content_type: str,
        width: int,
        height: int,
        label: Optional[str] = None,
        origin: Optional[str] = None,
        uploaded_by: Optional[str] = None,
    ) -> NotificationIcon:
        """Insert the image, or return the existing row with the same hash.

        Re-uploading a file already stored is a no-op rather than an error: the
        operator's intent ("use this image") is satisfied either way, and the id
        they get back is the same URL already cached by every push service.
        """
        existing = self.get(icon_id)
        if existing is not None:
            return existing

        row = NotificationIcon(
            id=icon_id,
            data=data,
            content_type=content_type,
            size_bytes=len(data),
            width=width,
            height=height,
            label=label,
            origin=origin,
            uploaded_by=uploaded_by,
        )
        self.db_session.add(row)
        self.db_session.commit()
        self.db_session.refresh(row)
        return row

    def delete(self, icon_id: str) -> bool:
        row = self.get(icon_id)
        if row is None:
            return False
        self.db_session.delete(row)
        self.db_session.commit()
        return True

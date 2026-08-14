from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from schemas.database.notification_log_db import NotificationLog

logger = logging.getLogger(__name__)


class NotificationLogRepository:
    """History of what the service told customers, and whether it landed."""

    def __init__(self, db_session: Session):
        self.db_session = db_session

    def record(
        self,
        event_id: str,
        sales_id: str,
        event_type: str,
        notification: Dict[str, Any],
        push_devices: int = 0,
    ) -> Optional[NotificationLog]:
        """Store one sent notification. Returns None if it was already logged.

        Idempotent on ``event_id`` so a republish (or a retry) does not create a
        second row for the same message.
        """
        existing = self.db_session.get(NotificationLog, str(event_id))
        if existing is not None:
            return None

        row = NotificationLog(
            event_id=str(event_id),
            sales_id=str(sales_id),
            event_type=str(event_type),
            title=str(notification.get("title") or ""),
            body=str(notification.get("body") or ""),
            icon=notification.get("icon"),
            urgency=notification.get("urgency"),
            push_devices=int(push_devices or 0),
        )
        self.db_session.add(row)
        self.db_session.commit()
        return row

    def mark_push_result(self, event_id: str, sent: int, failed: int) -> bool:
        """Record what the push service answered for one already-logged event."""
        row = self.db_session.get(NotificationLog, str(event_id))
        if row is None:
            return False
        row.push_sent = int(sent)
        row.push_failed = int(failed)
        row.push_settled = True
        self.db_session.commit()
        return True

    def recent(self, limit: int = 50, sales_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Newest notifications first, optionally for one order."""
        query = self.db_session.query(NotificationLog)
        if sales_id:
            query = query.filter(NotificationLog.sales_id == str(sales_id))
        rows = query.order_by(desc(NotificationLog.created_at)).limit(limit).all()
        return [
            {
                "event_id": row.event_id,
                "sales_id": row.sales_id,
                "event_type": row.event_type,
                "title": row.title,
                "body": row.body,
                "icon": row.icon,
                "urgency": row.urgency,
                "push_devices": row.push_devices,
                "push_sent": row.push_sent,
                "push_failed": row.push_failed,
                "push_settled": row.push_settled,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    def prune(self, older_than_days: int) -> int:
        """Drop history past the retention window.

        Called from the read path rather than a scheduler: this service has no
        cron, an operator opens this view rarely, and the alternative is a table
        that grows forever. Never raises — losing a prune is not worth failing
        the request the operator actually asked for.
        """
        if older_than_days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        try:
            removed = (
                self.db_session.query(NotificationLog)
                .filter(NotificationLog.created_at < cutoff)
                .delete(synchronize_session=False)
            )
            self.db_session.commit()
            if removed:
                logger.info("Pruned %d notification log row(s) older than %dd", removed, older_than_days)
            return int(removed or 0)
        except Exception:
            self.db_session.rollback()
            logger.warning("Notification log prune failed", exc_info=True)
            return 0

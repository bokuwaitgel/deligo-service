from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import delete, func
from sqlalchemy.orm import Session

from schemas.database.push_subscription_db import PushSubscription

logger = logging.getLogger(__name__)


class PushSubscriptionRepository:
    def __init__(self, db_session: Session):
        self.db_session = db_session

    def list_for_sales_id(self, sales_id: str) -> List[PushSubscription]:
        return (
            self.db_session.query(PushSubscription)
            .filter(PushSubscription.sales_id == str(sales_id))
            .all()
        )

    def get(self, sales_id: str, endpoint: str) -> Optional[PushSubscription]:
        return (
            self.db_session.query(PushSubscription)
            .filter(
                PushSubscription.sales_id == str(sales_id),
                PushSubscription.endpoint == endpoint,
            )
            .first()
        )

    def upsert(
        self,
        sales_id: str,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: Optional[str] = None,
    ) -> PushSubscription:
        """Register (or refresh) one browser's push endpoint for one order.

        Resubscribing is the normal case — browsers rotate the endpoint whenever
        the push service asks them to — so this must never create a duplicate.
        """
        existing = self.get(sales_id, endpoint)
        if existing is not None:
            existing.p256dh = p256dh
            existing.auth = auth
            existing.user_agent = user_agent
            existing.failure_count = 0
            existing.last_seen_at = datetime.now(timezone.utc)
            self.db_session.commit()
            self.db_session.refresh(existing)
            return existing

        row = PushSubscription(
            id=uuid.uuid4().hex,
            sales_id=str(sales_id),
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=user_agent,
        )
        self.db_session.add(row)
        self.db_session.commit()
        self.db_session.refresh(row)
        return row

    def delete_endpoint(self, endpoint: str, sales_id: Optional[str] = None) -> int:
        """Drop a subscription. Without ``sales_id`` every order it covers goes."""
        stmt = delete(PushSubscription).where(PushSubscription.endpoint == endpoint)
        if sales_id:
            stmt = stmt.where(PushSubscription.sales_id == str(sales_id))
        result = self.db_session.execute(stmt)
        self.db_session.commit()
        return int(getattr(result, "rowcount", 0) or 0)

    def delete_for_sales_id(self, sales_id: str) -> int:
        result = self.db_session.execute(
            delete(PushSubscription).where(PushSubscription.sales_id == str(sales_id))
        )
        self.db_session.commit()
        return int(getattr(result, "rowcount", 0) or 0)

    def mark_failure(self, subscription_id: str) -> None:
        """Count a soft send failure. Never raises — the caller is a background send."""
        try:
            row = self.db_session.get(PushSubscription, subscription_id)
            if row is None:
                return
            row.failure_count = int(row.failure_count or 0) + 1
            self.db_session.commit()
        except Exception:
            self.db_session.rollback()
            logger.debug("Could not record push failure for %s", subscription_id, exc_info=True)

    def prune_stale(self, max_age_days: int = 30) -> int:
        """Delete subscriptions nobody has refreshed in a long time.

        A tracking link is only useful for a day or two, so a month-old row is
        an order that finished long ago; keeping it only costs send attempts
        against endpoints the browser has already forgotten.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        result = self.db_session.execute(
            delete(PushSubscription).where(PushSubscription.last_seen_at < cutoff)
        )
        self.db_session.commit()
        return int(getattr(result, "rowcount", 0) or 0)

    def count(self) -> int:
        return int(self.db_session.query(func.count(PushSubscription.id)).scalar() or 0)

    def count_for_sales_id(self, sales_id: str) -> int:
        return int(
            self.db_session.query(func.count(PushSubscription.id))
            .filter(PushSubscription.sales_id == str(sales_id))
            .scalar()
            or 0
        )

    def recent_orders(self, limit: int = 25) -> List[dict]:
        """Orders with live subscriptions, most recently refreshed first.

        Grouped rather than listed row by row: the admin panel cares how many
        devices an order can reach, not which push service each one uses.
        """
        rows = (
            self.db_session.query(
                PushSubscription.sales_id,
                func.count(PushSubscription.id).label("devices"),
                func.max(PushSubscription.last_seen_at).label("last_seen_at"),
                func.sum(PushSubscription.failure_count).label("failures"),
            )
            .group_by(PushSubscription.sales_id)
            .order_by(func.max(PushSubscription.last_seen_at).desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "sales_id": row.sales_id,
                "devices": int(row.devices or 0),
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
                "failures": int(row.failures or 0),
            }
            for row in rows
        ]

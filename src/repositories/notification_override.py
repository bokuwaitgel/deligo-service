from __future__ import annotations

import logging
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from schemas.database.notification_override_db import (
    NotificationRuleOverride,
    NotificationTemplateOverride,
)

logger = logging.getLogger(__name__)

# The only template fields the admin panel may edit. `url` and `tag` are absent
# on purpose: both are composed by `build_notification` from the tracking prefix
# and the order id, and an operator editing them would break the click-through
# link or the one-notification-per-event-type replacement rule.
EDITABLE_TEMPLATE_FIELDS = ("title", "body", "icon", "urgency", "icon_image_id")

# Urgency is a closed set — the frontend switches on it for toast styling and
# the service worker for `requireInteraction`.
VALID_URGENCIES = ("low", "normal", "high")


class NotificationTemplateOverrideRepository:
    """Operator edits to notification wording, keyed by event type."""

    def __init__(self, db_session: Session):
        self.db_session = db_session

    def list_all(self) -> List[NotificationTemplateOverride]:
        return self.db_session.query(NotificationTemplateOverride).all()

    def as_dict(self) -> Dict[str, Dict[str, object]]:
        """Overrides keyed by event type, with NULL (inherit) fields dropped.

        Shaped for `notifications.apply_template_overrides`, which merges
        field-by-field — a row with only `body` set must not blank out the icon
        it says nothing about.
        """
        result: Dict[str, Dict[str, object]] = {}
        for row in self.list_all():
            fields = {
                f: getattr(row, f)
                for f in EDITABLE_TEMPLATE_FIELDS
                if getattr(row, f) is not None
            }
            if not fields:
                continue
            fields["_updated_at"] = row.updated_at.isoformat() if row.updated_at else None
            fields["_updated_by"] = row.updated_by
            result[row.event_type] = fields
        return result

    def get(self, event_type: str) -> Optional[NotificationTemplateOverride]:
        return (
            self.db_session.query(NotificationTemplateOverride)
            .filter(NotificationTemplateOverride.event_type == str(event_type))
            .first()
        )

    def upsert(
        self,
        event_type: str,
        fields: Dict[str, Optional[str]],
        updated_by: Optional[str] = None,
    ) -> NotificationTemplateOverride:
        """Set (or clear) individual fields for one event type.

        A key present with value ``None`` (or blank) clears that field back to
        inherit; a key absent from ``fields`` is left as it was. That is what
        lets "revert the icon" work without also reverting the body somebody
        rewrote last week.
        """
        row = self.get(event_type)
        if row is None:
            row = NotificationTemplateOverride(event_type=str(event_type))
            self.db_session.add(row)

        for field in EDITABLE_TEMPLATE_FIELDS:
            if field in fields:
                value = fields[field]
                setattr(
                    row,
                    field,
                    value.strip() if isinstance(value, str) and value.strip() else None,
                )

        row.updated_by = updated_by or row.updated_by
        self.db_session.commit()
        self.db_session.refresh(row)
        return row

    def delete(self, event_type: str) -> bool:
        """Drop every override for one event type — back to the defaults."""
        row = self.get(event_type)
        if row is None:
            return False
        self.db_session.delete(row)
        self.db_session.commit()
        return True


class NotificationRuleOverrideRepository:
    """Which notification fires for a wfm status, keyed by status id."""

    def __init__(self, db_session: Session):
        self.db_session = db_session

    def list_all(self) -> List[NotificationRuleOverride]:
        return self.db_session.query(NotificationRuleOverride).all()

    def as_dict(self) -> Dict[int, Dict[str, object]]:
        """Rules keyed by wfm status id.

        Unlike the template map this keeps rows whose ``event_type`` is NULL:
        a muted row carries its meaning in the ``muted`` flag alone.
        """
        result: Dict[int, Dict[str, object]] = {}
        for row in self.list_all():
            result[row.wfm_status_id] = {
                "event_type": row.event_type,
                "muted": bool(row.muted),
                "_updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "_updated_by": row.updated_by,
            }
        return result

    def get(self, wfm_status_id: int) -> Optional[NotificationRuleOverride]:
        return (
            self.db_session.query(NotificationRuleOverride)
            .filter(NotificationRuleOverride.wfm_status_id == int(wfm_status_id))
            .first()
        )

    def upsert(
        self,
        wfm_status_id: int,
        event_type: Optional[str] = None,
        muted: Optional[bool] = None,
        updated_by: Optional[str] = None,
        *,
        set_event_type: bool = False,
    ) -> NotificationRuleOverride:
        """Point one status at a different notification, and/or mute it.

        ``set_event_type`` distinguishes "the caller sent event_type: null,
        meaning revert to the default mapping" from "the caller only toggled
        mute and said nothing about the event type". Without it, muting would
        silently wipe a chosen event type.
        """
        row = self.get(wfm_status_id)
        if row is None:
            row = NotificationRuleOverride(wfm_status_id=int(wfm_status_id))
            self.db_session.add(row)

        if set_event_type:
            row.event_type = (
                event_type.strip() if isinstance(event_type, str) and event_type.strip() else None
            )
        if muted is not None:
            row.muted = bool(muted)

        row.updated_by = updated_by or row.updated_by
        self.db_session.commit()
        self.db_session.refresh(row)
        return row

    def delete(self, wfm_status_id: int) -> bool:
        """Drop the rule for one status — back to WFM_STATUS_EVENT_TYPES."""
        row = self.get(wfm_status_id)
        if row is None:
            return False
        self.db_session.delete(row)
        self.db_session.commit()
        return True

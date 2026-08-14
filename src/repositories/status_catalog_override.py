from __future__ import annotations

import logging
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from schemas.database.status_catalog_override_db import StatusCatalogOverride

logger = logging.getLogger(__name__)

# The only fields the admin panel may override. Anything structural
# (`wfm_status_id`, `status_code`, `group`, `is_closed`, …) stays owned by the
# source catalog: those drive behaviour — which orders count as closed, which
# are hidden from the customer — and an operator changing them from a colour
# picker would silently re-route logic, not restyle a chip.
EDITABLE_FIELDS = ("icon", "color", "pin_color", "label", "description")


class StatusCatalogOverrideRepository:
    def __init__(self, db_session: Session):
        self.db_session = db_session

    def list_all(self) -> List[StatusCatalogOverride]:
        return self.db_session.query(StatusCatalogOverride).all()

    def as_dict(self) -> Dict[int, Dict[str, object]]:
        """Overrides keyed by wfm id, with the NULL (inherit) fields dropped.

        Shaped for `status_catalog.apply_overrides`, which merges field-by-field
        rather than row-by-row — a row with only `icon` set must not blank out
        the colour it says nothing about.
        """
        result: Dict[int, Dict[str, object]] = {}
        for row in self.list_all():
            fields = {f: getattr(row, f) for f in EDITABLE_FIELDS if getattr(row, f) is not None}
            if not fields:
                continue
            fields["_updated_at"] = row.updated_at.isoformat() if row.updated_at else None
            fields["_updated_by"] = row.updated_by
            result[row.wfm_status_id] = fields
        return result

    def get(self, wfm_status_id: int) -> Optional[StatusCatalogOverride]:
        return (
            self.db_session.query(StatusCatalogOverride)
            .filter(StatusCatalogOverride.wfm_status_id == int(wfm_status_id))
            .first()
        )

    def upsert(
        self,
        wfm_status_id: int,
        fields: Dict[str, Optional[str]],
        updated_by: Optional[str] = None,
    ) -> StatusCatalogOverride:
        """Set (or clear) individual fields for one status.

        A key present with value ``None`` clears that field back to inherit;
        a key absent from ``fields`` is left as it was. That distinction is what
        lets the panel's "revert icon" button work without also reverting the
        colour someone set last week.
        """
        row = self.get(wfm_status_id)
        if row is None:
            row = StatusCatalogOverride(wfm_status_id=int(wfm_status_id))
            self.db_session.add(row)

        for field in EDITABLE_FIELDS:
            if field in fields:
                value = fields[field]
                setattr(row, field, value.strip() if isinstance(value, str) and value.strip() else None)

        row.updated_by = updated_by or row.updated_by
        self.db_session.commit()
        self.db_session.refresh(row)
        return row

    def delete(self, wfm_status_id: int) -> bool:
        """Drop every override for one status — full revert to the source."""
        row = self.get(wfm_status_id)
        if row is None:
            return False
        self.db_session.delete(row)
        self.db_session.commit()
        return True

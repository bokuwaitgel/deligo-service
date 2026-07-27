"""Tiny forward-only schema patches applied at startup.

The service has no Alembic setup — tables are created by
``Base.metadata.create_all`` in the app lifespan. ``create_all`` creates missing
TABLES but never adds missing COLUMNS to a table that already exists, so any
column added to an existing model needs an explicit ``ALTER TABLE`` here.

Every statement must be idempotent (``IF NOT EXISTS``) because it runs on every
boot of every replica.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# (table, column, DDL type) — appended to as models grow.
_ADD_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # Package 1: address-edit attribution shown on the order detail.
    ("delivery_orders", "location_updated_at", "TIMESTAMPTZ"),
    ("delivery_orders", "location_updated_by", "VARCHAR"),
    ("delivery_orders", "location_updated_by_name", "VARCHAR"),
)


def apply_schema_patches(engine: Engine) -> None:
    """Add any columns that exist on the models but not yet in the database."""
    if engine.dialect.name != "postgresql":
        # SQLite (tests) is rebuilt from the models by create_all, so there is
        # nothing to patch — and it has no ADD COLUMN IF NOT EXISTS.
        return

    with engine.begin() as conn:
        for table, column, ddl_type in _ADD_COLUMNS:
            try:
                conn.execute(
                    text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl_type}')
                )
            except Exception:
                # A missing table is fine — create_all will build it with the
                # column already present. Anything else is worth a loud log but
                # must not stop the API from booting.
                logger.warning(
                    "Schema patch failed: %s.%s (%s)", table, column, ddl_type, exc_info=True
                )

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
from typing import Iterable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)

# Any 64-bit constant works; every replica just has to pick the same one.
# Derived from the service name so a different app sharing this database does
# not block on us.
_SCHEMA_LOCK_ID = 8_143_552_907_311_004_21


def ensure_schema(engine: Engine, bases: Iterable[type]) -> None:
    """Create any missing tables, safely when several replicas boot at once.

    ``create_all`` checks which tables exist and then emits ``CREATE TABLE`` for
    the rest. Those two steps are not atomic, so N replicas starting together
    all see the same table missing and all try to create it: one wins and the
    others take a UniqueViolation on ``pg_type``, which used to escape the
    lifespan and kill the container. With four replicas and a restart policy,
    that is a total outage the moment a new table is added.

    A Postgres advisory lock serialises the whole check-and-create so only one
    replica does DDL; the rest wait, then find nothing to create. The lock is
    released with the transaction, including if the process dies holding it.

    The try/except is still there for the cases the lock cannot cover: SQLite
    (tests) has no advisory locks, and a database user without permission to
    take one must not be turned into a boot failure. A table another replica
    created a moment ago is a success for us, not an error.
    """
    bases = tuple(bases)

    if engine.dialect.name == "postgresql":
        try:
            with engine.begin() as conn:
                conn.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _SCHEMA_LOCK_ID})
                for base in bases:
                    base.metadata.create_all(conn)
            return
        except DBAPIError:
            # Fall through to the unlocked path, which tolerates the race per
            # table rather than failing the whole boot.
            logger.warning("Locked schema creation failed — retrying per table", exc_info=True)

    for base in bases:
        try:
            base.metadata.create_all(engine)
        except DBAPIError as exc:
            # Losing the race is a success for us: the table we wanted exists.
            # Rather than pattern-matching driver-specific "already exists"
            # errors, check the end state — that is what we actually care
            # about, and it re-raises a genuine failure (no permission, no
            # connection) instead of booting a replica with no schema.
            missing = _missing_tables(engine, base)
            if missing:
                logger.error("Schema creation failed for %s: %s", missing, exc)
                raise
            logger.warning(
                "Schema creation raced for %s — tables already present, continuing",
                getattr(base, "__name__", base),
            )


def _missing_tables(engine: Engine, base: type) -> list[str]:
    """Which of ``base``'s tables are still absent after a failed create_all."""
    try:
        present = set(inspect(engine).get_table_names())
    except Exception:
        # If we cannot even inspect, treat every table as missing so the caller
        # re-raises rather than assuming the schema is fine.
        return sorted(base.metadata.tables)
    return sorted(name for name in base.metadata.tables if name not in present)

# (table, column, DDL type) — appended to as models grow.
_ADD_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # Package 1: address-edit attribution shown on the order detail.
    ("delivery_orders", "location_updated_at", "TIMESTAMPTZ"),
    ("delivery_orders", "location_updated_by", "VARCHAR"),
    ("delivery_orders", "location_updated_by_name", "VARCHAR"),
    # Deligo sync bookkeeping. NOT NULL DEFAULT TRUE matches the model default
    # and backfills existing rows as active in one pass — a row that predates
    # this column was, by definition, still being synced.
    ("delivery_orders", "sync_active", "BOOLEAN NOT NULL DEFAULT TRUE"),
    # Upstream order status ('salesNew', 'salesDelivery', ...). Nullable: an
    # order the sync has not classified yet has no status, and the read paths
    # filter with IN(...) which already excludes NULL.
    ("delivery_orders", "status", "VARCHAR"),
    # NULL = the queue-position notification has not gone out for this order,
    # which is the correct reading for every row that predates the column.
    ("delivery_orders", "queue_alert_sent_at", "TIMESTAMPTZ"),
    # Uploaded image shown instead of the Material Symbols glyph.
    ("notification_template_overrides", "icon_image_id", "VARCHAR(64)"),
)

# (index, table, column list) — the Index() declarations on the models, repeated
# here because create_all only builds indexes alongside the CREATE TABLE it
# emits. An index added to a model whose table already exists is never created,
# and the miss is invisible: queries stay correct and just get slower.
_ADD_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_delivery_orders_sales_number", "delivery_orders", "sales_number"),
    ("ix_delivery_orders_customer_address", "delivery_orders", "customer_address"),
    (
        "ix_delivery_active_by_driver",
        "delivery_orders",
        "driver_id, sync_active, status",
    ),
)


def apply_schema_patches(engine: Engine) -> None:
    """Add any columns and indexes on the models but not yet in the database."""
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

        # After the columns: a composite index can reference one that was only
        # just added.
        for index, table, columns in _ADD_INDEXES:
            try:
                conn.execute(
                    text(f'CREATE INDEX IF NOT EXISTS {index} ON {table} ({columns})')
                )
            except Exception:
                # Same reasoning as above, and one step softer: a missing index
                # only costs speed, never correctness.
                logger.warning("Index patch failed: %s on %s(%s)", index, table, columns, exc_info=True)

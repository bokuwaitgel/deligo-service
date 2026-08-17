from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

logger = logging.getLogger(__name__)

_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


def _get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        conn_str = os.getenv("DATABASE_URL", "")
        if not conn_str:
            raise RuntimeError("DATABASE_URL environment variable is not set")
        # Total connections to Postgres = API_REPLICAS * WORKERS * (pool_size + max_overflow).
        # Keep that product under the server's max_connections (default 100).
        # Defaults below: 4 replicas * 2 workers * (5 + 5) = 80, leaving headroom
        # for seed jobs / admin sessions. Tune via env without a code change.
        pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
        max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "5"))
        pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))
        _ENGINE = create_engine(
            conn_str,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=300,
        )
        logger.info(
            "SQLAlchemy engine created: %s (pool_size=%d max_overflow=%d)",
            conn_str.split("@")[-1] if "@" in conn_str else conn_str[:40],
            pool_size,
            max_overflow,
        )
    return _ENGINE


def _get_session_factory() -> sessionmaker[Session]:
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = sessionmaker(bind=_get_engine())
    return _SESSION_FACTORY


def get_delivery_repository():
    from src.repositories.delivery import DeliveryRepository

    session = _get_session_factory()()
    try:
        yield DeliveryRepository(session)
    finally:
        session.close()


def get_driver_location_repository():
    from src.repositories.driver_location import DriverLocationRepository

    session = _get_session_factory()()
    try:
        yield DriverLocationRepository(session)
    finally:
        session.close()


def get_push_subscription_repository():
    from src.repositories.push_subscription import PushSubscriptionRepository

    session = _get_session_factory()()
    try:
        yield PushSubscriptionRepository(session)
    finally:
        session.close()


def get_status_catalog_override_repository():
    from src.repositories.status_catalog_override import StatusCatalogOverrideRepository

    session = _get_session_factory()()
    try:
        yield StatusCatalogOverrideRepository(session)
    finally:
        session.close()


def get_notification_template_override_repository():
    from src.repositories.notification_override import NotificationTemplateOverrideRepository

    session = _get_session_factory()()
    try:
        yield NotificationTemplateOverrideRepository(session)
    finally:
        session.close()


def get_notification_log_repository():
    from src.repositories.notification_log import NotificationLogRepository

    session = _get_session_factory()()
    try:
        yield NotificationLogRepository(session)
    finally:
        session.close()


def get_notification_icon_repository():
    from src.repositories.notification_icon import NotificationIconRepository

    session = _get_session_factory()()
    try:
        yield NotificationIconRepository(session)
    finally:
        session.close()


def get_notification_rule_override_repository():
    from src.repositories.notification_override import NotificationRuleOverrideRepository

    session = _get_session_factory()()
    try:
        yield NotificationRuleOverrideRepository(session)
    finally:
        session.close()

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import Depends
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
        # expire_on_commit=False: without it every attribute read after a commit
        # issues a fresh SELECT, which needs a connection back out of the pool at
        # the worst possible moment — while writing the response. That reload is
        # what turned a busy pool into 500s on POST /api/drivers/{id}/location.
        _SESSION_FACTORY = sessionmaker(bind=_get_engine(), expire_on_commit=False)
    return _SESSION_FACTORY


def configure_thread_limiter() -> None:
    """Cap concurrent sync request handlers at what the DB pool can serve.

    Endpoints are sync ``def``, so Starlette runs them on anyio's thread pool —
    40 threads by default. Each one checks out a pooled connection, so 40 threads
    chasing pool_size+max_overflow=10 connections means 30 of them wait out
    ``pool_timeout`` and then fail with ``QueuePool limit ... reached``.

    Limiting the thread pool instead makes the excess requests queue for a
    *thread* (cheap, no connection held, no 30s timer) and each running handler
    find a free connection. Back-pressure replaces 500s.
    """
    try:
        import anyio.to_thread

        pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
        max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "5"))
        # Headroom for the background senders (webpush / deligo-notify), which
        # open their own sessions off the request path.
        reserved = int(os.getenv("DB_BACKGROUND_RESERVE", "2"))
        limit = int(os.getenv("API_THREAD_LIMIT", str(max(1, pool_size + max_overflow - reserved))))
        anyio.to_thread.current_default_thread_limiter().total_tokens = limit
        logger.info("Request thread pool limited to %d concurrent handlers", limit)
    except Exception:
        logger.warning("Could not configure the request thread limiter", exc_info=True)


def get_db_session():
    """One session — and so at most one pooled connection — per request.

    Repositories depend on this rather than opening their own session: FastAPI
    caches a dependency per request, so an endpoint taking two repositories used
    to check out two connections for the whole request.
    """
    session = _get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def release_connection(session: Session) -> None:
    """Hand this request's connection back to the pool mid-request.

    Call before a slow upstream (Deligo) round-trip: a session that has run a
    query holds its connection until close, and holding it idle across seconds
    of HTTP is what drains the pool. The session stays usable — the next query
    opens a new transaction on a freshly checked-out connection. Rows already
    loaded stay readable because the factory sets ``expire_on_commit=False``.
    """
    try:
        session.close()
    except Exception:
        logger.warning("Could not release the DB connection early", exc_info=True)


def get_delivery_repository(session: Session = Depends(get_db_session)):
    from src.repositories.delivery import DeliveryRepository

    return DeliveryRepository(session)


def get_driver_location_repository(session: Session = Depends(get_db_session)):
    from src.repositories.driver_location import DriverLocationRepository

    return DriverLocationRepository(session)


def get_push_subscription_repository(session: Session = Depends(get_db_session)):
    from src.repositories.push_subscription import PushSubscriptionRepository

    return PushSubscriptionRepository(session)


def get_status_catalog_override_repository(session: Session = Depends(get_db_session)):
    from src.repositories.status_catalog_override import StatusCatalogOverrideRepository

    return StatusCatalogOverrideRepository(session)


def get_notification_template_override_repository(session: Session = Depends(get_db_session)):
    from src.repositories.notification_override import NotificationTemplateOverrideRepository

    return NotificationTemplateOverrideRepository(session)


def get_notification_log_repository(session: Session = Depends(get_db_session)):
    from src.repositories.notification_log import NotificationLogRepository

    return NotificationLogRepository(session)


def get_notification_rule_override_repository(session: Session = Depends(get_db_session)):
    from src.repositories.notification_override import NotificationRuleOverrideRepository

    return NotificationRuleOverrideRepository(session)

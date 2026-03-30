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
        _ENGINE = create_engine(
            conn_str,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=300,
        )
        logger.info("SQLAlchemy engine created: %s", conn_str.split("@")[-1] if "@" in conn_str else conn_str[:40])
    return _ENGINE


def _get_session_factory() -> sessionmaker[Session]:
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = sessionmaker(bind=_get_engine())
    return _SESSION_FACTORY


def get_order_repository():
    from src.repositories.order import OrderRepository

    session = _get_session_factory()()
    try:
        yield OrderRepository(session)
    finally:
        session.close()

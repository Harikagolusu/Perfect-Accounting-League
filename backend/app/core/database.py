"""SQLAlchemy engine, session factory, declarative base and FastAPI dependency.

Creating the engine is lazy - importing this module never opens a database
connection, so the application (and the liveness probe) starts even when
PostgreSQL is temporarily unavailable.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    echo=settings.debug,
    # Guards against stale connections dropped by a database restart.
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Declarative base class for every PAL ORM model."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
"""Shared pytest fixtures.

Tests run against a real PostgreSQL database (`pal_test_db` by default, override
with `TEST_DATABASE_URL`) rather than SQLite, so the schema under test is the
exact one Alembic manages. The dev database is never touched.
"""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Registers every ORM model on Base.metadata, which is what create_all() builds
# from. Imported explicitly rather than relying on app.main pulling it in.
import app.models  # noqa: F401  (imported for its registration side effect)
from app.core.database import Base, get_db
from app.main import app

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/pal_test_db",
)

test_engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(
    bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False
)


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    """Create the tables once per test session and drop them afterwards."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(autouse=True)
def clean_tables() -> Iterator[None]:
    """Leave every test with empty tables, so tests can run in any order."""
    with test_engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield


@pytest.fixture
def db() -> Iterator[Session]:
    """A session the app's dependency is overridden to use."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    """Test client wired to the test session instead of the real engine."""

    def override_get_db() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
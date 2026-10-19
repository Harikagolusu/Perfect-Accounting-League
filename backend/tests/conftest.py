"""Shared pytest fixtures.

Tests run against a real PostgreSQL database (`pal_test_db` by default, override
with `TEST_DATABASE_URL`) rather than SQLite, so the schema under test is the
exact one Alembic manages. The dev database is never touched.

Uploads are redirected to a temporary directory for the whole session, so
running the suite can never write into the project's real `storage/` folder or
delete a developer's files.
"""

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Registers every ORM model on Base.metadata, which is what create_all() builds
# from. Imported explicitly rather than relying on app.main pulling it in.
import app.models  # noqa: F401  (imported for its registration side effect)
from app.core.config import get_settings
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
def temp_storage(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Point document storage at a throwaway directory for the test session."""
    previous = os.environ.get("DOCUMENT_STORAGE_PATH")
    root = tmp_path_factory.mktemp("pal-document-storage")
    os.environ["DOCUMENT_STORAGE_PATH"] = str(root)
    get_settings.cache_clear()
    try:
        yield root
    finally:
        if previous is None:
            os.environ.pop("DOCUMENT_STORAGE_PATH", None)
        else:
            os.environ["DOCUMENT_STORAGE_PATH"] = previous
        get_settings.cache_clear()


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    """Create the tables once per test session and drop them afterwards."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(autouse=True)
def clean_state(temp_storage: Path) -> Iterator[None]:
    """Empty the tables and the temp storage root before every test.

    Uploading is a filesystem side effect, so clearing only the database would
    let one test's files leak into the next one's assertions.
    """
    with test_engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    for child in temp_storage.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink()
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
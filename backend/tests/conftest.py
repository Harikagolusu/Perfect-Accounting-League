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
def no_real_ai_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the real AI provider out of every test by default.

    A developer's local `.env` may contain a real key. Overriding the variable
    with an empty string takes precedence over the dotenv file, so no test can
    accidentally spend money or depend on network access. A test that needs a
    key sets its own with `monkeypatch.setenv` after this fixture runs.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    # A key saved from Settings lives in the database; read the test one.
    monkeypatch.setattr(
        "app.services.ai.key_store.session_factory", TestingSessionLocal
    )
    # Deterministic page-limit behaviour regardless of a developer's .env.
    monkeypatch.setenv("INVOICE_MAX_PAGES", "10")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


class _AcceptingTally:
    """Stand-in TallyService: every ledger create is accepted, nothing is sent."""

    def __init__(self) -> None:
        self.created: list[dict] = []

    def create_ledger(self, **kwargs):
        from app.integrations.tally.parser import TallyImportResult

        self.created.append(kwargs)
        return TallyImportResult(
            created=1, altered=0, ignored=0, errors=0, exceptions=0,
            cancelled=0, last_voucher_id=None, message=None,
        )

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def fake_tally_ledger_create(monkeypatch: pytest.MonkeyPatch) -> _AcceptingTally:
    """Keep real TallyPrime out of ledger/vendor creation in every test.

    Creating a ledger or vendor now mirrors it into Tally first (block
    semantics). Tests get an accepting stub by default; a test that needs a
    rejection passes its own `service=` or re-patches `get_tally_service`.
    """
    fake = _AcceptingTally()
    monkeypatch.setattr("app.services.ledger.get_tally_service", lambda: fake)
    return fake


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
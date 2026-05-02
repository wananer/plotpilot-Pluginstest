"""Fixtures for API integration tests."""

import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from infrastructure.persistence.database.connection import DatabaseConnection
from infrastructure.persistence.database.sqlite_entity_base_repository import (
    SqliteEntityBaseRepository
)
from infrastructure.persistence.database.sqlite_narrative_event_repository import (
    SqliteNarrativeEventRepository
)

# pathlib: parents[0]==parent；v1/conftest.py → 仓库根为 parents[5]
SCHEMA_PATH = (
    Path(__file__).resolve().parents[5]
    / "infrastructure"
    / "persistence"
    / "database"
    / "schema.sql"
)


@pytest.fixture
def db(isolated_data_dir, monkeypatch):
    """File-backed SQLite fixture aligned with path-based repositories."""
    db = DatabaseConnection(str(isolated_data_dir / "aitext.db"))

    from infrastructure.persistence.database import connection

    connection._db_instance = db
    yield db
    db.close()


@pytest.fixture
def client(db, monkeypatch):
    """FastAPI test client with mocked database."""
    # Mock get_database to return our test database
    def mock_get_database():
        return db

    monkeypatch.setattr(
        "infrastructure.persistence.database.connection.get_database",
        mock_get_database,
    )
    # dependencies 内 `from connection import get_database` 会绑定旧引用，需同步 patch
    monkeypatch.setattr(
        "interfaces.api.dependencies.get_database",
        mock_get_database,
    )
    monkeypatch.setattr(
        "infrastructure.persistence.database.connection._db_instance",
        db,
    )

    # Import app after monkeypatching
    from interfaces.main import app
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def test_novel_id(db):
    """Create a test novel and return its ID."""
    novel_id = "test-novel-1"
    db.execute(
        "INSERT INTO novels (id, title, slug, target_chapters) VALUES (?, ?, ?, ?)",
        (novel_id, "Test Novel", "test-novel", 10)
    )
    db.get_connection().commit()
    return novel_id


@pytest.fixture
def test_entity_id(db, test_novel_id):
    """Create a test entity and return its ID."""
    entity_id = "test-entity-1"
    core_attributes = {
        "name": "John Doe",
        "age": 30,
        "occupation": "Detective"
    }

    db.execute(
        "INSERT INTO entity_bases (id, novel_id, entity_type, core_attributes) VALUES (?, ?, ?, ?)",
        (entity_id, test_novel_id, "character", str(core_attributes))
    )
    db.get_connection().commit()
    return entity_id

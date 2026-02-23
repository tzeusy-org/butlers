"""Shared fixtures for migration integration tests.

The canonical helper functions live in ``butlers.testing.migration`` (importable
from any test tree).  This conftest re-exports them for convenience and adds a
pytest fixture that wires them together.
"""

from __future__ import annotations

import pytest

from butlers.testing.migration import (  # noqa: F401 – re-exported for import convenience
    constraint_exists,
    create_migration_db,
    get_column_info,
    index_exists,
    migration_db_name,
    table_exists,
)


@pytest.fixture
def fresh_migration_db(postgres_container):
    """Yield a fresh PostgreSQL database URL for one migration test run.

    Uses the session-scoped *postgres_container* fixture from the root conftest.
    Each test that requests this fixture gets a brand-new, empty database.
    """
    db_url = create_migration_db(postgres_container, migration_db_name())
    yield db_url

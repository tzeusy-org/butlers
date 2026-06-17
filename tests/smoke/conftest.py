"""Smoke-tier shared fixtures.

The smoke test tier is the fast operational gate:

- Tests are marked ``@pytest.mark.smoke`` and selectable via ``pytest -m smoke``.
- **No test in this tier spawns a real LLM CLI.**  Intercept spawner calls
  with ``mock_spawner`` (provided by the root conftest via
  ``butlers.testing.shared_fixtures``).
- DB-backed smoke tests borrow the session-scoped ``postgres_container``
  fixture from the root conftest — no new container is started per test file.

Canonical smoke test pattern::

    import shutil
    import pytest
    from butlers.testing.shared_fixtures import MockSpawner, SpawnerResult

    pytestmark = pytest.mark.smoke
    _docker_available = shutil.which("docker") is not None


    async def test_mock_flow(mock_spawner: MockSpawner):
        mock_spawner.enqueue_result(SpawnerResult(output="ok", success=True))
        result = await mock_spawner.spawn(prompt="test")
        assert result.success


    @pytest.mark.skipif(not _docker_available, reason="Docker not available")
    def test_db_reachable(postgres_container):
        assert postgres_container.get_exposed_port(5432)

DB-backed fixtures
------------------
``smoke_db_url`` provisions a fresh, extension-bootstrapped database (no
migrations applied) backed by the session ``postgres_container``.  Use it
for raw connectivity checks; run ``butlers.migrations.run_migrations``
explicitly when you need a schema.
"""

from __future__ import annotations

import shutil

import pytest

from butlers.testing.migration import create_migration_db, migration_db_name

# Re-export so ``from tests.smoke.conftest import mock_spawner`` keeps working
# in smoke test files that prefer a direct import.
from butlers.testing.shared_fixtures import (  # noqa: F401
    MockSpawner,
    SpawnerResult,
    mock_spawner,
)

__all__ = ["MockSpawner", "SpawnerResult", "mock_spawner"]

_docker_available = shutil.which("docker") is not None


@pytest.fixture(scope="module")
def smoke_db_url(postgres_container):
    """Fresh PostgreSQL database for one smoke test module.

    Backed by the session-scoped ``postgres_container``; required extensions
    (vector, pgcrypto, uuid-ossp, pg_trgm) are bootstrapped automatically but
    NO Alembic migrations are applied.  One database is created per module so
    schema state cannot leak across smoke test files.

    Guard callers with::

        @pytest.mark.skipif(not _docker_available, reason="Docker not available")
    """
    db_name = migration_db_name()
    return create_migration_db(postgres_container, db_name)

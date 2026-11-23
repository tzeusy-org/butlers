"""core_364: the shared ``public.owner_watches`` table over two butler schemas (bu-4c7nvp.1.5).

The core chain runs once per butler schema over this shared table, so the upgrade is
idempotent, a downgrade of one schema keeps the table for the other, and only the last schema to
leave drops it, and only while it is empty. Real migrated databases only; synthetic rows.
"""

from __future__ import annotations

import shutil
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
    table_exists,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_FIRST, _SECOND = "general", "health"
_ROW = (
    "INSERT INTO public.owner_watches (id, version, generation, predicate, predicate_digest, "
    "grant_digest, sources, categories, delivery, state, created_at, expires_at, approval_digest)"
    ' VALUES (\'w1\', 1, 1, \'{"tag":"bundle","bundle_id":"x"}\'::jsonb, repeat(\'a\', 64), '
    "repeat('a', 64), '{bundle:x}', '{}', 'digest', 'active', now(), now() + interval '1 day', "
    "repeat('a', 64))"
)


def _run(url: str, statement: str) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(statement))
    finally:
        engine.dispose()


def test_two_schemas_share_one_table_and_only_the_last_to_leave_drops_it(postgres_container):
    name = migration_db_name()
    db_url = create_migration_db(postgres_container, name)
    first = _build_alembic_config(db_url, chains=["core"], target_schema=_FIRST)
    second = _build_alembic_config(db_url, chains=["core"], target_schema=_SECOND)

    command.upgrade(first, "core_364")
    command.upgrade(second, "core_364")
    command.upgrade(second, "core_364")  # an idempotent replay
    assert table_exists(db_url, "owner_watches")

    _run(db_url, _ROW)
    command.downgrade(first, "core_363")
    assert table_exists(db_url, "owner_watches")  # the other schema still depends on it

    with pytest.raises(Exception, match="cannot drop the owner watches"):
        command.downgrade(second, "core_363")
    assert table_exists(db_url, "owner_watches")  # a populated table is never dropped

    bootstrap = migration_bootstrap_db_url(postgres_container, urlparse(db_url).path.lstrip("/"))
    _run(bootstrap, "ALTER TABLE public.owner_watches DISABLE TRIGGER ALL")
    _run(bootstrap, "DELETE FROM public.owner_watches")
    _run(bootstrap, "ALTER TABLE public.owner_watches ENABLE TRIGGER ALL")
    command.downgrade(second, "core_363")
    assert not table_exists(db_url, "owner_watches")

    command.upgrade(first, "core_364")
    assert table_exists(db_url, "owner_watches")

"""core_358 clears only the seeded 7-day fact horizon (bu-uhbhzq.3), on migrated PostgreSQL."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [pytest.mark.integration, pytest.mark.db]

_READ = text(
    "SELECT ttl_days, updated_by FROM public.memory_retention_policies WHERE kind = 'fact'"
)


def _migrated(postgres_container):
    from alembic import command
    from butlers.migrations import _build_alembic_config

    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
        revisions={"core": "core_356"},
    )
    return url, command, _build_alembic_config(url, chains=["core"])


def test_the_seeded_seven_days_becomes_keep_forever_and_downgrade_restores_it(
    postgres_container,
) -> None:
    url, command, config = _migrated(postgres_container)
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            assert c.execute(_READ).one()[0] == 7
        command.upgrade(config, "core_358")
        with engine.connect() as c:
            ttl, by = c.execute(_READ).one()
        assert ttl is None and by == "migration:core_358"
        command.downgrade(config, "core_356")
        with engine.connect() as c:
            assert c.execute(_READ).one()[0] == 7
    finally:
        engine.dispose()


def test_an_edited_horizon_is_left_alone_both_ways(postgres_container) -> None:
    url, command, config = _migrated(postgres_container)
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(
                text(
                    "UPDATE public.memory_retention_policies SET ttl_days = 90, "
                    "updated_by = 'admin' WHERE kind = 'fact'"
                )
            )
        command.upgrade(config, "core_358")
        with engine.connect() as c:
            assert c.execute(_READ).one()[0] == 90
        command.downgrade(config, "core_356")
        with engine.connect() as c:
            assert c.execute(_READ).one()[0] == 90
    finally:
        engine.dispose()


def test_a_horizon_an_admin_set_after_the_upgrade_survives_the_downgrade(
    postgres_container,
) -> None:
    url, command, config = _migrated(postgres_container)
    engine = create_engine(url)
    try:
        command.upgrade(config, "core_358")
        with engine.begin() as c:
            c.execute(
                text(
                    "UPDATE public.memory_retention_policies SET ttl_days = NULL, "
                    "updated_by = 'admin' WHERE kind = 'fact'"
                )
            )
        command.downgrade(config, "core_356")
        with engine.connect() as c:
            assert c.execute(_READ).one()[0] is None
    finally:
        engine.dispose()

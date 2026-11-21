"""core_354 admits the 'animal' entity type on migrated PostgreSQL (bu-s11n0s.13 S1)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [pytest.mark.integration, pytest.mark.db]

_INSERT = text(
    "INSERT INTO public.entities (canonical_name, entity_type) VALUES (:name, :entity_type)"
)


def test_animal_is_admitted_and_unknown_types_stay_rejected(postgres_container) -> None:
    from alembic import command
    from butlers.migrations import _build_alembic_config

    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
        revisions={"core": "core_353"},
    )
    config = _build_alembic_config(url, chains=["core"])
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            with pytest.raises(IntegrityError):
                c.execute(_INSERT, {"name": "Rex", "entity_type": "animal"})
        command.upgrade(config, "core_354")
        with engine.begin() as c:
            c.execute(_INSERT, {"name": "Rex", "entity_type": "animal"})
        with engine.connect() as c:
            with pytest.raises(IntegrityError):
                c.execute(_INSERT, {"name": "Ghost", "entity_type": "ghost"})
        # Cumulative downgrade: an existing animal row must not block it.
        command.downgrade(config, "core_353")
        with engine.connect() as c:
            kept = c.execute(
                text("SELECT entity_type FROM public.entities WHERE canonical_name = 'Rex'")
            ).scalar()
            assert kept == "animal"
    finally:
        engine.dispose()

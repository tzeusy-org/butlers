"""core_354 owner-only outcome shaping setting on migrated PostgreSQL (bu-s11n0s.15 S3-S5)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [pytest.mark.integration, pytest.mark.db]

_COLUMNS = text(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = 'insight_settings' "
    "AND column_name IN ('outcome_shaping', 'shaping_state') ORDER BY column_name"
)


def test_the_setting_defaults_to_observe_rejects_other_values_and_round_trips(
    postgres_container,
) -> None:
    from alembic import command
    from butlers.migrations import _build_alembic_config

    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
        revisions={"core": "core_354"},
    )
    config = _build_alembic_config(url, chains=["core"])
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            assert [r[0] for r in c.execute(_COLUMNS)] == ["outcome_shaping", "shaping_state"]
            c.execute(text("INSERT INTO public.insight_settings (id) VALUES (1)"))
            row = c.execute(
                text("SELECT outcome_shaping, shaping_state FROM public.insight_settings")
            ).one()
            assert (row.outcome_shaping, row.shaping_state) == ("observe", {})
            c.execute(text("UPDATE public.insight_settings SET outcome_shaping = 'shape'"))
        for bad in (
            "UPDATE public.insight_settings SET outcome_shaping = 'on'",
            "UPDATE public.insight_settings SET shaping_state = '[]'::jsonb",
        ):
            with pytest.raises(DBAPIError), engine.begin() as c:
                c.execute(text(bad))

        command.downgrade(config, "core_353")
        with engine.connect() as c:
            assert list(c.execute(_COLUMNS)) == []
        command.upgrade(config, "core_354")
        with engine.connect() as c:
            assert len(list(c.execute(_COLUMNS))) == 2
    finally:
        engine.dispose()

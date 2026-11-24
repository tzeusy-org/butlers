"""core_344: ``insight_settings.verbosity`` admits the ratchet's own ``auto_off`` (bu-s11n0s.8 S2).

Real migrated databases only: the check accepts ``auto_off`` and still refuses unknown values,
an existing ``off`` row stays an explicit opt-out, a re-run is a no-op, and the downgrade maps
``auto_off`` back to ``off`` before restoring the narrower check.
"""

from __future__ import annotations

import shutil

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_SCHEMA = "general"


def _execute(db_url: str, statement: str) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(statement))
    finally:
        engine.dispose()


def _verbosity(db_url: str) -> str:
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            return connection.execute(
                text("SELECT verbosity FROM public.insight_settings")
            ).scalar_one()
    finally:
        engine.dispose()


def test_auto_off_is_admitted_unknown_is_not_and_the_downgrade_maps_it_back(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema=_SCHEMA)
    command.upgrade(config, "core_343")  # pinned-revision: the predecessor
    _execute(db_url, "UPDATE public.insight_settings SET verbosity = 'off'")
    with pytest.raises(IntegrityError):
        _execute(db_url, "UPDATE public.insight_settings SET verbosity = 'auto_off'")

    command.upgrade(config, "core_344")  # pinned-revision: under test
    assert _verbosity(db_url) == "off"  # an existing off stays an explicit opt-out
    _execute(db_url, "UPDATE public.insight_settings SET verbosity = 'auto_off'")
    with pytest.raises(IntegrityError):
        _execute(db_url, "UPDATE public.insight_settings SET verbosity = 'sometimes'")

    command.downgrade(config, "core_343")  # pinned-revision: restores the narrower check
    assert _verbosity(db_url) == "off"
    with pytest.raises(IntegrityError):
        _execute(db_url, "UPDATE public.insight_settings SET verbosity = 'auto_off'")

"""core_248 declares probed vision support by (runtime_type, model_id), idempotently."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [pytest.mark.integration, pytest.mark.db]

_ROWS = [
    # alias, runtime_type, model_id, capabilities
    ("vision-sol-high", "codex", "gpt-6-sol", {}),
    ("vision-sol-healing", "codex", "gpt-6-sol", {"tool_use": True}),
    ("vision-qwen-max", "opencode", "opencode-go/qwen3.7-max", {}),
    ("vision-unprobed", "opencode", "opencode-go/kimi-k2.5", {}),
    ("vision-wrong-runtime", "opencode", "gpt-6-sol", {}),
]


def _capabilities(connection) -> dict[str, dict]:
    rows = connection.exec_driver_sql(
        "SELECT alias, capabilities FROM public.model_catalog WHERE alias LIKE 'vision-%%'"
    ).all()
    return {alias: caps for alias, caps in rows}


def test_vision_evidence_merges_by_runtime_and_model(postgres_container):
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, "core@core_247")
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            for alias, runtime_type, model_id, caps in _ROWS:
                connection.exec_driver_sql(
                    "INSERT INTO public.model_catalog "
                    "(alias, runtime_type, model_id, complexity_tier, capabilities) "
                    "VALUES (%s, %s, %s, 'workhorse', %s::jsonb)",
                    (alias, runtime_type, model_id, json.dumps(caps)),
                )

        command.upgrade(config, "core@core_248")
        with engine.connect() as connection:
            first = _capabilities(connection)

        assert first == {
            "vision-sol-high": {"vision": True},
            "vision-sol-healing": {"tool_use": True, "vision": True},
            "vision-qwen-max": {"vision": False},
            "vision-unprobed": {},
            "vision-wrong-runtime": {},
        }

        # The core chain runs once per butler schema against the shared table.
        command.downgrade(config, "core@core_247")
        command.upgrade(config, "core@core_248")
        with engine.connect() as connection:
            assert _capabilities(connection) == first
    finally:
        engine.dispose()

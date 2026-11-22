"""core_356 widens the JSON credential rule and recomputes leaves written under core_320's.

bu-y66o00.71. The materialization triggers only recompute a row when it is written, so a key that
core_320's tokenizer let through (``apikey``, ``APIKey``, ``cvv2``, ``pin2``) stays published
until the migration recomputes it. Each schema's own core run recomputes its own tables.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [pytest.mark.integration, pytest.mark.db]

_LEAKY = {"apikey": "synthetic-omit", "APIKey": "synthetic-omit", "cvv2": "123"}
_KEPT = {"notes": "synthetic-keep", "secretary": "synthetic-keep"}
_READING = {"systolic": 120, "pin2": 1234}


def _paths(engine, schema: str, source_table: str) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text(
                    f"SELECT path FROM {schema}.json_fields_public "
                    "WHERE source_table=:source AND starts_with(path, 'metadata.')"
                ),
                {"source": f"{schema}.{source_table}"},
            ).scalars()
        )


def _metrics(engine) -> set[str]:
    with engine.connect() as conn:
        return set(conn.execute(text("SELECT metric FROM health.measurements_public")).scalars())


def test_core_356_recomputes_leaves_published_under_the_old_rule(postgres_container) -> None:
    url = create_migration_db(postgres_container, migration_db_name())
    for domain in ("finance", "health"):
        command.upgrade(
            _build_alembic_config(url, chains=["core"], target_schema=domain), "core_355"
        )
        for chain in (domain, "memory"):
            asyncio.run(run_migrations(url, chain=chain, schema=domain))
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO finance.transactions(posted_at,merchant,amount,currency,direction,
                        category,metadata)
                    VALUES(now(),'C fixture',1,'USD','debit','groceries',CAST(:m AS jsonb))
                """),
                {"m": json.dumps({**_LEAKY, **_KEPT})},
            )
            for predicate, metadata in (
                ("condition", {**_LEAKY, **_KEPT}),
                ("measurement_blood_pressure", {"value": _READING}),
            ):
                conn.execute(
                    text("""
                        INSERT INTO health.facts(subject,predicate,content,metadata,sensitivity,
                            scope)
                        VALUES('someone else',:p,'Unchanged content',CAST(:m AS jsonb),
                            'confidential','arbitrary')
                    """),
                    {"p": predicate, "m": json.dumps(metadata)},
                )
        leaked = {"metadata." + key for key in (*_LEAKY, *_KEPT)}
        kept = {"metadata." + key for key in _KEPT}
        # core_320's rule published every one of these.
        assert _paths(engine, "finance", "transactions") == leaked
        # The reading's own metadata leaves sit beside the condition fact's.
        readings = {"metadata.value." + key for key in _READING}
        assert _paths(engine, "health", "facts") == leaked | readings
        assert _metrics(engine) == {
            "measurement_blood_pressure.systolic",
            "measurement_blood_pressure.pin2",
        }

        # The shared functions change on the first run; only health's own run recomputes health.
        asyncio.run(run_migrations(url, chain="core", schema="health"))
        assert _paths(engine, "health", "facts") == kept | {"metadata.value.systolic"}
        assert _metrics(engine) == {"measurement_blood_pressure.systolic"}
        assert _paths(engine, "finance", "transactions") == leaked

        asyncio.run(run_migrations(url, chain="core", schema="finance"))
        assert _paths(engine, "finance", "transactions") == kept

        # A later write is tokenized by the new functions without any migration.
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE finance.transactions SET metadata=CAST(:m AS jsonb)"),
                {"m": json.dumps({"accessToken": "x", "pin2": "1", "notes": "synthetic-keep"})},
            )
        assert _paths(engine, "finance", "transactions") == {"metadata.notes"}
    finally:
        engine.dispose()

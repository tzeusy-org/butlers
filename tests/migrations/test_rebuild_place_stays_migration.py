"""chronicler_029 rebuilds OwnTracks place stays for sparse-report bridging.

Seeds a database migrated to chronicler_028, applies the real upgrade, and
checks that live ``place_episode`` rows are tombstoned, ``away_episode`` rows
are untouched, and the place-cluster checkpoint is cleared for a replay.

Requirement: REQ-butler-chronicler-014 (Place stays are evidence; stays are
rebuilt with bridging).
Issue: bu-r3fmja.1
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text

from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_SOURCE = "owntracks.place_cluster"


@pytest.fixture
def chronicler_028_db(postgres_container):
    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_028"},
    )
    from tests.chronicler._private_memory_setup import prepare_chronicler_private_memory_sync

    prepare_chronicler_private_memory_sync(db_url)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        yield db_url, engine
    finally:
        engine.dispose()


def _episode(conn, ref: str, episode_type: str) -> None:
    conn.execute(
        text(
            "INSERT INTO chronicler.episodes "
            "(source_name, source_ref, episode_type, start_at, end_at, layer) "
            "VALUES (:s, :r, :t, :a, :b, 'activity')"
        ),
        {
            "s": _SOURCE,
            "r": ref,
            "t": episode_type,
            "a": datetime(2026, 11, 2, 2, 54, tzinfo=UTC),
            "b": datetime(2026, 11, 2, 3, 30, tzinfo=UTC),
        },
    )


def test_place_stays_are_tombstoned_and_the_checkpoint_replays(chronicler_028_db) -> None:
    db_url, engine = chronicler_028_db
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO chronicler.source_adapter_state "
                "(source_name, chronicler_compatibility) VALUES (:s, 'supported') "
                "ON CONFLICT DO NOTHING"
            ),
            {"s": _SOURCE},
        )
        _episode(conn, "place:1", "place_episode")
        _episode(conn, "away:1", "away_episode")
        conn.execute(
            text(
                "INSERT INTO chronicler.projection_checkpoints "
                "(source_name, subsource, watermark, carryover) "
                "VALUES (:s, '', :w, CAST(:c AS jsonb))"
            ),
            {
                "s": _SOURCE,
                "w": datetime(2026, 11, 2, 20, 46, tzinfo=UTC),
                "c": json.dumps({"owntracks:owner": {"point_count": 1}}),
            },
        )

    from butlers.migrations import run_migrations

    asyncio.run(run_migrations(db_url, chain="chronicler", schema="chronicler"))

    with engine.connect() as conn:
        rows = dict(
            conn.execute(
                text(
                    "SELECT episode_type, tombstone_reason FROM chronicler.episodes "
                    "WHERE source_name = :s"
                ),
                {"s": _SOURCE},
            ).all()
        )
        checkpoint = conn.execute(
            text(
                "SELECT watermark, carryover FROM chronicler.projection_checkpoints "
                "WHERE source_name = :s"
            ),
            {"s": _SOURCE},
        ).one()

    assert "bu-r3fmja" in rows["place_episode"]
    assert rows["away_episode"] is None
    assert tuple(checkpoint) == (None, None)

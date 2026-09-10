"""Real PostgreSQL regression coverage for core_230 (spotify_track_plays)."""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from alembic import command
from butlers.connectors.spotify import (
    TrackObservation,
    TrackPlayEvidence,
    TrackPlayTracker,
    close_track_play,
    gap_fill_play_already_observed,
    load_open_track_plays,
    record_gap_fill_track_play,
    upsert_open_track_play,
)
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import (
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = pytest.mark.integration


def _migrate_core(postgres_container, db_name: str) -> str:
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))
    return db_url


def test_spotify_track_plays_table_and_indexes_exist(postgres_container) -> None:
    db_url = _migrate_core(postgres_container, migration_db_name())
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT to_regclass('connectors.spotify_track_plays')")
                ).scalar_one()
                is not None
            )
            assert (
                connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = 'connectors' "
                        "AND tablename = 'spotify_track_plays' "
                        "AND indexname = 'ux_spotify_track_plays_identity'"
                    )
                ).scalar_one_or_none()
                is not None
            )
    finally:
        engine.dispose()


def test_lifestyle_role_can_select_but_not_insert_on_track_plays(postgres_container) -> None:
    """Check (8): butler_lifestyle_rw can SELECT and cannot INSERT on the plays table."""
    db_name = migration_db_name()
    db_url = _migrate_core(postgres_container, db_name)
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            privileges = (
                connection.execute(
                    text(
                        "SELECT "
                        "has_table_privilege("
                        "'butler_lifestyle_rw', 'connectors.spotify_track_plays', 'SELECT'"
                        ") AS can_select, "
                        "has_table_privilege("
                        "'butler_lifestyle_rw', 'connectors.spotify_track_plays', 'INSERT'"
                        ") AS can_insert"
                    )
                )
                .mappings()
                .one()
            )
            assert privileges == {"can_select": True, "can_insert": False}
    finally:
        engine.dispose()

    admin_engine = create_engine(
        migration_bootstrap_db_url(postgres_container, db_name), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(text('SET ROLE "butler_lifestyle_rw"'))
            try:
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM connectors.spotify_track_plays")
                    ).scalar_one()
                    == 0
                )
                with pytest.raises(ProgrammingError, match="permission denied"):
                    connection.execute(
                        text(
                            "INSERT INTO connectors.spotify_track_plays "
                            "(endpoint_identity, spotify_user_id, track_uri, first_seen_ms, last_seen_ms) "
                            "VALUES ('e', 'u', 'spotify:track:x', 1, 1)"
                        )
                    )
            finally:
                connection.execute(text("RESET ROLE"))
    finally:
        admin_engine.dispose()


def test_lifestyle_role_gains_select_on_pre_existing_listening_sessions(postgres_container) -> None:
    """core_230 also grants butler_lifestyle_rw SELECT on core_079's sessions table."""
    db_url = _migrate_core(postgres_container, migration_db_name())
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT has_table_privilege("
                        "'butler_lifestyle_rw', 'connectors.spotify_listening_sessions', 'SELECT'"
                        ")"
                    )
                ).scalar_one()
                is True
            )
    finally:
        engine.dispose()


def test_connector_writer_has_full_dml_on_track_plays(postgres_container) -> None:
    db_url = _migrate_core(postgres_container, migration_db_name())
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            privileges = (
                connection.execute(
                    text(
                        "SELECT "
                        "has_table_privilege("
                        "'connector_writer', 'connectors.spotify_track_plays', 'SELECT'"
                        ") AS can_select, "
                        "has_table_privilege("
                        "'connector_writer', 'connectors.spotify_track_plays', 'INSERT'"
                        ") AS can_insert, "
                        "has_table_privilege("
                        "'connector_writer', 'connectors.spotify_track_plays', 'UPDATE'"
                        ") AS can_update, "
                        "has_table_privilege("
                        "'connector_writer', 'connectors.spotify_track_plays', 'DELETE'"
                        ") AS can_delete"
                    )
                )
                .mappings()
                .one()
            )
            assert privileges == {
                "can_select": True,
                "can_insert": True,
                "can_update": True,
                "can_delete": True,
            }
    finally:
        engine.dispose()


def test_play_only_upserts_and_close_preserve_null_progress(postgres_container) -> None:
    """Repeated missing-progress observations must not fabricate a zero measurement."""
    db_url = _migrate_core(postgres_container, migration_db_name())

    async def _exercise() -> tuple[int | None, float | None, bool | None, str]:
        pool = await asyncpg.create_pool(db_url)
        evidence = TrackPlayEvidence(
            track_uri="spotify:track:play-only",
            track_name="Unknown progress",
            first_seen_ms=1_000,
            last_seen_ms=2_000,
            duration_ms=180_000,
            max_progress_ms=None,
            observation_precision="play_only",
            closed=False,
        )
        try:
            await upsert_open_track_play(
                pool,
                endpoint_identity="spotify:user1",
                spotify_user_id="user1",
                evidence=evidence,
            )
            await upsert_open_track_play(
                pool,
                endpoint_identity="spotify:user1",
                spotify_user_id="user1",
                evidence=evidence,
            )
            await close_track_play(
                pool,
                endpoint_identity="spotify:user1",
                spotify_user_id="user1",
                evidence=TrackPlayEvidence(**{**evidence.__dict__, "closed": True}),
            )
            row = await pool.fetchrow(
                "SELECT max_progress_ms, completion_ratio, skipped, observation_precision "
                "FROM connectors.spotify_track_plays"
            )
            assert row is not None
            return (
                row["max_progress_ms"],
                row["completion_ratio"],
                row["skipped"],
                row["observation_precision"],
            )
        finally:
            await pool.close()

    assert asyncio.run(_exercise()) == (None, None, None, "play_only")


def test_track_play_persistence_is_idempotent_monotonic_and_lossless(postgres_container) -> None:
    """Exercise open replay, close-without-open, and final persisted progress in PostgreSQL."""
    db_url = _migrate_core(postgres_container, migration_db_name())

    async def _exercise() -> None:
        pool = await asyncpg.create_pool(db_url)
        try:
            low = TrackPlayEvidence(
                track_uri="spotify:track:monotonic",
                track_name="Monotonic",
                first_seen_ms=1_000,
                last_seen_ms=2_000,
                duration_ms=200_000,
                max_progress_ms=5_000,
                observation_precision="progress_tracked",
                closed=False,
            )
            high = TrackPlayEvidence(
                **{
                    **low.__dict__,
                    "last_seen_ms": 5_000,
                    "max_progress_ms": 190_000,
                }
            )
            await asyncio.gather(
                upsert_open_track_play(
                    pool,
                    endpoint_identity="spotify:user1",
                    spotify_user_id="user1",
                    evidence=low,
                ),
                upsert_open_track_play(
                    pool,
                    endpoint_identity="spotify:user1",
                    spotify_user_id="user1",
                    evidence=high,
                ),
            )
            await close_track_play(
                pool,
                endpoint_identity="spotify:user1",
                spotify_user_id="user1",
                evidence=TrackPlayEvidence(**{**low.__dict__, "closed": True}),
            )
            row = await pool.fetchrow(
                "SELECT count(*) OVER () AS row_count, max_progress_ms, completion_ratio, skipped "
                "FROM connectors.spotify_track_plays"
            )
            assert row is not None
            assert row["row_count"] == 1
            assert row["max_progress_ms"] == 190_000
            assert row["completion_ratio"] == pytest.approx(0.95)
            assert row["skipped"] is False

            missing = TrackPlayEvidence(
                track_uri="spotify:track:missing-open",
                track_name="Missing open",
                first_seen_ms=10_000,
                last_seen_ms=15_000,
                duration_ms=250_000,
                max_progress_ms=5_000,
                observation_precision="progress_tracked",
                closed=True,
            )
            await close_track_play(
                pool,
                endpoint_identity="spotify:user2",
                spotify_user_id="user2",
                evidence=missing,
            )
            closed = await pool.fetchrow(
                "SELECT completion_ratio, skipped, closed_at FROM connectors.spotify_track_plays "
                "WHERE endpoint_identity = 'spotify:user2'"
            )
            assert closed is not None
            assert closed["completion_ratio"] == pytest.approx(0.02)
            assert closed["skipped"] is True
            assert closed["closed_at"] is not None

            retry_evidence = TrackPlayEvidence(
                **{
                    **missing.__dict__,
                    "track_uri": "spotify:track:retry",
                    "first_seen_ms": 20_000,
                    "last_seen_ms": 25_000,
                }
            )

            class _FailOncePool:
                failed = False

                async def fetchrow(self, query, *args):
                    if not self.failed:
                        self.failed = True
                        raise ConnectionError("transient close failure")
                    return await pool.fetchrow(query, *args)

            retry_pool = _FailOncePool()
            with pytest.raises(ConnectionError, match="transient close failure"):
                await close_track_play(
                    retry_pool,
                    endpoint_identity="spotify:user3",
                    spotify_user_id="user3",
                    evidence=retry_evidence,
                )
            await close_track_play(
                retry_pool,
                endpoint_identity="spotify:user3",
                spotify_user_id="user3",
                evidence=retry_evidence,
            )
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM connectors.spotify_track_plays "
                    "WHERE endpoint_identity = 'spotify:user3'"
                )
                == 1
            )
        finally:
            await pool.close()

    asyncio.run(_exercise())


def test_restart_recovery_and_recently_played_reconciliation(postgres_container) -> None:
    """A fresh tracker closes persisted A and a later recent item does not duplicate it."""
    db_url = _migrate_core(postgres_container, migration_db_name())

    async def _exercise() -> None:
        pool = await asyncpg.create_pool(db_url)
        try:
            persisted = TrackPlayEvidence(
                track_uri="spotify:track:a",
                track_name="A",
                first_seen_ms=100_000,
                last_seen_ms=105_000,
                duration_ms=250_000,
                max_progress_ms=5_000,
                observation_precision="progress_tracked",
                closed=False,
            )
            await upsert_open_track_play(
                pool,
                endpoint_identity="spotify:user1",
                spotify_user_id="user1",
                evidence=persisted,
            )

            fresh_tracker = TrackPlayTracker()
            recovered = await load_open_track_plays(pool, endpoint_identity="spotify:user1")
            assert len(recovered) == 1
            fresh_tracker.restore(recovered[0])
            closed = fresh_tracker.observe(
                TrackObservation(
                    track_uri="spotify:track:b",
                    track_name="B",
                    duration_ms=200_000,
                    progress_ms=0,
                    timestamp_ms=130_000,
                )
            )
            assert closed is not None
            await close_track_play(
                pool,
                endpoint_identity="spotify:user1",
                spotify_user_id="user1",
                evidence=closed,
            )
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM connectors.spotify_track_plays "
                    "WHERE endpoint_identity = 'spotify:user1' AND closed_at IS NOT NULL"
                )
                == 1
            )

            assert await gap_fill_play_already_observed(
                pool,
                endpoint_identity="spotify:user1",
                track_uri="spotify:track:a",
                played_at_ms=130_000,
            )
            await record_gap_fill_track_play(
                pool,
                endpoint_identity="spotify:user1",
                spotify_user_id="user1",
                track_uri="spotify:track:a",
                track_name="A",
                duration_ms=250_000,
                played_at_ms=130_000,
            )
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM connectors.spotify_track_plays "
                    "WHERE endpoint_identity = 'spotify:user1' AND track_uri = 'spotify:track:a'"
                )
                == 1
            )
        finally:
            await pool.close()

    asyncio.run(_exercise())


def test_downgrade_drops_table_and_revokes_sessions_grant(postgres_container) -> None:
    db_name = migration_db_name()
    db_url = _migrate_core(postgres_container, db_name)
    bootstrap_core = _build_alembic_config(
        migration_bootstrap_db_url(postgres_container, db_name), chains=["core"]
    )
    command.downgrade(bootstrap_core, "core_229")
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT to_regclass('connectors.spotify_track_plays')")
                ).scalar_one()
                is None
            )
            assert (
                connection.execute(
                    text(
                        "SELECT has_table_privilege("
                        "'butler_lifestyle_rw', 'connectors.spotify_listening_sessions', 'SELECT'"
                        ")"
                    )
                ).scalar_one()
                is False
            )
    finally:
        engine.dispose()

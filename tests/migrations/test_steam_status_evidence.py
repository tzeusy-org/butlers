"""Real SQL proof for Steam status ACL/replay and Chronicler evidence history.

REQ-connector-steam-001; REQ-butler-chronicler-020. Requires native PostgreSQL.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from urllib.parse import urlparse

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from alembic import command
from butlers.db import register_jsonb_codec
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = pytest.mark.integration


def test_status_history_idempotency_acl_and_bootstrap_replay(postgres_container):
    name = migration_db_name()
    url = create_migrated_test_db(
        postgres_container, name, chains=["core"], revisions={"core": "core_313"}
    )
    control = migration_bootstrap_db_url(postgres_container, name)
    engine = create_engine(url)
    insert = """INSERT INTO connectors.steam_status_history
        (steam_id, observed_at, persona_state, game_name)
        VALUES (42, '2026-11-01 13:15Z', 1, 'Portal')
        ON CONFLICT (steam_id, observed_at) DO NOTHING"""

    def probe():
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE connector_writer"))
            conn.execute(text(insert))
            conn.execute(text(insert))
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE butler_chronicler_rw"))
            assert conn.execute(
                text("SELECT game_name FROM connectors.steam_status_history")
            ).all() == [("Portal",)]
            assert not conn.execute(
                text(
                    "SELECT has_table_privilege(current_user, 'connectors.steam_status_history', 'INSERT,UPDATE,DELETE,TRUNCATE')"
                )
            ).scalar()
        with pytest.raises(ProgrammingError, match="permission denied"):
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL ROLE butler_chronicler_rw"))
                conn.execute(text("DELETE FROM connectors.steam_status_history"))

    try:
        probe()
        # Replay grants after table creation, then repeat both the allowed and denied action.
        migration_user = urlparse(url).username
        assert migration_user is not None
        _bootstrap_migration_prerequisites(control, migration_user)
        probe()
        config = _build_alembic_config(url, chains=["core"])
        command.downgrade(config, "core_312")
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT count(*) FROM connectors.steam_status_history")).scalar()
                == 1
            )
        command.upgrade(config, "core@core_313")
        probe()
    finally:
        engine.dispose()


def test_daily_and_spotify_history_relayers_preserve_rows_and_other_sources(postgres_container):
    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for source, kind in [
                ("steam.play_history", "play_episode"),
                ("spotify.session_summary", "listening_episode"),
                ("google_health.measurements", "sleep_episode"),
            ]:
                conn.execute(
                    text(
                        "INSERT INTO chronicler.source_adapter_state "
                        "(source_name, chronicler_compatibility) VALUES (:s, 'supported')"
                    ),
                    {"s": source},
                )
                conn.execute(
                    text("""INSERT INTO chronicler.episodes
                    (source_name, source_ref, episode_type, start_at, end_at, layer, title)
                    VALUES (:s, :s, :k, '2026-11-01 13:15Z', '2026-11-01 14:15Z',
                            'activity', 'Keep provenance')"""),
                    {"s": source, "k": kind},
                )
            conn.execute(
                text(
                    "INSERT INTO chronicler.source_adapter_state "
                    "(source_name, chronicler_compatibility) "
                    "VALUES ('chronicler.situation_inferred', 'supported')"
                )
            )
            conn.execute(
                text("""INSERT INTO chronicler.projection_checkpoints
                (source_name, subsource, watermark)
                VALUES ('chronicler.situation_inferred', '', '2026-11-01 12:00Z')""")
            )
            before = conn.execute(
                text("SELECT id, source_ref, title FROM chronicler.episodes ORDER BY source_name")
            ).all()
        config = _build_alembic_config(url, chains=["chronicler"], target_schema="chronicler")
        command.upgrade(config, "chronicler@chronicler_030")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT id, source_ref, title FROM chronicler.episodes ORDER BY source_name"
                    )
                ).all()
                == before
            )
            assert dict(
                conn.execute(text("SELECT source_name, layer FROM chronicler.episodes")).all()
            ) == {
                "steam.play_history": "evidence",
                "spotify.session_summary": "evidence",
                "google_health.measurements": "activity",
            }
            assert (
                conn.execute(
                    text(
                        "SELECT watermark FROM chronicler.projection_checkpoints "
                        "WHERE source_name = 'chronicler.situation_inferred'"
                    )
                ).scalar()
                is None
            )
        # A bounded rollback keeps history and does not reintroduce double-counted activity.
        config = _build_alembic_config(url, chains=["chronicler"], target_schema="chronicler")
        command.downgrade(config, "chronicler_029")
        command.upgrade(config, "chronicler@chronicler_030")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT id, source_ref, title FROM chronicler.episodes ORDER BY source_name"
                    )
                ).all()
                == before
            )

        # Exercise the real view/query rather than a fake that ignores its layer filter.
        from butlers.chronicler.adapters.situation import SituationInferredAdapter

        async def steam_sources():
            conn = await asyncpg.connect(url.replace("postgresql+psycopg2://", "postgresql://"))
            try:
                await conn.execute("SET search_path TO chronicler, public")
                spans = await SituationInferredAdapter()._activities(
                    conn, datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 2, tzinfo=UTC)
                )
                return {span.source_name for span in spans if span.lane == "play"}
            finally:
                await conn.close()

        assert asyncio.run(steam_sources()) == {"steam.play_history"}
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO chronicler.source_adapter_state "
                    "(source_name, chronicler_compatibility) "
                    "VALUES ('steam.status_history', 'supported')"
                )
            )
            conn.execute(
                text("""INSERT INTO chronicler.episodes
                (source_name, source_ref, episode_type, start_at, end_at, layer)
                VALUES ('steam.status_history', 'observed:1', 'play_session',
                        '2026-11-01 13:30Z', '2026-11-01 13:45Z', 'evidence')""")
            )
        assert asyncio.run(steam_sources()) == {"steam.status_history"}
    finally:
        engine.dispose()


@pytest.mark.parametrize("change", ["tombstone", "move"])
def test_changed_old_steam_evidence_removes_stale_counted_play(postgres_container, change):
    """Corrections outside the lookback invalidate previously projected provenance dates."""
    from datetime import timedelta
    from unittest.mock import AsyncMock, patch

    from butlers.chronicler.adapters import situation as adapter_mod

    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_030"},
    )

    async def scenario():
        pool = await asyncpg.create_pool(
            url.replace("postgresql+psycopg2://", "postgresql://"),
            server_settings={"search_path": "chronicler, public"},
            init=register_jsonb_codec,
        )
        # This scenario deliberately derives UTC-local days; declare that actual
        # rollup zone rather than silently mixing it with the default SGT cache.
        await pool.execute("UPDATE daily_repair_control SET timezone='UTC'")
        now = datetime(2026, 11, 3, 12, tzinfo=UTC)
        old = now - timedelta(days=10)
        intermediate = old - timedelta(days=2)
        try:
            await pool.execute("""INSERT INTO source_adapter_state
                (source_name, chronicler_compatibility) VALUES
                ('steam.status_history', 'supported'),
                ('chronicler.situation_inferred', 'supported')""")
            evidence = await pool.fetchval(
                """INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, end_at, layer, updated_at)
                VALUES ('steam.status_history', 'status:old', 'play_session', $1, $2,
                        'evidence', $3) RETURNING id""",
                old,
                old + timedelta(hours=1),
                old,
            )
            # An intermediate previous correction is older even than the canonical date.
            # Only the persisted provenance can recover this affected day.
            stale = await pool.fetchval(
                """INSERT INTO episodes
                (source_name, source_ref, episode_type, start_at, end_at, layer,
                 evidence_refs, payload)
                VALUES ('chronicler.situation_inferred', 'old-play', 'play_block',
                        $1, $2, 'activity', jsonb_build_array($3::text),
                        jsonb_build_object('local_date', $4::text)) RETURNING id""",
                intermediate,
                intermediate + timedelta(hours=1),
                str(evidence),
                intermediate.date().isoformat(),
            )
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM episodes WHERE id=$1 AND tombstone_at IS NULL", stale
                )
                == 1
            )
            if change == "tombstone":
                await pool.execute(
                    """INSERT INTO overrides
                    (target_kind, target_id, corrected_tombstone_at, created_at)
                    VALUES ('episode', $1, $2, $2)""",
                    evidence,
                    now,
                )
            else:
                await pool.execute(
                    """INSERT INTO overrides
                    (target_kind, target_id, corrected_start_at, corrected_end_at, created_at)
                    VALUES ('episode', $1, $2, $3, $4)""",
                    evidence,
                    now - timedelta(hours=2),
                    now - timedelta(hours=1),
                    now,
                )
            with (
                patch.object(adapter_mod, "_now", return_value=now),
                patch.object(adapter_mod, "load_anchors", AsyncMock(return_value=())),
                patch.object(adapter_mod, "resolve_owner_entity_id", AsyncMock(return_value=None)),
                patch.object(
                    adapter_mod.SituationInferredAdapter, "_tail_hold", AsyncMock(return_value=None)
                ),
            ):
                await adapter_mod.SituationInferredAdapter(timezone="UTC").project(
                    pool, chronicler_pool=pool, since=now - timedelta(minutes=5)
                )
            assert await pool.fetchval(
                "SELECT tombstone_at IS NOT NULL FROM episodes WHERE id=$1", stale
            )
            assert await pool.fetchval("""SELECT count(*) FROM episodes
                WHERE source_name='chronicler.situation_inferred'
                  AND episode_type='play_block' AND tombstone_at IS NULL""") == (
                0 if change == "tombstone" else 1
            )
        finally:
            await pool.close()

    asyncio.run(scenario())

"""chronicler_027 merges OwnTracks movement episodes that already overlap.

PR #5113 (bu-2tsgqb.6) stops new overlapping movement chunks; this migration
repairs the ones stored before it, with the same overlap rule. The tests seed a
database migrated to chronicler_026, apply the real Alembic upgrade, then rerun
the repair to prove it is idempotent.

Requirement: REQ-butler-chronicler-012 (Stored overlapping movement episodes
are repaired).
Issue: bu-2tsgqb.11
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from butlers.testing.migration import create_migrated_test_db, load_migration, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_MIGRATION = load_migration(
    Path(__file__).resolve().parents[2]
    / "roster"
    / "chronicler"
    / "migrations"
    / "027_merge_overlapping_movement_episodes.py"
)
_SOURCE = "owntracks.points"
_PHONE = "owntracks:owner/phone"
_WATCH = "owntracks:owner/watch"
_OWNER = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _at(hhmm: str, day: int = 16) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def _ref(endpoint: str, start: datetime) -> str:
    return f"connectors.owntracks_points:movement:{endpoint}:{int(start.timestamp())}"


class _Seeder:
    def __init__(self, conn) -> None:
        self.conn = conn
        conn.execute(
            text(
                "INSERT INTO chronicler.source_adapter_state "
                "(source_name, chronicler_compatibility) VALUES (:s, 'supported') "
                "ON CONFLICT DO NOTHING"
            ),
            {"s": _SOURCE},
        )

    def fix(self, ts: datetime) -> uuid.UUID:
        return self.conn.execute(
            text(
                "INSERT INTO chronicler.point_events "
                "(source_name, source_ref, event_type, occurred_at) "
                "VALUES (:s, :r, 'location', :t) RETURNING id"
            ),
            {"s": _SOURCE, "r": f"fix:{uuid.uuid4()}", "t": ts},
        ).scalar_one()

    def episode(
        self,
        endpoint: str,
        start: str,
        end: str,
        *,
        fixes: Sequence[uuid.UUID] = (),
        point_count: int | None = None,
        end_lat: float = 1.0,
    ) -> uuid.UUID:
        start_at, end_at = _at(start), _at(end)
        payload = {
            "endpoint_identity": endpoint,
            "point_count": len(fixes) if point_count is None else point_count,
            "start_lat": 40.0,
            "start_lon": 116.0,
            "end_lat": end_lat,
            "end_lon": 116.5,
        }
        episode_id = self.conn.execute(
            text(
                "INSERT INTO chronicler.episodes "
                "(source_name, source_ref, episode_type, start_at, end_at, title, payload, "
                " evidence_refs) "
                "VALUES (:s, :r, 'movement_episode', :a, :b, 'Movement', "
                " CAST(:p AS jsonb), CAST(:e AS jsonb)) RETURNING id"
            ),
            {
                "s": _SOURCE,
                "r": _ref(endpoint, start_at),
                "a": start_at,
                "b": end_at,
                "p": json.dumps(payload),
                "e": json.dumps([str(f) for f in fixes]),
            },
        ).scalar_one()
        for fix in fixes:
            self.conn.execute(
                text(
                    "INSERT INTO chronicler.episode_event_links (episode_id, event_id) "
                    "VALUES (:e, :f)"
                ),
                {"e": episode_id, "f": fix},
            )
        self.conn.execute(
            text(
                "INSERT INTO chronicler.episode_entities (episode_id, entity_id, role) "
                "VALUES (:e, :o, 'owner')"
            ),
            {"e": episode_id, "o": _OWNER},
        )
        return episode_id


def _episodes(conn) -> dict[str, dict]:
    rows = conn.execute(
        text(
            "SELECT id, source_ref, start_at, end_at, title, payload, evidence_refs, "
            "       tombstone_at IS NOT NULL AS tombstoned, tombstone_reason, "
            "       ARRAY(SELECT l.event_id FROM chronicler.episode_event_links l "
            "             WHERE l.episode_id = e.id) AS links, "
            "       ARRAY(SELECT ee.entity_id FROM chronicler.episode_entities ee "
            "             WHERE ee.episode_id = e.id) AS entities "
            "FROM chronicler.episodes e WHERE source_name = :s"
        ),
        {"s": _SOURCE},
    ).mappings()
    return {row["source_ref"]: dict(row) for row in rows}


def _carryover(conn) -> dict:
    return conn.execute(
        text(
            "SELECT carryover FROM chronicler.projection_checkpoints "
            "WHERE source_name = :s AND subsource = ''"
        ),
        {"s": _SOURCE},
    ).scalar_one()


def _rerun_repair(engine) -> None:
    with engine.connect() as conn:
        conn.exec_driver_sql("SET search_path TO chronicler, public")
        conn.exec_driver_sql(_MIGRATION.REPAIR_SQL)


@pytest.fixture
def chronicler_026_db(postgres_container):
    """A database migrated to chronicler_026, before the repair."""
    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_026"},
    )
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        yield db_url, engine
    finally:
        engine.dispose()


def _upgrade_to_head(db_url: str) -> None:
    from butlers.migrations import run_migrations

    asyncio.run(run_migrations(db_url, chain="chronicler", schema="chronicler"))


def test_overlapping_movement_episodes_merge_into_the_earliest(chronicler_026_db) -> None:
    db_url, engine = chronicler_026_db
    with engine.connect() as conn:
        seed = _Seeder(conn)
        a, b, c, d = (seed.fix(_at(t)) for t in ("08:14", "08:25", "09:26", "12:01"))
        # Oct 16 shape: 08:25-09:26 sits inside 08:14-12:01 and shares a fix.
        seed.episode(_PHONE, "08:14", "12:01", fixes=[a, b, d], end_lat=2.0)
        seed.episode(_PHONE, "08:25", "09:26", fixes=[b, c])
        # A chain: 13:00-14:00 and 13:50-15:00 overlap, 15:00-15:30 touches the second.
        e, f, g = (seed.fix(_at(t)) for t in ("13:00", "14:30", "15:30"))
        seed.episode(_PHONE, "13:00", "14:00", fixes=[e])
        seed.episode(_PHONE, "13:50", "15:00", fixes=[f])
        seed.episode(_PHONE, "15:00", "15:30", fixes=[g], end_lat=9.0)
        # Separate from both groups, and a same-span episode on another endpoint.
        seed.episode(_PHONE, "17:00", "17:30", fixes=[seed.fix(_at("17:00"))])
        seed.episode(_WATCH, "08:30", "09:00", fixes=[seed.fix(_at("08:30"))])
        # Legacy episodes with no linked evidence keep their stored counts.
        seed.episode(_WATCH, "19:00", "20:00", point_count=4)
        seed.episode(_WATCH, "19:30", "20:30", point_count=3, end_lat=7.0)
        # Carryover still points at a duplicate that is about to be tombstoned.
        conn.execute(
            text(
                "INSERT INTO chronicler.projection_checkpoints (source_name, subsource, carryover) "
                "VALUES (:s, '', CAST(:c AS jsonb))"
            ),
            {
                "s": _SOURCE,
                "c": json.dumps(
                    {
                        _PHONE: {
                            "source_ref": _ref(_PHONE, _at("15:00")),
                            "start_at": _at("15:00").isoformat(),
                            "end_at": _at("15:30").isoformat(),
                            "start_lat": 40.0,
                            "start_lon": 116.0,
                            "point_count": 1,
                        },
                        "_trail_anchors": {_PHONE: [1, 2, 3]},
                    }
                ),
            },
        )

    _upgrade_to_head(db_url)

    with engine.connect() as conn:
        rows = _episodes(conn)
        live = {ref: row for ref, row in rows.items() if not row["tombstoned"]}
        assert sorted(
            (r["payload"]["endpoint_identity"], r["start_at"]) for r in live.values()
        ) == [
            (_PHONE, _at("08:14")),
            (_PHONE, _at("13:00")),
            (_PHONE, _at("17:00")),
            (_WATCH, _at("08:30")),
            (_WATCH, _at("19:00")),
        ]

        oct16 = live[_ref(_PHONE, _at("08:14"))]
        assert (oct16["end_at"], oct16["payload"]["end_lat"]) == (_at("12:01"), 2.0)
        assert set(oct16["links"]) == {a, b, c, d}
        assert oct16["payload"]["point_count"] == 4 and oct16["title"] == "Movement (4 points)"
        assert oct16["evidence_refs"] == [str(x) for x in (a, b, c, d)]
        assert oct16["entities"] == [_OWNER]

        chain = live[_ref(_PHONE, _at("13:00"))]
        assert (chain["end_at"], chain["payload"]["end_lat"]) == (_at("15:30"), 9.0)
        assert chain["payload"]["point_count"] == 3

        legacy = live[_ref(_WATCH, _at("19:00"))]
        assert (legacy["end_at"], legacy["payload"]["end_lat"]) == (_at("20:30"), 7.0)
        assert legacy["payload"]["point_count"] == 7 and legacy["evidence_refs"] == []

        duplicate = rows[_ref(_PHONE, _at("08:25"))]
        assert duplicate["tombstoned"] and set(duplicate["links"]) == {b, c}
        assert _ref(_PHONE, _at("08:14")) in duplicate["tombstone_reason"]
        assert "bu-2tsgqb.11" in duplicate["tombstone_reason"]

        carry = _carryover(conn)
        assert carry["_trail_anchors"] == {_PHONE: [1, 2, 3]}
        assert carry[_PHONE]["source_ref"] == _ref(_PHONE, _at("13:00"))
        assert datetime.fromisoformat(carry[_PHONE]["start_at"]) == _at("13:00")
        assert datetime.fromisoformat(carry[_PHONE]["end_at"]) == _at("15:30")
        assert carry[_PHONE]["point_count"] == 3

        before = (rows, carry)

    _rerun_repair(engine)

    with engine.connect() as conn:
        assert (_episodes(conn), _carryover(conn)) == before


def test_group_with_a_user_correction_is_left_alone(chronicler_026_db) -> None:
    db_url, engine = chronicler_026_db
    with engine.connect() as conn:
        seed = _Seeder(conn)
        seed.episode(_PHONE, "08:14", "12:01", fixes=[seed.fix(_at("08:14"))])
        corrected = seed.episode(_PHONE, "08:25", "09:26", fixes=[seed.fix(_at("08:25"))])
        conn.execute(
            text(
                "INSERT INTO chronicler.overrides (target_kind, target_id, corrected_title) "
                "VALUES ('episode', :t, 'Walk to the hotel')"
            ),
            {"t": corrected},
        )
        before = _episodes(conn)

    _upgrade_to_head(db_url)

    with engine.connect() as conn:
        after = _episodes(conn)
        assert after == before
        assert not any(row["tombstoned"] for row in after.values())

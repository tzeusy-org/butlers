"""core_346: earlier policy-blocked OwnTracks rows lose their coordinates (bu-s11n0s.73).

Rows the connector recorded for a policy-blocked event before it stopped keeping the raw body
are given the shape it writes now: no raw body, coordinate-free text, not replayable. Parked
OwnTracks rows and other connectors' rows are untouched, and a second pass changes nothing.
"""

from __future__ import annotations

import json
import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.connectors.owntracks import (
    POLICY_FILTERED_NOT_REPLAYABLE,
    build_filtered_event_text,
)
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, load_migration, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_LOCATION = {
    "_type": "location",
    "tst": 1,
    "lat": 1.352083,
    "lon": 103.819836,
    "SSID": "home-wifi",
    "inregions": ["Home", "Gym"],
}
_TRANSITION = {
    "_type": "transition",
    "event": "enter",
    "desc": "Office",
    "lat": 1.29,
    "lon": 103.85,
}
_LEAVE = {"_type": "transition", "event": "leave", "lat": 1.29, "lon": 103.85}
_WAYPOINTS = {
    "_type": "waypoints",
    "waypoints": [{"desc": "Home", "lat": 1.35, "lon": 103.8}, {"desc": "Gym", "lat": 1.3}],
}
_INSERT = text(
    """
    INSERT INTO connectors.filtered_events (
        connector_type, endpoint_identity, external_message_id, source_channel,
        sender_identity, filter_reason, status, full_payload
    ) VALUES (:connector, 'owntracks:a1', :ext, :connector, 'owntracks:a1', :reason, :status,
              CAST(:payload AS jsonb))
    RETURNING id::text
    """
)


def _envelope(raw: dict) -> dict:
    return {
        "event": {"external_event_id": "1:location"},
        "sender": {"identity": "owntracks:a1"},
        "payload": {"raw": raw},
        "control": {},
    }


def _insert(
    conn, raw: dict, *, connector="owntracks", reason="connector_rule:block:x", status="filtered"
) -> str:
    return conn.execute(
        _INSERT,
        {
            "connector": connector,
            "ext": f"1:{raw['_type']}",
            "reason": reason,
            "status": status,
            "payload": json.dumps(_envelope(raw)),
        },
    ).scalar_one()


def _rows(engine) -> dict[str, tuple]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id::text, full_payload, xmin::text FROM connectors.filtered_events")
        ).all()
    return {row[0]: (row[1], row[2]) for row in rows}


def _sanitized(raw_text: str) -> dict:
    return {
        **_envelope({"_type": "x"}),
        "payload": {"raw": None, "normalized_text": raw_text},
        "replay_unavailable": POLICY_FILTERED_NOT_REPLAYABLE,
    }


def test_earlier_policy_rows_are_sanitized_and_others_kept(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    command.upgrade(config, "core_345")
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("SELECT connectors.connectors_filtered_events_ensure_partition(now())")
            )
            location = _insert(conn, _LOCATION)
            transition = _insert(conn, _TRANSITION, status="replay_failed")
            leave = _insert(conn, _LEAVE)
            waypoints = _insert(conn, _WAYPOINTS)
            kept = {
                _insert(conn, _LOCATION, reason="submission_error", status="error"),
                _insert(conn, _LOCATION, connector="telegram"),
            }
        before = _rows(engine)

        command.upgrade(config, "core_346")
        after = _rows(engine)
        assert after[location][0] == _sanitized(
            "Location update (blocked by policy; coordinates not kept) (in: Home, Gym)"
        )
        assert after[transition][0] == _sanitized("Entered region: Office")
        assert after[leave][0] == _sanitized("Left region: unknown region")
        assert after[waypoints][0] == _sanitized("Waypoint sync: 2 regions")
        for row in kept:
            assert after[row] == before[row]
        # The text is the connector's own for a location and a transition.
        assert after[location][0]["payload"]["normalized_text"] == build_filtered_event_text(
            "location", _LOCATION
        )
        assert after[transition][0]["payload"]["normalized_text"] == build_filtered_event_text(
            "transition", _TRANSITION
        )

        # Idempotent: another schema's pass, or a re-run, rewrites nothing.
        command.stamp(config, "core_345")
        command.upgrade(config, "core_346")
        assert _rows(engine) == after
        command.downgrade(config, "core_345")
        assert _rows(engine) == after
    finally:
        engine.dispose()


def test_the_reason_matches_the_connector() -> None:
    from pathlib import Path

    migration = load_migration(
        Path(__file__).parents[2]
        / "alembic/versions/core/core_346_owntracks_policy_filtered_sanitize.py"
    )
    assert migration._NOT_REPLAYABLE == POLICY_FILTERED_NOT_REPLAYABLE

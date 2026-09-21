# /// script
# requires-python = ">=3.12"
# dependencies = ["asyncpg>=0.31"]
# ///
# ruff: noqa: E501
"""Load a synthetic-only cached meeting-prep fixture into Route A's local DB."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

POPULATED_EVENT_ID = UUID("00000000-0000-4000-8000-000000000101")
EMPTY_EVENT_ID = UUID("00000000-0000-4000-8000-000000000102")
SOURCE_ID = UUID("00000000-0000-4000-8000-000000000103")
ATTENDEE_ID = UUID("00000000-0000-4000-8000-000000000104")
POPULATED_TITLE = "Route A populated synthetic meeting"
EMPTY_TITLE = "Route A empty synthetic meeting"
SYNTHETIC_ATTENDEE = "Route A Synthetic Attendee"
PROVIDER_EVENT_SOURCE_KIND = "provider_event"
ALLOWED_COMMITMENT_KEYS = frozenset(
    {"kind", "direction", "summary", "deadline", "escalation_level", "fingerprint"}
)
ALLOWED_KINDS = frozenset({"promise", "waiting_for", "follow_up", "obligation", "decision"})
ALLOWED_DIRECTIONS = frozenset({"owner_to_other", "other_to_owner", "self"})
ALLOWED_LEVELS = frozenset({"L0", "L1", "L2", "L3"})


@dataclass(frozen=True)
class SyntheticCommitment:
    kind: str
    direction: str
    summary: str
    deadline: str | None
    escalation_level: str
    fingerprint: str

    def validate(self) -> None:
        if self.kind not in ALLOWED_KINDS:
            raise ValueError(f"unsupported synthetic commitment kind: {self.kind}")
        if self.direction not in ALLOWED_DIRECTIONS:
            raise ValueError(f"unsupported synthetic commitment direction: {self.direction}")
        if self.escalation_level not in ALLOWED_LEVELS:
            raise ValueError(f"unsupported synthetic escalation level: {self.escalation_level}")
        if not self.summary.startswith("Route A synthetic "):
            raise ValueError("synthetic commitment summaries must carry the Route A prefix")
        if not self.fingerprint.startswith("route-a-"):
            raise ValueError("synthetic commitment fingerprints must carry the Route A prefix")


FIXTURE_COMMITMENTS = (
    SyntheticCommitment(
        kind="promise",
        direction="owner_to_other",
        summary="Route A synthetic owner commitment",
        deadline=None,
        escalation_level="L3",
        fingerprint="route-a-owner-l3",
    ),
    SyntheticCommitment(
        kind="waiting_for",
        direction="other_to_owner",
        summary="Route A synthetic counterparty commitment",
        deadline=None,
        escalation_level="L1",
        fingerprint="route-a-counterparty-l1",
    ),
)


def _environment() -> dict[str, str]:
    expected = {
        "POSTGRES_HOST": "postgres",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "route_a",
        "POSTGRES_USER": "route_a_app",
        "POSTGRES_SSLMODE": "disable",
    }
    values = {name: os.environ.get(name, "") for name in expected}
    if values != expected:
        unexpected = ", ".join(
            name for name, expected_value in expected.items() if values[name] != expected_value
        )
        raise RuntimeError(
            "Route A fixture loader requires exactly the declared disposable database identity: "
            f"{unexpected}"
        )
    if os.environ.get("POSTGRES_PASSWORD", ""):
        raise RuntimeError("Route A fixture loader refuses password-bearing database configuration")
    return values


def _commitment_payload() -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for commitment in FIXTURE_COMMITMENTS:
        commitment.validate()
        item = asdict(commitment)
        if set(item) != ALLOWED_COMMITMENT_KEYS:
            raise RuntimeError("fixture commitment exceeded the allowlist")
        payload.append(item)
    return payload


def _prep_envelope(event_id: UUID, title: str, starts_at: datetime) -> dict[str, Any]:
    return {
        "butler": "relationship",
        "event_id": str(event_id),
        "event_title": title,
        "event_starts_at": starts_at.isoformat(),
        "has_context": True,
        "attendees": [
            {
                "entity_id": str(ATTENDEE_ID),
                "name": SYNTHETIC_ATTENDEE,
                "dunbar_tier": None,
                "notes": [],
                "last_met": None,
                "last_met_event": None,
                "message_context": [],
                "commitments": _commitment_payload(),
            }
        ],
    }


async def load_fixture(manifest_path: Path) -> dict[str, Any]:
    config = _environment()
    starts_at = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(hours=1)
    ends_at = starts_at + timedelta(hours=1)
    connection = await asyncpg.connect(
        host=config["POSTGRES_HOST"],
        port=int(config["POSTGRES_PORT"]),
        database=config["POSTGRES_DB"],
        user=config["POSTGRES_USER"],
        ssl=False,
    )
    try:
        async with connection.transaction():
            await connection.execute(
                """
                INSERT INTO relationship.calendar_sources
                    (id, source_key, source_kind, lane, provider, calendar_id, display_name, writable, metadata)
                VALUES ($1, 'route-a-synthetic-calendar', $2, 'user', 'route-a', 'route-a',
                        'Route A synthetic calendar', false, '{}'::jsonb)
                ON CONFLICT (id) DO UPDATE SET updated_at = now()
                """,
                SOURCE_ID,
                PROVIDER_EVENT_SOURCE_KIND,
            )
            await connection.execute(
                """
                INSERT INTO public.entities (id, canonical_name, entity_type)
                VALUES ($1, $2, 'other')
                ON CONFLICT (id) DO UPDATE SET canonical_name = EXCLUDED.canonical_name
                """,
                ATTENDEE_ID,
                SYNTHETIC_ATTENDEE,
            )
            for event_id, title in (
                (POPULATED_EVENT_ID, POPULATED_TITLE),
                (EMPTY_EVENT_ID, EMPTY_TITLE),
            ):
                await connection.execute(
                    """
                    INSERT INTO relationship.calendar_events
                        (id, source_id, origin_ref, title, timezone, starts_at, ends_at, source_butler)
                    VALUES ($1, $2, $3, $4, 'UTC', $5, $6, 'relationship')
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title, starts_at = EXCLUDED.starts_at, ends_at = EXCLUDED.ends_at,
                        updated_at = now()
                    """,
                    event_id,
                    SOURCE_ID,
                    f"route-a-{event_id}",
                    title,
                    starts_at,
                    ends_at,
                )
                await connection.execute(
                    """
                    INSERT INTO relationship.calendar_event_instances
                        (event_id, source_id, origin_instance_ref, timezone, starts_at, ends_at)
                    VALUES ($1, $2, $3, 'UTC', $4, $5)
                    ON CONFLICT (event_id, origin_instance_ref) DO UPDATE SET
                        starts_at = EXCLUDED.starts_at, ends_at = EXCLUDED.ends_at, updated_at = now()
                    """,
                    event_id,
                    SOURCE_ID,
                    f"route-a-instance-{event_id}",
                    starts_at,
                    ends_at,
                )
                await connection.execute(
                    """
                    INSERT INTO relationship.calendar_event_entities (event_id, entity_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    event_id,
                    ATTENDEE_ID,
                )
            await connection.execute(
                """
                INSERT INTO relationship.state (key, value)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(), version = relationship.state.version + 1
                """,
                f"calendar/prep/{POPULATED_EVENT_ID}",
                json.dumps(_prep_envelope(POPULATED_EVENT_ID, POPULATED_TITLE, starts_at)),
            )
            await connection.execute(
                "DELETE FROM relationship.state WHERE key = $1", f"calendar/prep/{EMPTY_EVENT_ID}"
            )
        manifest = {
            "fixture": "route-a-meeting-prep-v1",
            "populated_event_id": str(POPULATED_EVENT_ID),
            "empty_event_id": str(EMPTY_EVENT_ID),
            "attendee_id": str(ATTENDEE_ID),
            "allowlisted_commitment_keys": sorted(ALLOWED_COMMITMENT_KEYS),
            "commitment_fingerprints": [item.fingerprint for item in FIXTURE_COMMITMENTS],
        }
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return manifest
    finally:
        await connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(load_fixture(args.manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

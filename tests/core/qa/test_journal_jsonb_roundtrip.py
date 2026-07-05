"""Real-Postgres regression: qa_investigation_events.data must not double-encode.

bu-cymc4: ``record_event()`` in butlers.core.qa.journal used to pre-serialize
``data`` with ``json.dumps()`` and bind it with an explicit ``$8::jsonb``
cast. Every asyncpg pool in this codebase registers a JSONB type codec
(``register_jsonb_codec``, src/butlers/db.py) whose encoder calls
``json.dumps()`` itself, so the old code path double-encoded ``data`` into a
jsonb-typed STRING instead of an OBJECT (see
tests/relationship/test_jsonb_codec.py). The mocked-session unit tests in
tests/core/qa/test_journal.py only assert on the Python value handed to the
mock -- they cannot prove what actually lands in a real jsonb column. This
test writes via the real ``record_event()`` code path against a
migrated-shape Postgres table and reads the row back directly.

``record_patrol_tick_events()`` (same module) is NOT covered here: it binds a
``text[]`` parameter and casts ``data::jsonb`` server-side inside the query,
so the client-side jsonb codec never touches it and it was never affected by
this bug (see the comment above ``data_values`` in journal.py).
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import pytest

from butlers.core.qa.journal import record_event

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

# Mirrors core_091's qa_investigation_events DDL closely enough to exercise
# the real INSERT: parent FKs (healing_attempts, qa_findings) are dropped
# since record_event() only needs a valid attempt_id/finding_id value, not a
# real referential integrity check, for this encoding-focused test.
_QA_INVESTIGATION_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS public.qa_investigation_events (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL,
    finding_id UUID,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    step       TEXT NOT NULL,
    text       TEXT NOT NULL,
    detail     TEXT,
    data       JSONB NOT NULL DEFAULT '{}'::jsonb
)
"""


async def test_record_event_data_roundtrips_as_dict_not_double_encoded_string(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_QA_INVESTIGATION_EVENTS_DDL)

        attempt_id = uuid.uuid4()
        finding_id = uuid.uuid4()
        non_json_safe_value = uuid.uuid4()
        event_id = await record_event(
            pool,
            attempt_id=attempt_id,
            finding_id=finding_id,
            step="flagged",
            text="Failure spotted",
            detail="ValueError at module:1",
            data={
                "fingerprint": "a" * 64,
                "extra": {"nested": True},
                "correlation_id": non_json_safe_value,
            },
            ts=datetime(2026, 5, 15, 4, 30, tzinfo=UTC),
        )
        assert event_id.version == 7

        row = await pool.fetchrow(
            "SELECT data FROM public.qa_investigation_events WHERE id = $1", event_id
        )
        assert row is not None
        stored_data = row["data"]
        assert isinstance(stored_data, dict), (
            f"data arrived as {type(stored_data).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored_data == {
            "fingerprint": "a" * 64,
            "extra": {"nested": True},
            # UUID sanitized to str by the json.dumps(default=str)/json.loads round-trip.
            "correlation_id": str(non_json_safe_value),
        }

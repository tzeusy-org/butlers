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
core-migrated Postgres table and reads the row back directly.

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


async def _attempt_and_finding(pool) -> tuple[uuid.UUID, uuid.UUID]:
    """Real parent rows for qa_investigation_events' FKs (core_091)."""
    attempt_id = await pool.fetchval(
        """
        INSERT INTO public.healing_attempts
            (fingerprint, butler_name, severity, exception_type, call_site)
        VALUES ($1, 'general', 2, 'ValueError', 'module:1')
        RETURNING id
        """,
        "a" * 64,
    )
    patrol_id = await pool.fetchval(
        "INSERT INTO public.qa_patrols (status) VALUES ('running') RETURNING id"
    )
    finding_id = await pool.fetchval(
        """
        INSERT INTO public.qa_findings
            (patrol_id, fingerprint, source_type, source_butler, severity, exception_type,
             event_summary, call_site, first_seen, last_seen)
        VALUES ($1, $2, 'log_scanner', 'general', 2, 'ValueError', 'Failure spotted',
                'module:1', now(), now())
        RETURNING id
        """,
        patrol_id,
        "a" * 64,
    )
    return attempt_id, finding_id


async def test_record_event_data_roundtrips_as_dict_not_double_encoded_string(db_pool) -> None:
    pool = db_pool
    attempt_id, finding_id = await _attempt_and_finding(pool)
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

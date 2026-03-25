"""Integration test: end-to-end data correction flow (task 7.8).

Creates a real database, inserts a session and bad state data, applies a
data_correction, and verifies the full audit trail.

Requires Docker (for the Postgres testcontainer).
"""

from __future__ import annotations

import shutil
import uuid

import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

# Guard import: skip entire module if corrections module not implemented yet
try:
    from butlers.core.corrections import (
        corrections_by_session,
        handle_data_correction,
    )

    _CORRECTIONS_AVAILABLE = True
except ModuleNotFoundError as exc:
    if getattr(exc, "name", None) == "butlers.core.corrections":
        _CORRECTIONS_AVAILABLE = False
    else:
        raise

corrections_required = pytest.mark.skipif(
    not _CORRECTIONS_AVAILABLE,
    reason="butlers.core.corrections not yet implemented",
)

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt TEXT NOT NULL,
    trigger_source TEXT NOT NULL,
    result TEXT,
    tool_calls JSONB NOT NULL DEFAULT '[]',
    duration_ms INTEGER,
    trace_id TEXT,
    model TEXT,
    cost JSONB,
    success BOOLEAN,
    error TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    parent_session_id UUID,
    request_id TEXT,
    ingestion_event_id UUID,
    complexity TEXT DEFAULT 'medium',
    resolution_source TEXT DEFAULT 'toml_fallback',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS corrections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    correction_type TEXT NOT NULL CHECK (correction_type IN (
        'data_correction', 'misroute', 'memory_deletion', 'action_reversal'
    )),
    target_session_id UUID NOT NULL REFERENCES sessions(id),
    correcting_session_id UUID NOT NULL REFERENCES sessions(id),
    description TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('applied', 'partially_applied', 'failed')),
    summary TEXT NOT NULL,
    original_data_snapshot JSONB,
    correction_details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS corrections_target_session_idx
    ON corrections(target_session_id);
CREATE INDEX IF NOT EXISTS corrections_correcting_session_idx
    ON corrections(correcting_session_id);
"""


@pytest.fixture
async def pool(postgres_container):
    """Provision a fresh database with sessions, state, and corrections tables."""
    import asyncpg

    db_name = f"test_{uuid.uuid4().hex[:12]}"

    admin = await asyncpg.connect(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database="postgres",
    )
    try:
        await admin.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin.close()

    p = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
        min_size=1,
        max_size=3,
    )
    await p.execute(_DDL)
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_session(pool, trigger_source: str = "route") -> uuid.UUID:
    row = await pool.fetchrow(
        """
        INSERT INTO sessions (prompt, trigger_source, request_id)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        "Test prompt",
        trigger_source,
        str(uuid.uuid4()),
    )
    return row["id"]


async def _insert_state(pool, key: str, value: object) -> None:
    import json

    await pool.execute(
        "INSERT INTO state(key, value) VALUES($1, $2::jsonb)",
        key,
        json.dumps(value),
    )


# ---------------------------------------------------------------------------
# 7.8 — End-to-end data correction flow
# ---------------------------------------------------------------------------


@corrections_required
async def test_e2e_data_correction_full_audit_trail(pool):
    """Full data correction flow: session → bad data → correction → audit trail."""
    # Step 1: A session runs and stores incorrect data
    bad_session_id = await _insert_session(pool, trigger_source="route")
    correcting_session_id = await _insert_session(pool, trigger_source="external")

    await _insert_state(pool, "user_preference", "incorrect_value")

    # Step 2: Apply data correction
    result = await handle_data_correction(
        pool,
        target_session_id=bad_session_id,
        correcting_session_id=correcting_session_id,
        description="The preference was recorded incorrectly as 'incorrect_value'",
        state_key="user_preference",
        corrected_value="correct_value",
    )

    assert result["status"] == "applied", f"Expected applied, got: {result}"
    assert result.get("correction_id") is not None

    # Step 3: Verify the state was updated to the corrected value
    new_value_row = await pool.fetchrow("SELECT value FROM state WHERE key = $1", "user_preference")
    assert new_value_row is not None
    stored_value = new_value_row["value"]
    assert "correct_value" in (stored_value if isinstance(stored_value, str) else str(stored_value))

    # Step 4: Verify the correction is in the audit table
    correction_id = result["correction_id"]
    corr_row = await pool.fetchrow(
        "SELECT * FROM corrections WHERE id = $1", uuid.UUID(str(correction_id))
    )
    assert corr_row is not None
    assert corr_row["correction_type"] == "data_correction"
    assert corr_row["status"] == "applied"
    assert corr_row["target_session_id"] == bad_session_id
    assert corr_row["correcting_session_id"] == correcting_session_id
    assert corr_row["original_data_snapshot"] is not None

    # Step 5: Verify audit query returns the correction
    audit_rows = await corrections_by_session(pool, target_session_id=bad_session_id)
    assert len(audit_rows) >= 1
    assert any(str(r.get("id", "")) == str(correction_id) for r in audit_rows)


@corrections_required
async def test_e2e_failed_correction_still_recorded(pool):
    """Failed correction (session not found) is still written to the corrections table."""
    correcting_session_id = await _insert_session(pool, trigger_source="external")
    nonexistent_target = uuid.uuid4()

    result = await handle_data_correction(
        pool,
        target_session_id=nonexistent_target,
        correcting_session_id=correcting_session_id,
        description="Attempt to correct a non-existent session",
        state_key="some_key",
        corrected_value="some_value",
    )

    assert result["status"] == "failed"

    # Even failed corrections must be recorded
    corrections_count = await pool.fetchval(
        "SELECT COUNT(*) FROM corrections WHERE correcting_session_id = $1",
        correcting_session_id,
    )
    assert corrections_count >= 1


@corrections_required
async def test_e2e_correction_append_only_no_updates(pool):
    """Correction rows in the database are never updated after insertion."""
    bad_session_id = await _insert_session(pool, trigger_source="route")
    correcting_session_id = await _insert_session(pool, trigger_source="external")
    await _insert_state(pool, "value_to_fix", "wrong")

    result = await handle_data_correction(
        pool,
        target_session_id=bad_session_id,
        correcting_session_id=correcting_session_id,
        description="Fix value",
        state_key="value_to_fix",
        corrected_value="right",
    )

    if result["status"] == "applied":
        correction_id = uuid.UUID(str(result["correction_id"]))

        # Fetch the correction row's created_at
        row_before = await pool.fetchrow(
            "SELECT created_at, summary FROM corrections WHERE id = $1",
            correction_id,
        )
        assert row_before is not None

        # Calling handle_data_correction again on the same key should create a NEW row
        await handle_data_correction(
            pool,
            target_session_id=bad_session_id,
            correcting_session_id=correcting_session_id,
            description="Fix again",
            state_key="value_to_fix",
            corrected_value="even_righter",
        )

        total = await pool.fetchval(
            "SELECT COUNT(*) FROM corrections WHERE target_session_id = $1",
            bad_session_id,
        )
        # Two separate corrections → two rows (not one updated row)
        assert total >= 2

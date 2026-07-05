"""Real-Postgres regression for the calendar undo action_result jsonb bug (bu-x92jw).

``api/routers/calendar_workspace.py``'s undo-marker writes used to pre-serialize
the marker with ``json.dumps()`` and bind it through ``$2::jsonb``. Every asyncpg
pool in this codebase registers a JSONB type codec (``register_jsonb_codec``,
``src/butlers/db.py``) whose encoder calls ``json.dumps()`` itself -- so the old
code path double-encoded the marker into a jsonb-typed STRING instead of an
OBJECT. Postgres's ``||`` operator between a jsonb object and a jsonb scalar
coerces *both* operands into an array, corrupting ``action_result`` into
``[{...original...}, "<json string>"]`` and defeating the
``NOT (action_result ? 'undo')`` double-dispatch guard (which checks for a
top-level object key, never present on an array).

The mocked-pool unit tests in ``tests/api/test_calendar_workspace.py`` cannot
catch this class of bug -- they never round-trip a value through asyncpg's
JSONB codec. These tests exercise the exact SQL fragments from the undo
handler against a real Postgres instance (testcontainers) to prove:

1. The fixed write path (bind a plain dict, no ``json.dumps``, no ``::jsonb``
   cast) round-trips ``action_result`` as a jsonb OBJECT, and the
   ``? 'undo'`` guard correctly blocks a second write.
2. The old buggy write path (``json.dumps()`` + ``::jsonb`` cast) reproduces
   the exact array-corruption failure mode, documenting why the fix matters.
3. A legacy corrupted (array-shaped) row is reconstructed by
   ``_reconstruct_action_result`` and healed back to a proper object by the
   guarded repair UPDATE, idempotently.
"""

from __future__ import annotations

import json
import shutil
import uuid

import pytest

from butlers.api.routers.calendar_workspace import _reconstruct_action_result

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_CREATE_TABLE_SQL = """
CREATE TEMP TABLE calendar_action_log_test (
    id UUID PRIMARY KEY,
    action_result JSONB
)
"""

_CLAIM_SQL = """
UPDATE calendar_action_log_test
SET action_result = COALESCE(action_result, '{}'::jsonb) || $2
WHERE id = $1
  AND NOT (COALESCE(action_result, '{}'::jsonb) ? 'undo')
RETURNING id
"""

# The pre-fix pattern: json.dumps() the marker, then bind through an explicit
# ::jsonb cast. Reproduced here (not imported from prod code, which no longer
# contains it) purely to document and lock in the failure mode being guarded
# against.
_BUGGY_CLAIM_SQL = """
UPDATE calendar_action_log_test
SET action_result = COALESCE(action_result, '{}'::jsonb) || $2::jsonb
WHERE id = $1
  AND NOT (COALESCE(action_result, '{}'::jsonb) ? 'undo')
RETURNING id
"""

_REPAIR_SQL = """
UPDATE calendar_action_log_test
SET action_result = $2
WHERE id = $1
  AND jsonb_typeof(action_result) = 'array'
"""


async def test_fixed_write_path_round_trips_object_and_guard_blocks_second_write(
    provisioned_postgres_pool,
):
    """Binding the marker as a plain dict (the fix) keeps action_result an
    OBJECT, so the ``? 'undo'`` guard correctly blocks a repeat undo claim."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_CREATE_TABLE_SQL)
        action_id = uuid.uuid4()
        await pool.execute(
            "INSERT INTO calendar_action_log_test (id, action_result) VALUES ($1, $2)",
            action_id,
            {"status": "updated", "pre_state": {"event_id": "evt-1"}},
        )

        marker = {"undo": {"status": "pending", "request_id": "undo-1"}}
        first_claim = await pool.fetchval(_CLAIM_SQL, action_id, marker)
        assert first_claim == action_id

        row = await pool.fetchrow(
            "SELECT action_result FROM calendar_action_log_test WHERE id = $1", action_id
        )
        stored = row["action_result"]
        assert isinstance(stored, dict), (
            f"Expected action_result to stay a jsonb OBJECT but got "
            f"{type(stored).__name__!r}: {stored!r}"
        )
        assert stored["undo"] == {"status": "pending", "request_id": "undo-1"}
        assert stored["pre_state"] == {"event_id": "evt-1"}

        # Second claim attempt (concurrent-undo guard): the ``? 'undo'`` check
        # now correctly matches the object's top-level key, so the guarded
        # UPDATE affects zero rows and RETURNING yields no id.
        second_claim = await pool.fetchval(_CLAIM_SQL, action_id, marker)
        assert second_claim is None


async def test_buggy_write_path_corrupts_action_result_into_array(provisioned_postgres_pool):
    """Documents the pre-fix failure mode: json.dumps() + ::jsonb double-encodes
    the marker, and Postgres's object-||-scalar coercion turns action_result
    into an array, silently defeating the double-dispatch guard."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_CREATE_TABLE_SQL)
        action_id = uuid.uuid4()
        await pool.execute(
            "INSERT INTO calendar_action_log_test (id, action_result) VALUES ($1, $2)",
            action_id,
            {"status": "updated", "pre_state": {"event_id": "evt-1"}},
        )

        marker_json_string = json.dumps({"undo": {"status": "pending", "request_id": "undo-1"}})
        claimed = await pool.fetchval(_BUGGY_CLAIM_SQL, action_id, marker_json_string)
        assert claimed == action_id  # the guard still "succeeds" -- that's the bug

        row = await pool.fetchrow(
            "SELECT action_result FROM calendar_action_log_test WHERE id = $1", action_id
        )
        stored = row["action_result"]
        assert isinstance(stored, list), (
            "Expected the buggy path to corrupt action_result into a jsonb "
            f"ARRAY but got {type(stored).__name__!r}: {stored!r}"
        )
        assert stored[0] == {"status": "updated", "pre_state": {"event_id": "evt-1"}}
        assert isinstance(stored[1], str)
        assert json.loads(stored[1]) == {"undo": {"status": "pending", "request_id": "undo-1"}}

        # The double-dispatch guard is now defeated: '? undo' never matches an
        # array element that is a JSON *string*, so a second claim still
        # "succeeds" against the corrupted row -- exactly the safety bug.
        second_claim = await pool.fetchval(_BUGGY_CLAIM_SQL, action_id, marker_json_string)
        assert second_claim == action_id


async def test_legacy_corrupted_array_row_is_reconstructed_and_healed(provisioned_postgres_pool):
    """A pre-existing corrupted (array-shaped) row is reconstructed correctly
    by ``_reconstruct_action_result`` and the guarded repair UPDATE heals it
    back into a proper jsonb object, idempotently."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_CREATE_TABLE_SQL)
        action_id = uuid.uuid4()
        original = {"status": "updated", "pre_state": {"event_id": "evt-1"}}
        await pool.execute(
            "INSERT INTO calendar_action_log_test (id, action_result) VALUES ($1, $2)",
            action_id,
            original,
        )
        # Corrupt the row the same way the pre-fix code actually did: run the
        # OLD buggy claim (json.dumps() marker + ::jsonb cast) against a real
        # asyncpg/Postgres round-trip -- this is what a genuinely legacy row
        # looks like (a hand-built JSON string inserted via ``$2::jsonb``
        # round-trips as a scalar jsonb STRING, not an array, since it never
        # passes through the codec the way production code does).
        undo_marker_string = json.dumps({"undo": {"status": "pending", "request_id": "undo-prev"}})
        await pool.execute(_BUGGY_CLAIM_SQL, action_id, undo_marker_string)

        row = await pool.fetchrow(
            "SELECT action_result FROM calendar_action_log_test WHERE id = $1", action_id
        )
        raw = row["action_result"]
        assert isinstance(raw, list)

        reconstructed = _reconstruct_action_result(raw)
        assert reconstructed["status"] == "updated"
        assert reconstructed["pre_state"] == {"event_id": "evt-1"}
        assert reconstructed["undo"] == {"status": "pending", "request_id": "undo-prev"}

        # Repair: heal the row back into a proper object, guarded so it only
        # touches array-shaped rows.
        await pool.execute(_REPAIR_SQL, action_id, reconstructed)

        healed_row = await pool.fetchrow(
            "SELECT action_result FROM calendar_action_log_test WHERE id = $1", action_id
        )
        healed = healed_row["action_result"]
        assert isinstance(healed, dict)
        assert healed == reconstructed

        # Idempotent: repairing an already-healed (object-shaped) row is a
        # no-op -- the jsonb_typeof guard matches zero rows.
        await pool.execute(_REPAIR_SQL, action_id, {"status": "should-not-apply"})
        unchanged_row = await pool.fetchrow(
            "SELECT action_result FROM calendar_action_log_test WHERE id = $1", action_id
        )
        assert unchanged_row["action_result"] == reconstructed

        # And once healed, the real double-dispatch guard works again.
        second_claim = await pool.fetchval(_CLAIM_SQL, action_id, reconstructed)
        assert second_claim is None

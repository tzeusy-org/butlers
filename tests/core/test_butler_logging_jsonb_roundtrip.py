"""Real-Postgres regression: ButlerLogger metadata must not double-encode JSONB.

bu-cymc4: ``ButlerLogger._write()`` used to pre-serialize ``metadata`` with
``json.dumps()`` and bind it with an explicit ``$N::jsonb`` cast. Every
asyncpg pool in this codebase registers a JSONB type codec
(``register_jsonb_codec``, src/butlers/db.py) whose encoder calls
``json.dumps()`` itself, so the old code path double-encoded metadata into a
jsonb-typed STRING instead of an OBJECT (see
tests/relationship/test_jsonb_codec.py). The mocked-pool unit tests in
tests/core/test_butler_logging.py only assert on the Python value handed to
the mock pool -- they cannot prove what actually lands in a real jsonb
column. This test writes via the real ``ButlerLogger.log()`` code path
against the core-migrated ``general.butler_logs`` table and reads the row back directly.
"""

from __future__ import annotations

import shutil

import pytest

from butlers.core.butler_logging import ButlerLogger

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


async def test_butler_log_metadata_roundtrips_as_dict_not_double_encoded_string(db) -> None:
    # ButlerLogger issues unqualified table names and relies on the pool's
    # search_path, as in production.
    pool = await db("core", schemas={"core": "general"}, schema="general")

    bl = ButlerLogger(pool=pool, schema="general")
    await bl.log(
        "INFO",
        "structured log line",
        source="test",
        metadata={"nested": {"a": 1}, "tags": ["alpha", "beta"]},
    )

    row = await pool.fetchrow("SELECT metadata FROM butler_logs WHERE msg = 'structured log line'")
    assert row is not None
    stored_metadata = row["metadata"]
    assert isinstance(stored_metadata, dict), (
        f"metadata arrived as {type(stored_metadata).__name__!r}, not a dict — "
        "the jsonb column was double-encoded into a string."
    )
    assert stored_metadata == {"nested": {"a": 1}, "tags": ["alpha", "beta"]}

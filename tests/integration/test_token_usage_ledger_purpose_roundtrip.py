"""Real-Postgres regression: purpose-tagged spend attribution (bu-qvnce.12).

Exercises the actual production write paths — not just mocked-pool unit
tests — against a real, fully-migrated Postgres instance (testcontainers):

- core_156 additively adds ``public.token_usage_ledger.purpose`` (nullable,
  no default) without disturbing pre-existing rows or the cache-token
  columns core_155 added just before it.
- core_157 seeds the two new ``runtime_type='api'`` catalog rows
  (``api-haiku-cheap`` / ``api-haiku-specialty``) at higher priority than
  every pre-existing 'cheap'/'specialty' entry, so ``resolve_model`` picks
  them by default — proving the "flip" (slice 2) actually took effect on a
  migrated database, not just in ``model_catalog_defaults.toml`` (which has
  no effect post-bootstrap; see core_157's docstring).
- ``DiscretionDispatcher.call(identity=...)`` writes the per-connector
  identity as ``butler_name`` and ``purpose="discretion"`` through a real
  asyncpg INSERT (the mocked-pool unit tests in
  ``tests/connectors/test_discretion_dispatcher.py`` cannot catch a
  parameter-count/column-order mismatch between ``_LEDGER_INSERT_SQL`` and
  the real table — this test can), and its composition/resume columns land
  honestly NULL (bu-hz0g0) since the discretion lane never composes a
  layered prompt.
- core_223 additively adds the five prompt-composition columns
  (``base_prompt_tokens``, ``timezone_instruction_tokens``,
  ``context_preamble_tokens``, ``routing_instructions_tokens``,
  ``memory_context_tokens``) and ``resume_outcome`` (bu-hz0g0), all
  nullable/no-default like ``purpose``.
"""

from __future__ import annotations

import shutil
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from butlers.core.discretion_dispatcher import DiscretionDispatcher
from butlers.core.model_routing import Complexity, record_token_usage, resolve_model

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
def ledger_url(fresh_db) -> str:
    # core_004 creates the current month's token_usage_ledger partition onward.
    return fresh_db("core")


@pytest.fixture
async def ledger_pool(db, ledger_url: str) -> asyncpg.Pool:
    return await db.open(ledger_url)


async def test_purpose_column_exists_and_is_nullable(ledger_pool: asyncpg.Pool) -> None:
    row = await ledger_pool.fetchrow(
        """
        SELECT is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'token_usage_ledger'
          AND column_name = 'purpose'
        """
    )
    assert row is not None, "core_156 did not add token_usage_ledger.purpose"
    assert row["is_nullable"] == "YES"
    assert row["column_default"] is None


async def test_seeded_api_catalog_rows_win_cheap_and_specialty_tiers(
    ledger_pool: asyncpg.Pool,
) -> None:
    """core_157's seed rows outrank every pre-existing entry for their tier."""
    cheap = await resolve_model(ledger_pool, "switchboard", Complexity.CHEAP)
    assert cheap is not None
    runtime_type, model_id, _extra_args, _id, _timeout = cheap
    assert runtime_type == "api"
    assert model_id == "claude-haiku-4-5-20251001"

    specialty = await resolve_model(ledger_pool, "__discretion__", Complexity.SPECIALTY)
    assert specialty is not None
    assert specialty[0] == "api"


async def test_discretion_dispatcher_writes_identity_and_purpose_via_real_pool(
    ledger_pool: asyncpg.Pool,
    ledger_url: str,
) -> None:
    """DiscretionDispatcher.call(identity=...) round-trips through a real INSERT."""
    dispatcher = DiscretionDispatcher(pool=ledger_pool)
    fake_adapter = AsyncMock()
    fake_adapter.invoke = AsyncMock(
        return_value=("FORWARD: looks real", [], {"input_tokens": 7, "output_tokens": 3})
    )
    # create_worker is synchronous even when the invocation is async. Each
    # detached worker starts without process metadata; the real ledger write
    # and all purpose/identity/null readback assertions below remain unchanged.
    fake_adapter.create_worker = lambda: SimpleNamespace(
        invoke=AsyncMock(
            return_value=("FORWARD: looks real", [], {"input_tokens": 7, "output_tokens": 3})
        ),
        last_process_info=None,
    )

    # Preserve a causal control for the old fixture: it omitted the production
    # JSONB codec, although serving/resolution evidence is a dict. Capture only
    # the first fixed exception category/SQLSTATE, never its message or binds.
    failures = []

    def capture_first_recorder_failure(*_args, **_kwargs):
        exc = sys.exc_info()[1]
        if not failures:
            failures.append(
                {
                    "argument_codec_refusal": isinstance(exc, asyncpg.DataError),
                    "sqlstate_is_data_exception": getattr(exc, "sqlstate", None) == "22000",
                }
            )

    unconfigured = await asyncpg.create_pool(ledger_url, min_size=1, max_size=1)
    try:
        old_fixture_dispatcher = DiscretionDispatcher(pool=unconfigured)
        with (
            patch.object(
                old_fixture_dispatcher, "_get_or_create_adapter", return_value=fake_adapter
            ),
            patch.object(
                old_fixture_dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)
            ),
            patch(
                "butlers.core.dispatch_outcomes.logger.warning",
                side_effect=capture_first_recorder_failure,
            ),
        ):
            old_result = await old_fixture_dispatcher.call("hi", identity="tg:555")
        assert old_result == "FORWARD: looks real"
        assert failures == [{"argument_codec_refusal": True, "sqlstate_is_data_exception": True}]
        assert (
            await ledger_pool.fetchval(
                "SELECT count(*) FROM public.token_usage_ledger WHERE butler_name='tg:555'"
            )
            == 0
        )
    finally:
        await unconfigured.close()

    with (
        patch.object(dispatcher, "_get_or_create_adapter", return_value=fake_adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
    ):
        result = await dispatcher.call("hi", identity="tg:555")

    assert result == "FORWARD: looks real"

    row = await ledger_pool.fetchrow(
        """
        SELECT butler_name, session_id, purpose, purpose_lane, input_tokens, output_tokens,
               base_prompt_tokens, timezone_instruction_tokens,
               context_preamble_tokens, routing_instructions_tokens,
               memory_context_tokens, resume_outcome
        FROM public.token_usage_ledger
        WHERE butler_name = 'tg:555'
        ORDER BY recorded_at DESC LIMIT 1
        """
    )
    assert row is not None
    assert row["session_id"] is None
    assert row["purpose"] == "discretion"
    assert row["purpose_lane"] == "standard"
    assert row["input_tokens"] == 7 and row["output_tokens"] == 3
    # bu-hz0g0: discretion never composes a layered prompt or resumes a
    # conversation, so every new column stays honestly NULL.
    assert row["base_prompt_tokens"] is None
    assert row["timezone_instruction_tokens"] is None
    assert row["context_preamble_tokens"] is None
    assert row["routing_instructions_tokens"] is None
    assert row["memory_context_tokens"] is None
    assert row["resume_outcome"] is None


async def test_record_token_usage_purpose_defaults_null_when_omitted(
    ledger_pool: asyncpg.Pool,
) -> None:
    """Backward compatibility: callers that don't pass purpose= get an honest NULL,
    never a fabricated category."""
    row = await ledger_pool.fetchrow(
        "SELECT id FROM public.model_catalog WHERE alias = 'api-haiku-cheap'"
    )
    assert row is not None
    entry_id = row["id"]

    await record_token_usage(
        ledger_pool,
        catalog_entry_id=entry_id,
        butler_name="atlas",
        session_id=uuid.uuid4(),
        input_tokens=1,
        output_tokens=1,
    )
    written = await ledger_pool.fetchrow(
        "SELECT purpose, purpose_lane FROM public.token_usage_ledger WHERE catalog_entry_id = $1 "
        "ORDER BY recorded_at DESC LIMIT 1",
        entry_id,
    )
    assert written is not None and written["purpose"] is None
    assert written["purpose_lane"] == "standard"


async def test_composition_and_resume_columns_exist_and_are_nullable(
    ledger_pool: asyncpg.Pool,
) -> None:
    """core_223 added all six columns nullable, no default (bu-hz0g0)."""
    rows = await ledger_pool.fetch(
        """
        SELECT column_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'token_usage_ledger'
          AND column_name = ANY($1::text[])
        """,
        [
            "base_prompt_tokens",
            "timezone_instruction_tokens",
            "context_preamble_tokens",
            "routing_instructions_tokens",
            "memory_context_tokens",
            "resume_outcome",
        ],
    )
    by_name = {row["column_name"]: row for row in rows}
    assert set(by_name) == {
        "base_prompt_tokens",
        "timezone_instruction_tokens",
        "context_preamble_tokens",
        "routing_instructions_tokens",
        "memory_context_tokens",
        "resume_outcome",
    }
    for column_name, row in by_name.items():
        assert row["is_nullable"] == "YES", f"{column_name} must be nullable"
        assert row["column_default"] is None, f"{column_name} must have no default"


async def test_record_token_usage_persists_composition_digest_and_resume_outcome(
    ledger_pool: asyncpg.Pool,
) -> None:
    """A caller passing the new kwargs (the spawner's composed-prompt path)
    round-trips every value through a real INSERT -- catching a
    parameter-count/column-order mismatch the mocked-pool unit tests cannot."""
    row = await ledger_pool.fetchrow(
        "SELECT id FROM public.model_catalog WHERE alias = 'api-haiku-cheap'"
    )
    assert row is not None
    entry_id = row["id"]
    session_id = uuid.uuid4()

    await record_token_usage(
        ledger_pool,
        catalog_entry_id=entry_id,
        butler_name="atlas",
        session_id=session_id,
        input_tokens=100,
        output_tokens=20,
        purpose="route",
        base_prompt_tokens=400,
        timezone_instruction_tokens=10,
        context_preamble_tokens=25,
        routing_instructions_tokens=0,
        memory_context_tokens=150,
        resume_outcome="resumed",
    )
    written = await ledger_pool.fetchrow(
        """
        SELECT base_prompt_tokens, timezone_instruction_tokens, context_preamble_tokens,
               routing_instructions_tokens, memory_context_tokens, resume_outcome
        FROM public.token_usage_ledger WHERE session_id = $1
        """,
        session_id,
    )
    assert written is not None
    assert written["base_prompt_tokens"] == 400
    assert written["timezone_instruction_tokens"] == 10
    assert written["context_preamble_tokens"] == 25
    assert written["routing_instructions_tokens"] == 0
    assert written["memory_context_tokens"] == 150
    assert written["resume_outcome"] == "resumed"

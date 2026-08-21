"""End-to-end lineage integration test for the dashboard chat widget (bu-bldja).

Track C of the owner chat-widget epic (bu-mj2k2). Tracks A+B shipped the real
Switchboard ingestion + session-linked reply plumbing:

  ``build_dashboard_envelope`` (src/butlers/api/conversation_envelope.py)
    → real ``ingest_v1`` (roster/switchboard/tools/ingestion/ingest.py) which
      persists a ``message_inbox`` row and, for a per-butler dashboard
      conversation, annotates ``request_context`` with a
      ``triage_decision='route_to'`` / ``triage_target=<butler>`` pin
    → real ``MessagePipeline.process`` (src/butlers/modules/pipeline.py) which
      takes the policy-bypass route path and dispatches via ``route()``
    → the routed butler session persists its confirm-loop reply through
      ``conversation_reply_create`` (src/butlers/api/conversations.py) into
      ``public.dashboard_messages``, stamped with the request_id it was
      routed with (recovered from the ambient runtime routing context by
      ``_best_effort_request_id`` in core_tools/_conversation_reply.py).

Before this file no test exercised that whole chain on a live database: the
decomposition-flow integration test mocks the spawner and asserts routing
fan-out, and tests/api/test_conversations.py covers the router/SSE + data
layer against AsyncMock pools. The gap this closes is the *lineage*: proving
that the request_id returned by ``ingest_v1`` is the very same id that lands
on the ``dashboard_messages`` reply row and on the ``sessions`` row — with no
fabricated / placeholder id substituted at any hop.

Faithful seam
-------------
Everything is real against a testcontainers PostgreSQL provisioned with the
actual ``core`` + ``switchboard`` Alembic chains: the ingest surface, the
pipeline routing *decision*, every ``message_inbox`` lifecycle write, the
``sessions`` rows, and the ``dashboard_messages`` lineage writes.

The **only** boundary mocked is the LLM spawn: ``route()`` (the cross-daemon
dispatch beyond which, in production, the target butler's Spawner runs the LLM
session in a separate process) is replaced by a deterministic stand-in that
reproduces exactly what a spawned session does to the database — create a
``sessions`` row with the routed request_id, set the runtime routing context
the way the Spawner does, then call the real ``conversation_reply_create`` and
``session_complete``. The mock uses **only** ids it reads out of the route
envelope the real pipeline handed it, never a test-closure id, so a broken
request_id hand-off would surface as a lineage-assertion failure rather than
being papered over.

NOTE: the LLM inference itself is necessarily mocked in CI. Full real-LLM
validation — a live daemon actually spawning a butler session that decides to
call ``conversation_reply`` — is a manual live-stack check, not something this
(or any) CI-runnable test can cover.
"""

from __future__ import annotations

import json
import shutil
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse
from uuid import UUID

import asyncpg
import pytest

from alembic import command
from butlers.api.conversation_envelope import build_dashboard_envelope
from butlers.api.conversations import (
    conversation_create,
    conversation_reply_create,
    message_create,
    message_find_reply_since,
)
from butlers.core.sessions import session_complete, session_create
from butlers.core.tool_call_capture import (
    clear_runtime_session_routing_context,
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
    set_runtime_session_routing_context,
)
from butlers.core_tools._conversation_reply import _best_effort_request_id
from butlers.db import register_jsonb_codec
from butlers.migrations import _build_alembic_config
from butlers.modules.pipeline import MessagePipeline
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

# The whole file needs a real database; skip cleanly where Docker is absent.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

# Import path the dashboard router / connectors use; resolves to
# roster/switchboard/tools/ingestion/ingest.py via the butler tool loader.
from butlers.tools.switchboard.ingestion.ingest import ingest_v1  # noqa: E402

# A per-butler dashboard conversation pins its envelope to this target so the
# pipeline routes deterministically (no LLM classification hop).
_TARGET_BUTLER = "finance"

# Mock butler registry so ingest_v1's pinned_target validation accepts the pin
# without a live butler registry.
_MOCK_BUTLERS = [
    {"name": _TARGET_BUTLER, "description": "Finance management"},
    {"name": "health", "description": "Health tracking"},
]

# Patch targets — mirror the paths the shipped code imports from.
_CLASSIFY_BUTLERS_PATH = "butlers.tools.switchboard.routing.classify._load_available_butlers"
_ROUTE_PATH = "butlers.tools.switchboard.routing.route.route"

_RETAINED_DASHBOARD_CONVERSATION_COLUMNS = {
    "id",
    "butler_name",
    "title",
    "status",
    "created_at",
    "updated_at",
    "message_count",
    "routed_butler",
}
_RETIRED_DASHBOARD_CONVERSATION_AGGREGATES = {
    "total_input_tokens",
    "total_output_tokens",
    "total_duration_ms",
}


# ---------------------------------------------------------------------------
# Fixtures — real core + switchboard schema on a fresh testcontainers DB
# ---------------------------------------------------------------------------


@pytest.fixture
def migrated_db_url(postgres_container) -> str:
    """Fresh database with the real ``core`` + ``switchboard`` Alembic chains.

    Function-scoped (a unique db name per test) so each test gets full
    isolation without inter-test truncation. ``create_migrated_test_db``
    installs the required extensions before running migrations.
    """
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        # bu-nz1wx: provision the switchboard chain into the real ``switchboard``
        # schema so production's schema-qualified ``switchboard.message_inbox``
        # writes (ingest_v1) resolve — mirroring production's one-db/multi-schema
        # topology. Core tables stay in public; the test's own message_inbox reads
        # are schema-qualified below, so the pool keeps the default search_path.
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
def rollback_db_url(postgres_container) -> str:
    """Core chain stopped at ``core_175`` — the revision the rollback test owns.

    Rollback is not uniformly available across the core chain: revisions after
    ``core_175`` install privileged boundaries whose rollback is deliberately
    bootstrap-only, so a chain migrated to head cannot be walked back to
    ``core_174``.  Bounding the upgrade keeps this test on its own revision.
    """
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
        revisions={"core": "core_175"},
    )


@pytest.fixture
def bootstrap_db_url(postgres_container, rollback_db_url: str) -> str:
    """Return the disposable control URL for privileged rollback only."""
    db_name = urlparse(rollback_db_url).path.lstrip("/")
    return migration_bootstrap_db_url(postgres_container, db_name)


@pytest.fixture
async def rollback_pool(rollback_db_url: str):
    """asyncpg pool over the ``core_175``-bounded database (catalog reads only)."""
    p = await asyncpg.create_pool(rollback_db_url, min_size=1, max_size=2)
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
async def pool(migrated_db_url: str):
    """asyncpg pool over the migrated database.

    ``register_jsonb_codec`` is wired as the connection ``init`` so JSONB
    columns (``message_inbox.request_context``, ``dashboard_messages.tool_calls``,
    ``sessions.cost`` …) auto-encode dicts exactly the way the production
    ``butlers.db.Database`` pool does — without it, ``ingest_v1`` fails to
    persist request_context.
    """
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        # bu-nz1wx: switchboard tables + the message_inbox partition function live
        # in the ``switchboard`` schema; the core per-butler tables this test's
        # spawned-session stand-in writes (``sessions``, ``dashboard_messages``,
        # ``conversations``) live in ``public`` and exist in every butler schema —
        # only ``public.sessions`` carries the core_155 cache-token columns. So
        # ``public`` must win for those bare core reads/writes, while the
        # switchboard-only objects (the partition function; the qualified
        # ``switchboard.message_inbox`` writes) resolve by fallthrough. Hence
        # ``public`` first, then ``switchboard``.
        server_settings={"search_path": "public, switchboard"},
    )
    try:
        yield p
    finally:
        await p.close()


async def test_migrated_dashboard_conversation_schema_omits_dead_aggregates(
    bootstrap_db_url: str,
    rollback_pool: asyncpg.Pool,
) -> None:
    """core_175 drops only dead aggregates and restores their prior shape on rollback."""
    table_exists = await rollback_pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'dashboard_conversations'
        )
        """
    )
    assert table_exists is True

    current_columns = {
        row["column_name"]
        for row in await rollback_pool.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'dashboard_conversations'
            """
        )
    }
    assert _RETAINED_DASHBOARD_CONVERSATION_COLUMNS <= current_columns
    assert _RETIRED_DASHBOARD_CONVERSATION_AGGREGATES.isdisjoint(current_columns)

    command.downgrade(_build_alembic_config(bootstrap_db_url, chains=["core"]), "core_174")

    restored_columns = {
        row["column_name"]: row
        for row in await rollback_pool.fetch(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'dashboard_conversations'
              AND column_name = ANY($1::text[])
            """,
            list(_RETIRED_DASHBOARD_CONVERSATION_AGGREGATES),
        )
    }
    assert set(restored_columns) == _RETIRED_DASHBOARD_CONVERSATION_AGGREGATES
    for column in restored_columns.values():
        assert column["data_type"] == "bigint"
        assert column["is_nullable"] == "NO"
        assert column["column_default"] == "0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_conversation_and_user_message(
    pool: asyncpg.Pool, *, message_text: str
) -> tuple[UUID, dict[str, Any]]:
    """Replicate the router's pre-submission persistence.

    The dashboard POST handler creates the conversation row and persists the
    user message *before* submitting to the Switchboard (so a retry re-submits
    the same content). Return the conversation id and the user message row.
    """
    conv = await conversation_create(pool, butler_name=_TARGET_BUTLER, first_message=message_text)
    conversation_id: UUID = conv["id"]
    user_msg = await message_create(
        pool,
        conversation_id=conversation_id,
        role="user",
        content=message_text,
    )
    return conversation_id, user_msg


async def _read_request_context(pool: asyncpg.Pool, message_inbox_id: UUID) -> dict[str, Any]:
    """Read back the persisted request_context the scanner would hand the pipeline.

    In production a scanner reads the accepted ``message_inbox`` row and invokes
    ``MessagePipeline.process`` with its request_context; the test does the same
    so the pinned ``triage_decision`` reaches the pipeline exactly as shipped.
    """
    row = await pool.fetchrow(
        "SELECT request_context, lifecycle_state FROM switchboard.message_inbox WHERE id = $1",
        message_inbox_id,
    )
    assert row is not None, "message_inbox row missing after ingest_v1"
    assert row["lifecycle_state"] == "accepted"
    rc = row["request_context"]
    if isinstance(rc, str):
        rc = json.loads(rc)
    return rc


# ---------------------------------------------------------------------------
# Happy path — routed, session completes, lineage intact
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_dashboard_message_lineage_end_to_end(pool):
    """ingest → real pipeline route → mock-spawned session → intact lineage.

    Asserts the request_id returned by ``ingest_v1`` is the same id that flows
    into the route envelope, is recovered by the routed session via the real
    runtime-routing-context path, and lands on both the ``dashboard_messages``
    reply row and the ``sessions`` row — the join key that links a dashboard
    reply back to the session that produced it.
    """
    message_text = "Record: dinner with Alice cost $80, split evenly."
    conversation_id, user_msg = await _seed_conversation_and_user_message(
        pool, message_text=message_text
    )

    # --- Real ingest surface -------------------------------------------------
    envelope = build_dashboard_envelope(
        conversation_id=conversation_id,
        message_id=user_msg["id"],
        message_text=message_text,
        pinned_target=_TARGET_BUTLER,
    )
    with patch(_CLASSIFY_BUTLERS_PATH, new_callable=AsyncMock, return_value=_MOCK_BUTLERS):
        ingest_response = await ingest_v1(pool, envelope, enable_thread_affinity=False)

    assert ingest_response.status == "accepted"
    ingest_request_id: UUID = ingest_response.request_id
    assert isinstance(ingest_request_id, UUID)

    # The pin was recorded on request_context for the pipeline to honour.
    request_context = await _read_request_context(pool, ingest_request_id)
    assert request_context["triage_decision"] == "route_to"
    assert request_context["triage_target"] == _TARGET_BUTLER
    assert request_context["source_thread_identity"] == str(conversation_id)

    # --- Mock ONLY the spawn boundary; capture what the pipeline handed it ---
    captured: dict[str, Any] = {}

    async def mock_route(
        pool_arg,
        *,
        target_butler,
        tool_name,
        args,
        source_butler="switchboard",
        **_kwargs,
    ) -> dict[str, Any]:
        # Everything the routed session knows comes out of the route envelope
        # the real pipeline built — never a test-closure id.
        route_ctx = args.get("__switchboard_route_context", {})
        req_ctx = args.get("request_context", {})
        routed_request_id = route_ctx.get("request_id")
        thread_identity = req_ctx.get("source_thread_identity")
        captured["target_butler"] = target_butler
        captured["route_request_id"] = routed_request_id
        captured["thread_identity"] = thread_identity

        # Spawner analogue: create the session row with the routed request_id.
        session_id = await session_create(
            pool_arg,
            prompt=args["input"]["prompt"],
            trigger_source="dashboard",
            model="claude-sonnet-4-5-test",
            request_id=str(routed_request_id),
        )
        captured["session_id"] = session_id

        # Reproduce how the Spawner exposes routing context to the session so
        # conversation_reply recovers the request_id through the real path.
        token = set_current_runtime_session_id(str(session_id))
        set_runtime_session_routing_context(str(session_id), {"request_id": str(routed_request_id)})
        try:
            recovered = _best_effort_request_id()
            captured["recovered_request_id"] = recovered
            reply = await conversation_reply_create(
                pool_arg,
                UUID(str(thread_identity)),
                message="Recorded: dinner with Alice $80, split evenly — correct?",
                request_id=recovered,
            )
            captured["reply"] = reply
        finally:
            clear_runtime_session_routing_context(str(session_id))
            reset_current_runtime_session_id(token)

        # The routed session finishes with real accounting on its sessions row.
        await session_complete(
            pool_arg,
            session_id,
            output="Recorded: dinner with Alice $80, split evenly — correct?",
            tool_calls=[{"name": "conversation_reply"}],
            duration_ms=1234,
            success=True,
            input_tokens=150,
            output_tokens=42,
        )
        return {"status": "ok"}

    # The LLM dispatch fn must never be touched on the deterministic pin path.
    dispatch_fn = AsyncMock(side_effect=AssertionError("dispatch_fn should not be called"))

    with patch(_ROUTE_PATH, side_effect=mock_route):
        pipeline = MessagePipeline(switchboard_pool=pool, dispatch_fn=dispatch_fn)
        result = await pipeline.process(
            message_text=message_text,
            tool_args={
                "source_channel": "dashboard",
                "source_identity": envelope["source"]["endpoint_identity"],
                "request_id": str(ingest_request_id),
                "request_context": request_context,
            },
            message_inbox_id=ingest_request_id,
        )

    # --- Routing decision was real and deterministic -------------------------
    dispatch_fn.assert_not_called()
    assert result.target_butler == _TARGET_BUTLER
    assert result.acked_targets == [_TARGET_BUTLER]
    assert result.failed_targets == []
    assert result.routing_error is None

    # --- The pipeline handed the routed session the ORIGINAL ids -------------
    assert captured["route_request_id"] == str(ingest_request_id)
    assert captured["thread_identity"] == str(conversation_id)
    # request_id recovered through the real runtime-routing-context path.
    assert captured["recovered_request_id"] == ingest_request_id

    # --- dashboard_messages reply row carries the real request_id ------------
    reply_row = await pool.fetchrow(
        """
        SELECT conversation_id, role, request_id, session_id, content
        FROM public.dashboard_messages
        WHERE conversation_id = $1 AND role = 'assistant'
        """,
        conversation_id,
    )
    assert reply_row is not None, "no assistant reply row persisted"
    assert reply_row["request_id"] == ingest_request_id, "reply request_id is not the ingest id"
    assert reply_row["conversation_id"] == conversation_id

    # The SSE poller (message_find_reply_since) finds exactly this reply.
    polled = await message_find_reply_since(pool, conversation_id, since=user_msg["created_at"])
    assert polled is not None
    assert polled["request_id"] == ingest_request_id

    # --- sessions row carries the same request_id + real metadata ------------
    session_row = await pool.fetchrow(
        """
        SELECT id, request_id, model, input_tokens, output_tokens, success
        FROM sessions
        WHERE request_id = $1
        """,
        str(ingest_request_id),
    )
    assert session_row is not None, "no sessions row created for the routed request"
    assert session_row["id"] == captured["session_id"]
    assert session_row["request_id"] == str(ingest_request_id)
    assert session_row["model"] == "claude-sonnet-4-5-test"
    assert session_row["input_tokens"] == 150
    assert session_row["output_tokens"] == 42
    assert session_row["success"] is True

    # --- Lineage join: reply ↔ session ↔ ingest all share one request_id -----
    assert str(reply_row["request_id"]) == session_row["request_id"] == str(ingest_request_id)

    # --- message_inbox lifecycle reflects the successful bypass route --------
    inbox_state = await pool.fetchval(
        "SELECT lifecycle_state FROM switchboard.message_inbox WHERE id = $1", ingest_request_id
    )
    assert inbox_state == "parsed"


# ---------------------------------------------------------------------------
# Failure shape — spawn fails, status is honest, no orphaned reply
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_dashboard_message_spawn_failure_leaves_no_orphaned_lineage(pool):
    """When the spawn boundary fails, the failure is recorded honestly.

    No ``dashboard_messages`` assistant reply and no ``sessions`` row are left
    behind carrying the request_id — the pending user turn survives untouched,
    and ``message_inbox`` lands in ``errored`` rather than silently ``parsed``.
    """
    message_text = "Log: coffee $6 this morning."
    conversation_id, user_msg = await _seed_conversation_and_user_message(
        pool, message_text=message_text
    )

    envelope = build_dashboard_envelope(
        conversation_id=conversation_id,
        message_id=user_msg["id"],
        message_text=message_text,
        pinned_target=_TARGET_BUTLER,
    )
    with patch(_CLASSIFY_BUTLERS_PATH, new_callable=AsyncMock, return_value=_MOCK_BUTLERS):
        ingest_response = await ingest_v1(pool, envelope, enable_thread_affinity=False)
    assert ingest_response.status == "accepted"
    ingest_request_id: UUID = ingest_response.request_id
    request_context = await _read_request_context(pool, ingest_request_id)

    async def failing_route(
        pool_arg, *, target_butler, tool_name, args, source_butler="switchboard", **_kwargs
    ):
        # Simulate the target butler's Spawner failing to start / crashing —
        # nothing downstream (session row, reply) is written.
        raise RuntimeError("runtime spawn failed: butler runtime unavailable")

    dispatch_fn = AsyncMock(side_effect=AssertionError("dispatch_fn should not be called"))

    with patch(_ROUTE_PATH, side_effect=failing_route):
        pipeline = MessagePipeline(switchboard_pool=pool, dispatch_fn=dispatch_fn)
        result = await pipeline.process(
            message_text=message_text,
            tool_args={
                "source_channel": "dashboard",
                "source_identity": envelope["source"]["endpoint_identity"],
                "request_id": str(ingest_request_id),
                "request_context": request_context,
            },
            message_inbox_id=ingest_request_id,
        )

    # --- Failure surfaced honestly on the routing result ---------------------
    dispatch_fn.assert_not_called()
    assert result.target_butler == _TARGET_BUTLER
    assert result.acked_targets == []
    assert result.failed_targets == [_TARGET_BUTLER]
    assert result.routing_error is not None
    assert _TARGET_BUTLER in result.routing_error

    # --- No orphaned assistant reply; the user turn is untouched -------------
    role_counts = {
        r["role"]: r["n"]
        for r in await pool.fetch(
            """
            SELECT role, COUNT(*) AS n
            FROM public.dashboard_messages
            WHERE conversation_id = $1
            GROUP BY role
            """,
            conversation_id,
        )
    }
    assert role_counts.get("user") == 1, "the pending user message should survive"
    assert role_counts.get("assistant", 0) == 0, "no assistant reply should exist on failure"

    # No dashboard_messages row anywhere carries this request_id.
    orphan_replies = await pool.fetchval(
        "SELECT COUNT(*) FROM public.dashboard_messages WHERE request_id = $1",
        ingest_request_id,
    )
    assert orphan_replies == 0

    # --- No half-born session row for the failed spawn -----------------------
    session_count = await pool.fetchval(
        "SELECT COUNT(*) FROM sessions WHERE request_id = $1", str(ingest_request_id)
    )
    assert session_count == 0

    # --- message_inbox records the failure, not a false 'parsed' -------------
    inbox_state = await pool.fetchval(
        "SELECT lifecycle_state FROM switchboard.message_inbox WHERE id = $1", ingest_request_id
    )
    assert inbox_state == "errored"

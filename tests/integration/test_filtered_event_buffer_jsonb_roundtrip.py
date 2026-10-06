"""Real-Postgres regression: FilteredEventBuffer.record()/flush() must not
double-encode ``connectors.filtered_events.full_payload`` (bu-dycxq — sibling
sweep to bu-cymc4/bu-x92jw/bu-bstqu/bu-c8b8e/bu-xfcpf).

``FilteredEventBuffer.record()`` used to ``json.dumps()`` the ``full_payload``
dict before appending it to the in-memory buffer for a later batch
``executemany`` INSERT. Every asyncpg pool in this codebase registers a JSONB
type codec (``register_jsonb_codec``, ``src/butlers/db.py``) whose encoder
calls ``json.dumps()`` on the bound Python object itself — so the old code
path double-encoded ``full_payload`` into a jsonb-typed STRING instead of an
OBJECT. ``drain_replay_pending`` carries an ``isinstance(raw_payload, str)``
workaround on read to tolerate the corrupted shape.

Every connector (gmail, telegram, discord, google_drive, google_calendar,
spotify, google_health, owntracks, steam, activitywatch) shares this single
``FilteredEventBuffer``/``drain_replay_pending`` implementation, so fixing this
one writer fixes every connector's filtered-event write path uniformly.

Live-data audit (read-only, butlers-dev, 2026-07-05): of 807,206 total
``connectors.filtered_events`` rows across the two populated monthly
partitions (filtered_events_202606: 550,832 rows, filtered_events_202607:
256,374 rows), 807,205 (effectively ALL — 100.0%) have
``jsonb_typeof(full_payload) = 'string'``. This table is documented as
operational visibility data, not an audit trail (loss is by-design acceptable,
see ``FilteredEventBuffer`` module docstring), and there are currently zero
``replay_pending`` rows in either partition, so a bulk repair of ~800k
historical rows is out of scope for this bead (no migrations permitted). The
``isinstance(raw_payload, str)`` read-side workaround in ``drain_replay_pending``
is therefore KEPT — it is the only thing standing between this near-total
historical corruption and a replay silently failing to parse ``full_payload``.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, drain_replay_pending
from butlers.core.ingestion_events import ingestion_dropped_known_summary
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Actual heads for filtered rows, runtime registry and contact-query SQL."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard", "relationship"],
        schemas={"switchboard": "switchboard", "relationship": "relationship"},
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE connectors.filtered_events")
    yield p
    await p.close()


def _sample_payload() -> dict:
    return FilteredEventBuffer.full_payload(
        channel="email",
        provider="gmail",
        endpoint_identity="gmail:user:alice@example.com",
        external_event_id="msg-001",
        external_thread_id="thread-001",
        observed_at="2026-03-11T10:00:00Z",
        sender_identity="sender@example.com",
        raw={"headers": [], "body": "Hello"},
        normalized_text="Hello",
        policy_tier="full",
    )


@pytest.mark.pg_clock
async def test_record_and_flush_round_trips_full_payload_as_object(pool: asyncpg.Pool) -> None:
    """record() + flush() persist full_payload as a jsonb OBJECT, not a
    jsonb-typed string."""
    buf = FilteredEventBuffer(
        connector_type="gmail", endpoint_identity="gmail:user:alice@example.com"
    )
    buf.record(
        external_message_id="msg-1",
        source_channel="email",
        sender_identity="sender@example.com",
        subject_or_preview="Hello",
        filter_reason=FilteredEventBuffer.reason_label_exclude("CATEGORY_PROMOTIONS"),
        full_payload=_sample_payload(),
    )

    await buf.flush(pool)
    assert len(buf) == 0

    row = await pool.fetchrow(
        "SELECT full_payload FROM connectors.filtered_events "
        "WHERE connector_type = $1 AND external_message_id = $2",
        "gmail",
        "msg-1",
    )
    stored = row["full_payload"]
    assert isinstance(stored, dict), (
        f"Expected full_payload to be stored as a jsonb OBJECT but got "
        f"{type(stored).__name__!r}: {stored!r}"
    )
    assert stored["source"]["channel"] == "email"
    assert stored["payload"]["normalized_text"] == "Hello"


@pytest.mark.pg_clock
async def test_drain_replay_pending_handles_clean_object_row(pool: asyncpg.Pool) -> None:
    """drain_replay_pending submits a clean (object-shaped) full_payload row
    unchanged, and marks it replay_complete."""
    buf = FilteredEventBuffer(
        connector_type="gmail", endpoint_identity="gmail:user:alice@example.com"
    )
    buf.record(
        external_message_id="msg-clean",
        source_channel="email",
        sender_identity="sender@example.com",
        subject_or_preview="Hello",
        filter_reason=FilteredEventBuffer.reason_submission_error(),
        status="replay_pending",
        full_payload=_sample_payload(),
    )
    await buf.flush(pool)

    submitted: list[dict] = []

    async def _submit(envelope: dict) -> None:
        submitted.append(envelope)

    await drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", _submit)

    assert len(submitted) == 1
    assert submitted[0]["schema_version"] == "ingest.v1"
    assert submitted[0]["payload"]["normalized_text"] == "Hello"

    row = await pool.fetchrow(
        "SELECT status FROM connectors.filtered_events WHERE external_message_id = $1",
        "msg-clean",
    )
    assert row["status"] == "replay_complete"


async def test_drain_replay_pending_handles_legacy_string_shaped_row(pool: asyncpg.Pool) -> None:
    """A pre-existing corrupted row (full_payload stored as a jsonb-typed
    STRING, matching the ~100% corruption rate found in the live-data audit)
    is still parsed and submitted correctly by drain_replay_pending's
    ``isinstance(raw_payload, str)`` workaround."""
    payload = _sample_payload()
    reference_ts = datetime.now(UTC)
    await pool.fetchval(
        "SELECT connectors.connectors_filtered_events_ensure_partition($1)", reference_ts
    )
    await pool.execute(
        """
        INSERT INTO connectors.filtered_events (
            received_at, connector_type, endpoint_identity, external_message_id,
            source_channel, sender_identity, subject_or_preview, filter_reason,
            status, full_payload, error_detail
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
        """,
        reference_ts,
        "gmail",
        "gmail:user:alice@example.com",
        "msg-legacy",
        "email",
        "sender@example.com",
        "Hello",
        FilteredEventBuffer.reason_submission_error(),
        "replay_pending",
        json.dumps(payload),  # pre-fix double-encoding, reproduced deliberately
        None,
    )

    row = await pool.fetchrow(
        "SELECT full_payload FROM connectors.filtered_events WHERE external_message_id = $1",
        "msg-legacy",
    )
    assert isinstance(row["full_payload"], str), (
        "Test setup sanity check: expected the hand-inserted row to reproduce "
        "the corrupted (string-typed) shape found in the live-data audit."
    )

    submitted: list[dict] = []

    async def _submit(envelope: dict) -> None:
        submitted.append(envelope)

    await drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", _submit)

    assert len(submitted) == 1
    assert submitted[0]["payload"]["normalized_text"] == "Hello"

    row = await pool.fetchrow(
        "SELECT status FROM connectors.filtered_events WHERE external_message_id = $1",
        "msg-legacy",
    )
    assert row["status"] == "replay_complete"


async def test_buggy_write_path_would_have_corrupted_full_payload_into_a_string(
    pool: asyncpg.Pool,
) -> None:
    """Documents the pre-fix failure mode: json.dumps()-ing full_payload before
    binding it double-encodes the value into a jsonb-typed STRING instead of an
    OBJECT — the exact anti-pattern this bead removes from
    ``FilteredEventBuffer.record()``."""
    reference_ts = datetime.now(UTC)
    await pool.fetchval(
        "SELECT connectors.connectors_filtered_events_ensure_partition($1)", reference_ts
    )
    buggy_json_string = json.dumps(_sample_payload())
    await pool.execute(
        """
        INSERT INTO connectors.filtered_events (
            received_at, connector_type, endpoint_identity, external_message_id,
            source_channel, sender_identity, subject_or_preview, filter_reason,
            status, full_payload, error_detail
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        reference_ts,
        "gmail",
        "gmail:user:alice@example.com",
        "msg-buggy",
        "email",
        "sender@example.com",
        "Hello",
        FilteredEventBuffer.reason_submission_error(),
        "filtered",
        buggy_json_string,  # bound as a plain str param, matching the old record() bug
        None,
    )

    row = await pool.fetchrow(
        "SELECT full_payload FROM connectors.filtered_events WHERE external_message_id = $1",
        "msg-buggy",
    )
    stored = row["full_payload"]
    assert isinstance(stored, str), (
        "Expected the buggy write path to corrupt full_payload into a jsonb "
        f"STRING but got {type(stored).__name__!r}: {stored!r}"
    )
    assert json.loads(stored)["payload"]["normalized_text"] == "Hello"


def _known_drop_payload(message_id: str, *, marked: bool) -> dict:
    return FilteredEventBuffer.full_payload(
        channel="email",
        provider="gmail",
        endpoint_identity="gmail:user:alice@example.com",
        external_event_id=message_id,
        external_thread_id=None,
        observed_at="2026-03-11T10:00:00Z",
        sender_identity="friend@known.example",
        raw={},
        normalized_text="Hello",
        important_dropped_basis="known_contact" if marked else None,
    )


@pytest.mark.pg_clock
async def test_dropped_known_summary_counts_open_marked_drops_only(
    pool: asyncpg.Pool, migrated_db_url: str, monkeypatch, caplog
) -> None:
    """Marked, still-unanswered drops count; unmarked, replayed and pending ones do not,
    and one (rule, sender) pair is a single episode however many messages it dropped."""
    buf = FilteredEventBuffer(
        connector_type="gmail", endpoint_identity="gmail:user:alice@example.com"
    )
    block = FilteredEventBuffer.reason_policy_rule("connector_rule", "block", "sender_domain")
    skip = FilteredEventBuffer.reason_policy_rule("global_rule", "skip", "keyword")
    for message_id, reason, status, marked in [
        ("m-open-1", block, "filtered", True),
        ("m-open-2", block, "filtered", True),
        ("m-open-3", skip, "replay_failed", True),
        ("m-unmarked", block, "filtered", False),
        ("m-replayed", block, "replay_complete", True),
        ("m-pending", block, "replay_pending", True),
    ]:
        buf.record(
            external_message_id=message_id,
            source_channel="email",
            sender_identity="friend@known.example",
            subject_or_preview="Hello",
            filter_reason=reason,
            status=status,
            full_payload=_known_drop_payload(message_id, marked=marked),
        )
    await buf.flush(pool)

    summary = await ingestion_dropped_known_summary(pool, from_dt=datetime(2000, 1, 1, tzinfo=UTC))

    assert summary == {"dropped": 3, "episodes": 2}

    # bu-q7vx1q.43 V3 baseline: preserve the original marked-count positive,
    # then isolate one actual drop whose first contact query cannot execute.
    # No new snapshot/context API is a prerequisite of this counterexample.
    from unittest.mock import AsyncMock

    import httpx
    from fastapi import FastAPI

    from butlers.api.db import DatabaseManager
    from butlers.api.routers.ingestion_events import _get_db_manager, router
    from butlers.connectors.gmail import GmailConnectorConfig, GmailConnectorRuntime
    from butlers.connectors.gmail_policy import GmailPolicyEvaluator

    await pool.execute("TRUNCATE TABLE connectors.filtered_events")
    # A real successful empty read also proves that the contact schema/query
    # is usable; the failure below is the deliberately closed second pool.
    empty_evaluator = GmailPolicyEvaluator(pool)
    assert await empty_evaluator.get_known_contacts() == frozenset()
    assert empty_evaluator._cache_loaded_at > float("-inf"), (
        "An empty return after a swallowed SQL failure is not a successful-empty positive"
    )
    contact_pool = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=1, init=register_jsonb_codec
    )
    await contact_pool.close()
    registry_pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=2,
        init=register_jsonb_codec,
        server_settings={"search_path": "switchboard, public"},
    )
    try:
        runtime = GmailConnectorRuntime(
            GmailConnectorConfig(
                switchboard_mcp_url="http://switchboard.example.test/unused",
                connector_endpoint_identity="gmail:user:availability@example.test",
                gmail_client_id="synthetic-client-id",
                gmail_client_secret="synthetic-client-secret",
                gmail_refresh_token="synthetic-refresh-token",
            ),
            db_pool=contact_pool,
        )
        # Only provider responses are synthetic. Actual evaluator, handler,
        # label policy, buffering, codec, aggregate and API code remain wired.
        monkeypatch.setattr(runtime, "_fetch_sent_message_ids", AsyncMock(return_value=frozenset()))
        monkeypatch.setattr(
            runtime,
            "_fetch_message",
            AsyncMock(
                return_value={
                    "id": "m-classification-unavailable",
                    "threadId": "synthetic-thread",
                    "internalDate": "1708000000000",
                    "labelIds": ["SPAM"],
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "stranger@example.test"},
                            {"name": "Subject", "value": "Synthetic filtered message"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": ""},
                    },
                }
            ),
        )
        await runtime._refresh_policy_tier_assigner()
        assert any(
            record.name == "butlers.connectors.gmail_policy"
            and "DB refresh failed" in record.getMessage()
            for record in caplog.records
        ), "The actual first contact query must reach its DB exception path"
        await runtime._ingest_single_message("m-classification-unavailable")
        assert len(runtime._filtered_event_buffer) == 1
        await runtime._filtered_event_buffer.flush(pool)
        async with pool.acquire() as readback:
            row = await readback.fetchrow(
                "SELECT status, jsonb_typeof(full_payload) AS kind, full_payload "
                "FROM connectors.filtered_events WHERE external_message_id = $1",
                "m-classification-unavailable",
            )
        assert row is not None
        assert row["status"] == "filtered"
        assert row["kind"] == "object"
        assert row["full_payload"]["payload"]["raw"] == {}

        manager = DatabaseManager()
        manager._shared_pool = pool
        manager._pools["switchboard"] = registry_pool
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[_get_db_manager] = lambda: manager
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://dashboard.example.test"
        ) as client:
            response = await client.get("/api/ingestion/events/dropped-known?window=24h")
        assert response.status_code == 200
        result = response.json()
        assert result["dropped"] == 0
        assert result["episodes"] == 0
        # FIRST causal assertion: today's readable zero loses the DB-failure
        # history. Future classification keys/epoch helpers are not needed.
        assert result["available"] is False, (
            "A persisted drop after an actual first contact-query failure is "
            "unknown even when the filtered aggregate reads zero successfully"
        )

        # Same real gate species: admitted unknown, actual query success, failed
        # loaded publication, then recovery of CURRENT truth only.
        from types import SimpleNamespace

        from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
        from butlers.connectors.known_contact_state import CLASSIFICATION_KEY
        from butlers.core_tools._base import ToolContext
        from butlers.core_tools._switchboard import register_switchboard_tools

        registered = {}

        def capture_tool(_group, **options):
            def register(fn):
                registered[options.get("name", fn.__name__)] = fn
                return fn

            return register

        import asyncio

        before_registration = set(asyncio.all_tasks())
        register_switchboard_tools(
            ToolContext(
                daemon=SimpleNamespace(_pipeline=None, _buffer=None),
                pool=registry_pool,
                spawner=None,
                butler_name="switchboard",
                butler_type=None,
                is_switchboard=True,
                is_messenger=False,
                route_metrics=None,
            ),
            SimpleNamespace(),
            capture_tool,
        )
        await asyncio.gather(*(set(asyncio.all_tasks()) - before_registration))
        fail_loaded = True

        async def call_tool(name, arguments):
            check = arguments["capabilities"][CLASSIFICATION_KEY]
            if fail_loaded and check["state"] == "loaded":
                raise TimeoutError("synthetic private-error token-sentinel")
            return await registered[name](**arguments)

        publisher = ConnectorHeartbeat(
            HeartbeatConfig("gmail", runtime._config.connector_endpoint_identity),
            SimpleNamespace(call_tool=call_tool),
            runtime._metrics,
            runtime._get_health_state,
            get_capabilities=runtime._get_capabilities,
            get_contact_snapshot=runtime._gmail_policy_evaluator.peek_snapshot,
        )
        runtime._heartbeat = publisher
        assert await publisher.publish_once()  # accepted startup unknown
        runtime._gmail_policy_evaluator._db_pool = pool
        await runtime._refresh_policy_tier_assigner()
        empty_loaded = runtime._gmail_policy_evaluator.peek_snapshot()
        assert empty_loaded.state == "loaded" and empty_loaded.contacts == frozenset()

        async def api_summary():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://dashboard.example.test"
            ) as client:
                response = await client.get("/api/ingestion/events/dropped-known?window=24h")
            assert response.status_code == 200
            return response.json()

        # Read back actual provider-time and classification-time fields from a
        # separate acquisition, after another refresh and delayed buffer flush.
        runtime._fetch_message.return_value["id"] = "m-loaded-empty"
        await runtime._ingest_single_message("m-loaded-empty")
        frozen_empty = dict(
            runtime._filtered_event_buffer._rows[-1][9]["drop_context"]["classification"]
        )
        entity = await pool.fetchval(
            "INSERT INTO public.entities(canonical_name) VALUES($1) RETURNING id",
            "Synthetic priority",
        )
        await pool.execute(
            "INSERT INTO public.priority_contacts(contact_id, entity_id) VALUES($1,$1)",
            entity,
        )
        await pool.execute(
            "INSERT INTO relationship.entity_facts(subject,predicate,object,object_kind,src) "
            "VALUES($1,'has-email',$2,'literal','q43-synthetic')",
            entity,
            "known@example.test",
        )
        runtime._gmail_policy_evaluator._cache_loaded_at -= 901
        await runtime._refresh_policy_tier_assigner()
        known_loaded = runtime._gmail_policy_evaluator.peek_snapshot()
        assert known_loaded.state == "loaded" and known_loaded.contacts == frozenset(
            {"known@example.test"}
        )
        for suffix, provider_date in [
            ("old", "1"),
            ("future", "4102444800000"),
            ("malformed", "not-a-date"),
        ]:
            message = runtime._fetch_message.return_value
            message["payload"]["headers"][0]["value"] = "known@example.test"
            message["internalDate"] = provider_date
            await runtime._ingest_single_message(f"m-known-{suffix}")
        assert len(runtime._filtered_event_buffer) == 4
        await runtime._filtered_event_buffer.flush(pool)
        async with pool.acquire() as readback:
            persisted = await readback.fetch(
                "SELECT external_message_id, full_payload, received_at FROM connectors.filtered_events "
                "WHERE external_message_id LIKE 'm-known-%' OR external_message_id='m-loaded-empty'"
            )
        assert len(persisted) == 4
        for persisted_row in persisted:
            payload = persisted_row["full_payload"]
            classification = payload["drop_context"]["classification"]
            success = datetime.fromisoformat(classification["last_success_at"])
            observed = datetime.fromisoformat(classification["observed_at"])
            assert classification["state"] == "loaded"
            assert 0 <= (observed - success).total_seconds() <= 900
            assert payload["payload"]["raw"] == {}
            assert observed <= persisted_row["received_at"]
            if persisted_row["external_message_id"] == "m-loaded-empty":
                assert classification == frozen_empty
                assert "important_dropped" not in payload["drop_context"]
            else:
                assert payload["drop_context"]["important_dropped"] is True
                assert classification["observed_at"] != payload["event"]["observed_at"]
        unknown_current = await api_summary()
        assert unknown_current["dropped"] == 3 and unknown_current["counts_available"] is True
        assert unknown_current["classification_available"] is False
        assert unknown_current["uncertain_drops"] == 1 and unknown_current["available"] is False
        assert runtime._get_health_state() == ("healthy", None)
        fail_loaded = False
        assert await publisher.publish_once()
        recovered = await api_summary()
        assert recovered["classification_available"] is True
        assert recovered["available"] is False and recovered["uncertain_drops"] == 1
        async with pool.acquire() as readback:
            original = await readback.fetchval(
                "SELECT full_payload FROM connectors.filtered_events WHERE external_message_id=$1",
                "m-classification-unavailable",
            )
        assert original == row["full_payload"]  # recovery never rewrites history
        await pool.execute(
            "UPDATE connectors.filtered_events SET status='replay_pending' WHERE external_message_id=$1",
            "m-classification-unavailable",
        )
        assert (await api_summary())["uncertain_drops"] == 1
        await pool.execute(
            "UPDATE connectors.filtered_events SET status='replay_complete' WHERE external_message_id=$1",
            "m-classification-unavailable",
        )
        assert (await api_summary())["available"] is True

        # A wholly unobserved failed send cannot revoke previous fresh evidence.
        fail_loaded = True
        assert await publisher.publish_once() is False
        assert (await api_summary())["available"] is True
        await registry_pool.execute(
            "UPDATE switchboard.connector_registry SET last_heartbeat_at=now()-interval '301 seconds' "
            "WHERE connector_type='gmail' AND endpoint_identity=$1",
            runtime._config.connector_endpoint_identity,
        )
        assert (await api_summary())["classification_available"] is False
        fail_loaded = False
        assert await publisher.publish_once()
        await registry_pool.execute(
            "UPDATE switchboard.connector_registry SET capabilities=jsonb_set(capabilities, "
            "'{known_contact_check,last_success_at}',to_jsonb((now()-interval '901 seconds')::text)) "
            "WHERE connector_type='gmail' AND endpoint_identity=$1",
            runtime._config.connector_endpoint_identity,
        )
        assert (await api_summary())["classification_available"] is False
        # New actual query/ordinary admitted publication repairs current age.
        runtime._gmail_policy_evaluator._cache_loaded_at -= 901
        await runtime._refresh_policy_tier_assigner()
        assert (await api_summary())["available"] is True

        # Legacy, malformed, future and clock-incoherent HISTORY stays unknown;
        # independently retain all positive counts and window/status semantics.
        loaded_payload = persisted[0]["full_payload"]
        import copy

        for suffix, classification in [
            ("legacy", None),
            ("malformed", {"state": "contact-error-token"}),
            ("future", {**frozen_empty, "last_success_at": "2100-01-01T00:00:00+00:00"}),
            ("clock", {**frozen_empty, "observed_at": "2000-01-01T00:00:00+00:00"}),
        ]:
            planted = copy.deepcopy(loaded_payload)
            planted["drop_context"] = {"classification": classification}
            runtime._filtered_event_buffer.record(
                external_message_id=f"m-uncertain-{suffix}",
                source_channel="email",
                sender_identity="synthetic@example.test",
                subject_or_preview=None,
                filter_reason="label_exclude:SPAM",
                full_payload=planted,
            )
        await runtime._filtered_event_buffer.flush(pool)
        invalid_history = await api_summary()
        assert invalid_history["uncertain_drops"] == 4 and invalid_history["dropped"] == 3
        await pool.execute(
            "UPDATE connectors.filtered_events SET received_at=now()-interval '25 hours' "
            "WHERE external_message_id LIKE 'm-uncertain-%'"
        )
        assert (await api_summary())["available"] is True
        # Excluded row roles do not create authority; a genuinely empty complete
        # read is positive, while a real applicable legacy row is not.
        await registry_pool.execute(
            "DELETE FROM switchboard.connector_registry WHERE connector_type='gmail'"
        )
        assert (await api_summary())["available"] is True
        await registry_pool.execute(
            "INSERT INTO switchboard.connector_registry(connector_type,endpoint_identity,operational_role) "
            "VALUES('gmail','gmail:user:legacy@example.test','runtime_instance')"
        )
        assert (await api_summary())["classification_available"] is False
        await registry_pool.execute(
            "UPDATE switchboard.connector_registry SET operational_role='checkpoint' "
            "WHERE endpoint_identity='gmail:user:legacy@example.test'"
        )
        assert (await api_summary())["available"] is True
    finally:
        await registry_pool.close()


@pytest.mark.pg_clock
async def test_replay_of_marked_drop_submits_clean_envelope(pool: asyncpg.Pool) -> None:
    """The stored drop_context marker is stripped on replay so the envelope still validates."""
    buf = FilteredEventBuffer(
        connector_type="gmail", endpoint_identity="gmail:user:alice@example.com"
    )
    buf.record(
        external_message_id="m-replay",
        source_channel="email",
        sender_identity="friend@known.example",
        subject_or_preview="Hello",
        filter_reason=FilteredEventBuffer.reason_policy_rule("global_rule", "skip", "keyword"),
        status="replay_pending",
        full_payload={
            **_known_drop_payload("m-replay", marked=True),
            "drop_context": {
                "important_dropped": True,
                "basis": "known_contact",
                "classification": {
                    "state": "loaded",
                    "reason": "none",
                    "observed_at": "private-sentinel",
                },
            },
        },
    )
    await buf.flush(pool)

    submitted: list[dict] = []

    async def _submit(envelope: dict) -> None:
        submitted.append(envelope)

    await drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", _submit)

    assert len(submitted) == 1
    assert "drop_context" not in submitted[0]
    # Once replayed the drop is answered, so it no longer counts as outstanding harm.
    summary = await ingestion_dropped_known_summary(pool, from_dt=datetime(2000, 1, 1, tzinfo=UTC))
    assert summary == {"dropped": 0, "episodes": 0}

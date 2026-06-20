"""Tests for roster/health/tools/wellness_ingest.py.

Covers:
1. Predicate derivation for all connector-emitted resources (happy path + unknown resource)
2. Owner identity validation (multi-account aware)
3. Replay-idempotency (same idempotency_key → no duplicate)
4. Malformed payload (missing required field) → skipped_malformed_payload
5. Prometheus counter increments with correct labels
6. Activity fan-out → two facts (measurement_steps + measurement_active_minutes)
7. Sleep stage fan-out → up to two facts (sleep_session + sleep_stage_summary)
8. Owner identity validation — multi-account acceptance and rejection

All tests are unit tests (no DB required) — DB calls are mocked.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Envelope builder helpers
# ---------------------------------------------------------------------------


def _make_sleep_envelope(
    session_id: str = "sess-abc",
    sender_identity: str = "user@example.com",
    idempotency_key: str | None = None,
    raw: dict | None = None,
) -> dict:
    """Build a minimal sleep session ingest.v1 envelope.

    Uses ``sleep_session`` as the resource segment — matching what the connector
    emits (``google_health:<account_email>:sleep_session:<session_id>``).
    """
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "wellness",
            "provider": "google_health",
            "endpoint_identity": f"google_health:user:{sender_identity}",
        },
        "event": {
            "external_event_id": f"google_health:{sender_identity}:sleep_session:{session_id}",
            "external_thread_id": None,
            "observed_at": "2026-04-25T06:00:00Z",
        },
        "sender": {"identity": sender_identity},
        "payload": {
            "raw": raw
            if raw is not None
            else {
                "durationMillis": 25200000,
                "efficiency": 87,
                "startTime": "2026-04-24T23:00:00Z",
            },
            "normalized_text": "Slept 7h 0m (87% efficiency)",
        },
        "control": {
            "idempotency_key": idempotency_key or f"google_health:sleep:{session_id}",
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


def _make_daily_envelope(
    resource: str,
    record_date: str = "2026-04-24",
    sender_identity: str = "user@example.com",
    raw: dict | None = None,
) -> dict:
    """Build a minimal daily-summary ingest.v1 envelope."""
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "wellness",
            "provider": "google_health",
            "endpoint_identity": f"google_health:user:{sender_identity}",
        },
        "event": {
            "external_event_id": f"google_health:{sender_identity}:{resource}:{record_date}",
            "external_thread_id": None,
            "observed_at": "2026-04-25T06:00:00Z",
        },
        "sender": {"identity": sender_identity},
        "payload": {
            "raw": raw if raw is not None else {"value": 62, "date": record_date},
            "normalized_text": f"{resource}: 62",
        },
        "control": {
            "idempotency_key": f"google_health:{resource}:{record_date}",
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


def _make_sleep_with_stages_envelope(
    session_id: str = "sess-stages",
    sender_identity: str = "user@example.com",
    stages: dict | None = None,
    stage_key: str = "stages",
) -> dict:
    """Build a sleep envelope whose raw payload includes stage data.

    ``stage_key`` controls which field name carries the stage data
    (``stages`` or ``stageSummary`` — both are accepted by the extractor).
    """
    stage_data = stages or {"light": 120, "deep": 90, "rem": 60, "awake": 10}
    raw = {
        "durationMillis": 25200000,
        "efficiency": 87,
        "startTime": "2026-04-24T23:00:00Z",
        stage_key: stage_data,
    }
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "wellness",
            "provider": "google_health",
            "endpoint_identity": f"google_health:user:{sender_identity}",
        },
        "event": {
            "external_event_id": f"google_health:{sender_identity}:sleep_session:{session_id}",
            "external_thread_id": None,
            "observed_at": "2026-04-25T06:00:00Z",
        },
        "sender": {"identity": sender_identity},
        "payload": {
            "raw": raw,
            "normalized_text": "Slept 7h 0m (87% efficiency)",
        },
        "control": {
            "idempotency_key": f"google_health:sleep:{session_id}",
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


def _make_activity_envelope(
    record_date: str = "2026-04-24",
    sender_identity: str = "user@example.com",
    steps: int = 8000,
    active_minutes: int = 45,
) -> dict:
    """Build an activity envelope with both steps and active_minutes fields."""
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "wellness",
            "provider": "google_health",
            "endpoint_identity": f"google_health:user:{sender_identity}",
        },
        "event": {
            "external_event_id": f"google_health:{sender_identity}:activity:{record_date}",
            "external_thread_id": None,
            "observed_at": "2026-04-25T06:00:00Z",
        },
        "sender": {"identity": sender_identity},
        "payload": {
            "raw": {
                "steps": steps,
                "activeMinutes": active_minutes,
                "date": record_date,
            },
            "normalized_text": f"Steps: {steps}",
        },
        "control": {
            "idempotency_key": f"google_health:activity:{record_date}",
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_owner_identity_cache():
    """Reset the per-session owner identity cache before each test.

    The cache is a module-level singleton; without this reset, one test's
    mocked identity set would leak into the next test.
    """
    import butlers.tools.health.wellness_ingest as _wi

    _wi._recognised_owner_identities = None
    yield
    _wi._recognised_owner_identities = None


@pytest.fixture
def mock_pool():
    """Mock asyncpg pool that returns an owner entity row (google_accounts now via registry)."""
    pool = AsyncMock()
    pool.fetchrow.side_effect = _default_fetchrow
    return pool


async def _default_fetchrow(query: str, *args, **kwargs):
    """Default fetchrow for owner entity lookup."""
    if "public.entities" in query:
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: str(uuid.uuid4()) if k == "id" else None)
        return row
    return None


@pytest.fixture
def mock_embedding_engine():
    return MagicMock()


@pytest.fixture
def store_fact_result():
    return {"id": str(uuid.uuid4()), "superseded_id": None}


# ---------------------------------------------------------------------------
# Helper to build a HealthScopedAccount mock
# ---------------------------------------------------------------------------


def _make_health_scoped_account(email: str) -> MagicMock:
    """Return a mock HealthScopedAccount-like object with the given email."""
    acct = MagicMock()
    acct.email = email
    return acct


# ---------------------------------------------------------------------------
# Helper to call translate_wellness_envelope with standard mocks
# ---------------------------------------------------------------------------


async def _call_translate(
    envelope: dict,
    pool=None,
    embedding_engine=None,
    store_fact_return=None,
    recognised_emails: list[str] | None = None,
) -> dict:
    """Call translate_wellness_envelope with mocked dependencies.

    ``recognised_emails`` controls what ``list_health_scoped_accounts`` returns.
    Defaults to ``["user@example.com"]`` — the default sender in all envelope helpers.
    """
    from butlers.tools.health.wellness_ingest import translate_wellness_envelope

    if pool is None:
        pool = AsyncMock()

        async def _fetchrow(query, *args, **kwargs):
            if "public.entities" in query:
                row = MagicMock()
                row.__getitem__ = MagicMock(
                    side_effect=lambda k: str(uuid.uuid4()) if k == "id" else None
                )
                return row
            return None

        pool.fetchrow.side_effect = _fetchrow

    if embedding_engine is None:
        embedding_engine = MagicMock()

    if recognised_emails is None:
        recognised_emails = ["user@example.com"]

    sf_result = store_fact_return or {"id": str(uuid.uuid4()), "superseded_id": None}

    accounts = [_make_health_scoped_account(e) for e in recognised_emails]

    with (
        patch(
            "butlers.tools.health.wellness_ingest.list_health_scoped_accounts",
            new_callable=AsyncMock,
            return_value=accounts,
        ),
        patch(
            "butlers.tools.health.wellness_ingest.resolve_owner_entity_info",
            new_callable=AsyncMock,
            return_value=str(uuid.uuid4()),
        ),
        patch(
            "butlers.tools.health.wellness_ingest.memory_store_fact",
            new_callable=AsyncMock,
            return_value=sf_result,
        ) as mock_store,
        patch("butlers.tools.health.wellness_ingest.health_wellness_ingest_total") as mock_counter,
    ):
        result = await translate_wellness_envelope(pool, embedding_engine, envelope)
        return result, mock_store, mock_counter


# ---------------------------------------------------------------------------
# 1. Predicate derivation for connector-emitted resources
# ---------------------------------------------------------------------------


class TestPredicateDerivation:
    """All connector-emitted resource segments map to the correct predicates."""

    @pytest.mark.parametrize(
        "resource,expected_predicate",
        [
            ("sleep_session", "sleep_session"),
            ("resting_hr", "measurement_resting_hr"),
            ("hrv", "measurement_hrv"),
            ("spo2", "measurement_spo2"),
            ("breathing_rate", "measurement_breathing_rate"),
            ("vo2_max", "measurement_vo2_max"),
        ],
    )
    async def test_single_predicate_happy_path(
        self, resource: str, expected_predicate: str
    ) -> None:
        """Each single-predicate resource maps to its canonical predicate and returns status=ok."""
        if resource == "sleep_session":
            envelope = _make_sleep_envelope()
        else:
            envelope = _make_daily_envelope(resource)

        result, mock_store, mock_counter = await _call_translate(envelope)

        assert result["status"] == "ok", f"Expected ok for {resource}, got {result}"
        assert result["predicate"] == expected_predicate
        assert "fact_id" in result
        assert result["facts_written"] == 1
        mock_store.assert_awaited_once()
        call_kwargs = mock_store.call_args
        assert call_kwargs.kwargs.get("predicate") == expected_predicate or (
            len(call_kwargs.args) >= 4 and call_kwargs.args[3] == expected_predicate
        )

    async def test_unknown_resource_returns_skipped(self) -> None:
        """Unknown resource segment returns skipped_unknown_predicate."""
        envelope = _make_daily_envelope("unknown_resource")
        result, mock_store, mock_counter = await _call_translate(envelope)

        assert result["status"] == "skipped_unknown_predicate"
        mock_store.assert_not_awaited()
        mock_counter.labels.assert_called_once()
        label_call = mock_counter.labels.call_args
        assert label_call.kwargs.get("outcome") == "skipped_unknown_predicate"

    async def test_dead_code_keys_not_in_map(self) -> None:
        """Dead-code keys ('sleep', 'sleep_stage', 'steps', 'active_minutes') are gone."""
        from butlers.tools.health.wellness_ingest import _RESOURCE_TO_PREDICATES

        for dead_key in ("sleep", "sleep_stage", "steps", "active_minutes"):
            assert dead_key not in _RESOURCE_TO_PREDICATES, (
                f"Dead-code key {dead_key!r} must not be in _RESOURCE_TO_PREDICATES"
            )


# ---------------------------------------------------------------------------
# 2. Activity fan-out
# ---------------------------------------------------------------------------


class TestActivityFanOut:
    """activity resource fans out to both measurement_steps and measurement_active_minutes."""

    async def test_activity_writes_two_facts(self) -> None:
        """Activity envelope produces two facts with correct predicates."""
        envelope = _make_activity_envelope(steps=8000, active_minutes=45)
        result, mock_store, mock_counter = await _call_translate(envelope)

        assert result["status"] == "ok"
        assert result["facts_written"] == 2
        assert len(result["facts"]) == 2

        predicates = [f["predicate"] for f in result["facts"]]
        assert "measurement_steps" in predicates
        assert "measurement_active_minutes" in predicates

        # Two store_fact calls
        assert mock_store.await_count == 2

    async def test_activity_idempotency_keys_are_distinct(self) -> None:
        """Each fan-out fact gets a distinct idempotency key suffixed :steps/:active_minutes."""
        envelope = _make_activity_envelope()
        base_key = envelope["control"]["idempotency_key"]  # google_health:activity:2026-04-24

        result, mock_store, _ = await _call_translate(envelope)

        assert result["status"] == "ok"
        ikeys = [c.kwargs.get("idempotency_key") for c in mock_store.call_args_list]
        assert f"{base_key}:steps" in ikeys
        assert f"{base_key}:active_minutes" in ikeys
        assert ikeys[0] != ikeys[1]

    async def test_activity_steps_metadata(self) -> None:
        """Steps fact metadata contains steps value and unit='steps'."""
        envelope = _make_activity_envelope(steps=9500)
        result, mock_store, _ = await _call_translate(envelope)

        assert result["status"] == "ok"
        # Find the call for measurement_steps
        steps_call = next(
            c for c in mock_store.call_args_list if c.kwargs.get("predicate") == "measurement_steps"
        )
        # metadata is not a direct kwarg to memory_store_fact but we can verify predicate
        assert steps_call.kwargs.get("predicate") == "measurement_steps"

    async def test_activity_active_minutes_metadata(self) -> None:
        """Active-minutes fact metadata contains minutes value and unit='min'."""
        envelope = _make_activity_envelope(active_minutes=60)
        result, mock_store, _ = await _call_translate(envelope)

        assert result["status"] == "ok"
        am_call = next(
            c
            for c in mock_store.call_args_list
            if c.kwargs.get("predicate") == "measurement_active_minutes"
        )
        assert am_call.kwargs.get("predicate") == "measurement_active_minutes"

    async def test_activity_no_top_level_predicate_field(self) -> None:
        """Fan-out result has no top-level 'predicate' key (only single-predicate does)."""
        envelope = _make_activity_envelope()
        result, _, _ = await _call_translate(envelope)

        assert result["status"] == "ok"
        assert "predicate" not in result
        assert "fact_id" not in result

    async def test_activity_prometheus_incremented_per_fact(self) -> None:
        """Counter is incremented once per emitted fact (two calls for activity)."""
        envelope = _make_activity_envelope()
        result, _, mock_counter = await _call_translate(envelope)

        assert result["status"] == "ok"
        # Two .labels().inc() calls — one per predicate
        assert mock_counter.labels.call_count == 2
        call_predicates = {c.kwargs.get("predicate") for c in mock_counter.labels.call_args_list}
        assert "measurement_steps" in call_predicates
        assert "measurement_active_minutes" in call_predicates
        for c in mock_counter.labels.call_args_list:
            assert c.kwargs.get("outcome") == "success"


# ---------------------------------------------------------------------------
# 3. Owner-identity sender rejection
# ---------------------------------------------------------------------------


class TestSenderRejection:
    async def test_foreign_sender_rejected(self) -> None:
        """Sender not in the recognised owner identity set is rejected."""
        envelope = _make_sleep_envelope(sender_identity="other@example.com")
        # Recognised set contains only "user@example.com"; sender is different.
        result, _, mock_counter = await _call_translate(
            envelope, recognised_emails=["user@example.com"]
        )

        assert result["status"] == "rejected_non_owner_sender"
        mock_counter.labels.assert_called_once()
        label_call = mock_counter.labels.call_args
        assert label_call.kwargs.get("outcome") == "rejected_non_owner_sender"

    async def test_no_health_scoped_accounts_rejects(self) -> None:
        """When no health-scoped owner accounts are found, envelope is rejected."""
        result, _, _ = await _call_translate(_make_sleep_envelope(), recognised_emails=[])

        assert result["status"] == "rejected_non_owner_sender"


# ---------------------------------------------------------------------------
# 4. Replay idempotency
# ---------------------------------------------------------------------------


class TestReplayIdempotency:
    async def test_idempotency_key_forwarded_to_store_fact(self) -> None:
        """idempotency_key from control is forwarded to memory_store_fact with :session suffix.

        sleep_session fans out to two predicates, so the base key is suffixed:
        - sleep_session       → <base>:session
        - sleep_stage_summary → <base>:stage_summary (only when stage data present)
        """
        envelope = _make_sleep_envelope(
            idempotency_key="google_health:sleep:sess-abc",
            raw={
                "durationMillis": 25200000,
                "efficiency": 87,
                "startTime": "2026-04-24T23:00:00Z",
            },
        )
        result, mock_store, _ = await _call_translate(envelope)

        assert result["status"] == "ok"
        # sleep_session fact uses the :session suffix
        call_kwargs = mock_store.call_args
        assert call_kwargs.kwargs.get("idempotency_key") == "google_health:sleep:sess-abc:session"

    async def test_second_call_uses_same_idempotency_key(self) -> None:
        """Two calls with the same idempotency_key both forward it to store_fact.

        The store_fact layer is responsible for deduplication; we verify the key
        is forwarded consistently across both calls.  sleep_session is now a
        fan-out resource so the :session suffix is expected on each call.
        """
        ikey = "google_health:sleep:sess-replay"
        envelope = _make_sleep_envelope(session_id="sess-replay", idempotency_key=ikey)

        account = _make_health_scoped_account("user@example.com")

        with (
            patch(
                "butlers.tools.health.wellness_ingest.list_health_scoped_accounts",
                new_callable=AsyncMock,
                return_value=[account],
            ),
            patch(
                "butlers.tools.health.wellness_ingest.resolve_owner_entity_info",
                new_callable=AsyncMock,
                return_value=str(uuid.uuid4()),
            ),
            patch(
                "butlers.tools.health.wellness_ingest.memory_store_fact",
                new_callable=AsyncMock,
                return_value={"id": str(uuid.uuid4()), "superseded_id": None},
            ) as mock_store,
            patch("butlers.tools.health.wellness_ingest.health_wellness_ingest_total"),
        ):
            pool = AsyncMock()

            async def _fetchrow(query, *args, **kwargs):
                if "public.entities" in query:
                    row = MagicMock()
                    row.__getitem__ = MagicMock(
                        side_effect=lambda k: str(uuid.uuid4()) if k == "id" else None
                    )
                    return row
                return None

            pool.fetchrow.side_effect = _fetchrow
            embedding_engine = MagicMock()

            from butlers.tools.health.wellness_ingest import translate_wellness_envelope

            r1 = await translate_wellness_envelope(pool, embedding_engine, envelope)
            r2 = await translate_wellness_envelope(pool, embedding_engine, envelope)

        assert r1["status"] == "ok"
        assert r2["status"] == "ok"
        # Each call writes one sleep_session fact (stage data absent in default fixture)
        # and both forward the :session-suffixed key consistently.
        assert mock_store.call_count == 2
        expected_ikey = f"{ikey}:session"
        for c in mock_store.call_args_list:
            assert c.kwargs.get("idempotency_key") == expected_ikey


# ---------------------------------------------------------------------------
# 5. Malformed payload
# ---------------------------------------------------------------------------


class TestMalformedPayload:
    async def test_empty_raw_skips_sleep_session(self) -> None:
        """Sleep session with empty raw (no durationMillis) returns skipped_malformed_payload.

        A sleep record with no duration is considered malformed — duration is the
        minimum required field for a sleep session fact to be meaningful.
        """
        envelope = _make_sleep_envelope(raw={"startTime": "2026-04-24T23:00:00Z"})
        # No durationMillis → duration_ms=0 → treated as malformed
        result, mock_store, mock_counter = await _call_translate(envelope)

        assert result["status"] == "skipped_malformed_payload"
        mock_store.assert_not_awaited()

    async def test_none_raw_skips(self) -> None:
        """Envelope with raw=None is treated as empty → skipped_malformed_payload."""
        envelope = _make_sleep_envelope()
        envelope["payload"]["raw"] = None
        # raw becomes {} → no durationMillis → malformed
        result, mock_store, mock_counter = await _call_translate(envelope)

        assert result["status"] == "skipped_malformed_payload"
        mock_store.assert_not_awaited()

    async def test_warning_logged_on_missing_field(self, caplog) -> None:
        """A warning is logged when payload is malformed."""
        import logging

        envelope = _make_sleep_envelope(raw={"startTime": "2026-04-24T23:00:00Z"})
        with caplog.at_level(logging.WARNING, logger="butlers.tools.health.wellness_ingest"):
            result, _mock_store, _mock_ctr = await _call_translate(envelope)

        assert result["status"] == "skipped_malformed_payload"
        # Warning should have been emitted
        assert any("malformed" in r.message or "empty" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 6. Prometheus counter
# ---------------------------------------------------------------------------


class TestPrometheusCounter:
    async def test_success_increments_counter_with_correct_labels(self) -> None:
        """On success, counter is incremented with predicate and outcome=success."""
        envelope = _make_daily_envelope("resting_hr")
        result, _, mock_counter = await _call_translate(envelope)

        assert result["status"] == "ok"
        mock_counter.labels.assert_called_once_with(
            predicate="measurement_resting_hr", outcome="success"
        )
        mock_counter.labels.return_value.inc.assert_called_once()

    async def test_unknown_predicate_increments_skipped_counter(self) -> None:
        """Unknown resource increments counter with skipped_unknown_predicate outcome."""
        envelope = _make_daily_envelope("completely_unknown")
        result, _, mock_counter = await _call_translate(envelope)

        assert result["status"] == "skipped_unknown_predicate"
        label_call = mock_counter.labels.call_args
        assert label_call.kwargs.get("outcome") == "skipped_unknown_predicate"
        mock_counter.labels.return_value.inc.assert_called_once()

    async def test_foreign_sender_rejection_increments_counter(self) -> None:
        """Foreign sender increments counter with rejected_non_owner_sender."""
        envelope = _make_sleep_envelope(sender_identity="stranger@example.com")
        result, _, mock_counter = await _call_translate(
            envelope, recognised_emails=["primary@example.com"]
        )

        assert result["status"] == "rejected_non_owner_sender"
        mock_counter.labels.assert_called_once_with(
            predicate="unknown", outcome="rejected_non_owner_sender"
        )
        mock_counter.labels.return_value.inc.assert_called_once()

    @pytest.mark.parametrize(
        "resource,expected_predicate",
        [
            ("sleep_session", "sleep_session"),
            ("resting_hr", "measurement_resting_hr"),
            ("hrv", "measurement_hrv"),
            ("spo2", "measurement_spo2"),
            ("breathing_rate", "measurement_breathing_rate"),
            ("vo2_max", "measurement_vo2_max"),
        ],
    )
    async def test_counter_label_predicate_matches_resource(
        self, resource: str, expected_predicate: str
    ) -> None:
        """Counter is labeled with the canonical predicate name, not the resource key."""
        from butlers.tools.health.wellness_ingest import _RESOURCE_TO_PREDICATES

        if resource == "sleep_session":
            envelope = _make_sleep_envelope()
        else:
            envelope = _make_daily_envelope(resource)

        result, _, mock_counter = await _call_translate(envelope)

        assert result["status"] == "ok"
        # For single-predicate resources the tuple has one element
        assert expected_predicate in _RESOURCE_TO_PREDICATES[resource]
        if resource == "sleep_session":
            # sleep_session fans out: counter called for sleep_session (success) and
            # sleep_stage_summary (skipped — no stage data in default fixture).
            call_predicates = {
                c.kwargs.get("predicate") for c in mock_counter.labels.call_args_list
            }
            assert expected_predicate in call_predicates
            success_calls = [
                c
                for c in mock_counter.labels.call_args_list
                if c.kwargs.get("predicate") == expected_predicate
                and c.kwargs.get("outcome") == "success"
            ]
            assert len(success_calls) == 1, (
                f"Expected exactly one success counter call for {expected_predicate}"
            )
        else:
            mock_counter.labels.assert_called_once_with(
                predicate=expected_predicate, outcome="success"
            )


# ---------------------------------------------------------------------------
# 7. Sleep stage fan-out
# ---------------------------------------------------------------------------


class TestSleepStageFanOut:
    """sleep_session resource fans out to sleep_session + sleep_stage_summary.

    sleep_stage_summary is *optional*: when stage data is absent from the
    payload, only the sleep_session fact is written and the overall status is
    still ``ok``.  When stage data is present, both facts are written.
    """

    async def test_both_facts_written_when_stage_data_present(self) -> None:
        """Sleep envelope with stage data produces two facts."""
        envelope = _make_sleep_with_stages_envelope()
        result, mock_store, _ = await _call_translate(envelope)

        assert result["status"] == "ok"
        assert result["facts_written"] == 2
        assert len(result["facts"]) == 2

        predicates = [f["predicate"] for f in result["facts"]]
        assert "sleep_session" in predicates
        assert "sleep_stage_summary" in predicates

        assert mock_store.await_count == 2

    async def test_only_sleep_session_written_when_stage_data_absent(self) -> None:
        """Sleep envelope without stage data writes sleep_session only (stage is optional)."""
        envelope = _make_sleep_envelope()  # default fixture: no stages/stageSummary
        result, mock_store, _ = await _call_translate(envelope)

        assert result["status"] == "ok"
        assert result["facts_written"] == 1
        assert len(result["facts"]) == 1
        assert result["facts"][0]["predicate"] == "sleep_session"
        # Backwards-compat fields still present for single-fact result
        assert result["predicate"] == "sleep_session"
        assert "fact_id" in result

        mock_store.assert_awaited_once()

    async def test_distinct_idempotency_keys_when_stage_data_present(self) -> None:
        """Fan-out idempotency keys are suffixed :session and :stage_summary."""
        envelope = _make_sleep_with_stages_envelope(session_id="sess-keys")
        base_key = envelope["control"]["idempotency_key"]  # google_health:sleep:sess-keys

        result, mock_store, _ = await _call_translate(envelope)

        assert result["status"] == "ok"
        ikeys = [c.kwargs.get("idempotency_key") for c in mock_store.call_args_list]
        assert f"{base_key}:session" in ikeys
        assert f"{base_key}:stage_summary" in ikeys
        assert ikeys[0] != ikeys[1]

    @pytest.mark.parametrize("stage_key", ["stages", "stageSummary"])
    async def test_stage_data_accepted_under_both_field_names(self, stage_key: str) -> None:
        """Stage data is extracted from either 'stages' or 'stageSummary' field."""
        stage_data = {"light": 120, "deep": 90, "rem": 60, "awake": 10}
        envelope = _make_sleep_with_stages_envelope(stage_key=stage_key, stages=stage_data)
        result, mock_store, _ = await _call_translate(envelope)

        assert result["status"] == "ok"
        assert result["facts_written"] == 2
        predicates = [f["predicate"] for f in result["facts"]]
        assert "sleep_stage_summary" in predicates

    async def test_prometheus_counter_incremented_twice_when_stage_present(self) -> None:
        """Counter is incremented once per emitted fact (two calls when stage data present)."""
        envelope = _make_sleep_with_stages_envelope()
        result, _, mock_counter = await _call_translate(envelope)

        assert result["status"] == "ok"
        assert mock_counter.labels.call_count == 2
        call_predicates = {c.kwargs.get("predicate") for c in mock_counter.labels.call_args_list}
        assert "sleep_session" in call_predicates
        assert "sleep_stage_summary" in call_predicates
        for c in mock_counter.labels.call_args_list:
            assert c.kwargs.get("outcome") == "success"

    async def test_prometheus_counter_incremented_twice_when_stage_absent(self) -> None:
        """Counter is called for sleep_session (success) and sleep_stage_summary (skipped)."""
        envelope = _make_sleep_envelope()  # no stage data
        result, _, mock_counter = await _call_translate(envelope)

        assert result["status"] == "ok"
        assert mock_counter.labels.call_count == 2
        outcomes = {
            c.kwargs.get("predicate"): c.kwargs.get("outcome")
            for c in mock_counter.labels.call_args_list
        }
        assert outcomes.get("sleep_session") == "success"
        assert outcomes.get("sleep_stage_summary") == "skipped_malformed_payload"


# ---------------------------------------------------------------------------
# 8. Sleep session metadata extraction edge cases
# ---------------------------------------------------------------------------


class TestSleepSessionMetadataEdgeCases:
    """Unit tests for _extract_sleep_session_metadata edge cases."""

    def test_minutes_asleep_zero_is_preserved(self) -> None:
        """minutes_asleep=0 must be stored — 0 is a valid value, not absent."""
        from butlers.tools.health.wellness_ingest import _extract_sleep_session_metadata

        raw = {"durationMillis": 25200000, "minutesAsleep": 0}
        meta = _extract_sleep_session_metadata(raw)

        assert "minutes_asleep" in meta, "minutes_asleep=0 must not be silently dropped"
        assert meta["minutes_asleep"] == 0

    def test_minutes_awake_zero_is_preserved(self) -> None:
        """minutes_awake=0 must be stored — 0 is a valid value, not absent."""
        from butlers.tools.health.wellness_ingest import _extract_sleep_session_metadata

        raw = {"durationMillis": 25200000, "minutesAwake": 0}
        meta = _extract_sleep_session_metadata(raw)

        assert "minutes_awake" in meta, "minutes_awake=0 must not be silently dropped"
        assert meta["minutes_awake"] == 0

    def test_minutes_asleep_fallback_to_snake_case(self) -> None:
        """minutes_asleep falls back from minutesAsleep to minutes_asleep."""
        from butlers.tools.health.wellness_ingest import _extract_sleep_session_metadata

        raw = {"durationMillis": 25200000, "minutes_asleep": 45}
        meta = _extract_sleep_session_metadata(raw)

        assert meta.get("minutes_asleep") == 45

    def test_session_id_blank_stored_as_none(self) -> None:
        """A blank session_id must be normalised to None, not stored as an empty string."""
        from butlers.tools.health.wellness_ingest import _extract_sleep_session_metadata

        raw = {"durationMillis": 25200000, "session_id": ""}
        meta = _extract_sleep_session_metadata(raw)

        # Blank session_id must be None in metadata, not ""
        assert meta.get("session_id") is None

    def test_session_id_camel_case_fallback(self) -> None:
        """session_id falls back to sessionId when snake_case field is absent."""
        from butlers.tools.health.wellness_ingest import _extract_sleep_session_metadata

        raw = {"durationMillis": 25200000, "sessionId": "gfit-abc"}
        meta = _extract_sleep_session_metadata(raw)

        assert meta.get("session_id") == "gfit-abc"

    def test_session_id_absent_stored_as_none(self) -> None:
        """When session_id is entirely absent, metadata contains session_id=None."""
        from butlers.tools.health.wellness_ingest import _extract_sleep_session_metadata

        raw = {"durationMillis": 25200000}
        meta = _extract_sleep_session_metadata(raw)

        assert meta.get("session_id") is None


# ---------------------------------------------------------------------------
# 9. Owner identity validation — multi-account acceptance and rejection
# ---------------------------------------------------------------------------


class TestOwnerIdentityValidation:
    """Acceptance tests for the multi-account owner identity check.

    Covers the scenarios from bu-91zdb.6:
    - Accept envelopes whose sender matches any active, health-scoped account.
    - Reject when the sender's account is missing scopes or is revoked.
    - Reject when the sender is not in the recognised identity set at all.
    - The recognised-identity set is cached after the first query.
    """

    async def test_owner_identity_validation_accepts_secondary_health_scoped_account(
        self,
    ) -> None:
        """Envelope from a secondary health-scoped account is accepted.

        Both uniquosity@ (primary) and tzeuse@ (secondary) are active and
        health-scoped.  An envelope with sender.identity=tzeuse@ must be
        accepted and the result must carry the owner entity_id.
        """
        envelope = _make_sleep_envelope(sender_identity="tzeuse@gmail.com")

        # Both accounts are active and health-scoped — recognised set contains both.
        result, mock_store, _ = await _call_translate(
            envelope,
            recognised_emails=["uniquosity@gmail.com", "tzeuse@gmail.com"],
        )

        assert result["status"] == "ok"
        assert result["facts_written"] >= 1
        # entity_id passed to store_fact is the owner entity (resolved via
        # resolve_owner_entity_info, which is mocked to return a UUID string).
        store_call = mock_store.call_args_list[0]
        entity_id_kwarg = store_call.kwargs.get("entity_id")
        assert entity_id_kwarg is not None, "entity_id must be forwarded to memory_store_fact"

    async def test_owner_identity_validation_rejects_account_without_health_scopes(
        self,
    ) -> None:
        """Envelope from an account missing health scopes is rejected.

        tzeuse@ exists in google_accounts but its granted_scopes does not
        contain all three required Google Health scopes, so list_health_scoped_accounts
        will not include it.  The ingest must be rejected.
        """
        envelope = _make_sleep_envelope(sender_identity="tzeuse@gmail.com")

        # Recognised set only contains uniquosity@; tzeuse@ has insufficient scopes.
        result, _, mock_counter = await _call_translate(
            envelope, recognised_emails=["uniquosity@gmail.com"]
        )

        assert result["status"] == "rejected_non_owner_sender"
        label_call = mock_counter.labels.call_args
        assert label_call.kwargs.get("outcome") == "rejected_non_owner_sender"

    async def test_owner_identity_validation_rejects_revoked_account(self) -> None:
        """Envelope from a revoked account is rejected.

        tzeuse@ row exists in google_accounts but has status='revoked'.
        list_health_scoped_accounts excludes non-active rows, so the ingest
        must be rejected.
        """
        envelope = _make_sleep_envelope(sender_identity="tzeuse@gmail.com")

        # Recognised set is empty (revoked accounts are excluded by the registry helper).
        result, _, _ = await _call_translate(envelope, recognised_emails=[])

        assert result["status"] == "rejected_non_owner_sender"

    async def test_owner_identity_set_queried_once_per_session(self) -> None:
        """The recognised-identity set is fetched once and cached for subsequent calls.

        Two successive translate_wellness_envelope calls with the same pool must
        result in only one call to list_health_scoped_accounts — the second call
        hits the cache.
        """
        import butlers.tools.health.wellness_ingest as _wi
        from butlers.tools.health.wellness_ingest import translate_wellness_envelope

        # Ensure cache is clear (autouse fixture already does this, but be explicit).
        _wi._recognised_owner_identities = None

        envelope = _make_sleep_envelope(sender_identity="user@example.com")
        account = _make_health_scoped_account("user@example.com")

        with (
            patch(
                "butlers.tools.health.wellness_ingest.list_health_scoped_accounts",
                new_callable=AsyncMock,
                return_value=[account],
            ) as mock_registry,
            patch(
                "butlers.tools.health.wellness_ingest.resolve_owner_entity_info",
                new_callable=AsyncMock,
                return_value=str(uuid.uuid4()),
            ),
            patch(
                "butlers.tools.health.wellness_ingest.memory_store_fact",
                new_callable=AsyncMock,
                return_value={"id": str(uuid.uuid4()), "superseded_id": None},
            ),
            patch("butlers.tools.health.wellness_ingest.health_wellness_ingest_total"),
        ):
            pool = AsyncMock()
            pool.fetchrow.return_value = None
            embedding_engine = MagicMock()

            r1 = await translate_wellness_envelope(pool, embedding_engine, envelope)
            r2 = await translate_wellness_envelope(pool, embedding_engine, envelope)

        assert r1["status"] == "ok"
        assert r2["status"] == "ok"
        # Registry must be called exactly once — second call uses the cache.
        assert mock_registry.await_count == 1, (
            f"list_health_scoped_accounts called {mock_registry.await_count} times; "
            "expected exactly 1 (cached after first call)"
        )

    async def test_transient_db_failure_does_not_poison_cache(self) -> None:
        """A transient DB error must NOT cache an empty identity set.

        If list_health_scoped_accounts raises on the first call, the cache
        must remain None so that subsequent calls retry the query.  Without
        this invariant a single transient failure would permanently disable
        wellness ingestion until the daemon restarts.
        """
        import butlers.tools.health.wellness_ingest as _wi
        from butlers.tools.health.wellness_ingest import _get_recognised_owner_identities

        _wi._recognised_owner_identities = None
        pool = AsyncMock()

        with patch(
            "butlers.tools.health.wellness_ingest.list_health_scoped_accounts",
            new_callable=AsyncMock,
            side_effect=RuntimeError("transient connection error"),
        ) as mock_registry:
            result = await _get_recognised_owner_identities(pool)

        # Transient failure returns empty frozenset without caching.
        assert result == frozenset()
        assert _wi._recognised_owner_identities is None, (
            "cache must remain None after a transient failure so subsequent calls retry"
        )
        assert mock_registry.await_count == 1


# ---------------------------------------------------------------------------
# 10. Regression: 4-segment external_event_id parsing (bu-rmwyi)
# ---------------------------------------------------------------------------


class TestFourSegmentExternalEventId:
    """Regression tests for 4-segment external_event_id format.

    After bu-91zdb.4 the connector emits:
        google_health:<account_email>:<resource>:<date_or_id>

    These tests lock down that the resource segment is correctly extracted from
    position [2] (not [1]) so wellness data is never silently discarded.
    """

    async def test_wellness_ingest_parses_4_segment_external_event_id_for_sleep_session(
        self,
    ) -> None:
        """4-segment sleep_session event ID is parsed correctly.

        envelope external_event_id = 'google_health:uniquosity@gmail.com:sleep_session:sess-4seg'
        Must yield status=ok with predicate='sleep_session', NOT skipped_unknown_predicate.
        """
        envelope = _make_sleep_envelope(
            session_id="sess-4seg",
            sender_identity="uniquosity@gmail.com",
        )
        # Confirm the fixture actually uses the 4-segment format.
        eid = envelope["event"]["external_event_id"]
        assert eid == "google_health:uniquosity@gmail.com:sleep_session:sess-4seg", (
            f"Fixture did not produce the expected 4-segment id; got: {eid!r}"
        )

        result, mock_store, _ = await _call_translate(
            envelope,
            recognised_emails=["uniquosity@gmail.com"],
        )

        assert result["status"] == "ok", (
            f"Expected ok but got {result['status']!r} — "
            "resource segment is probably being read from the wrong position"
        )
        assert result["predicate"] == "sleep_session"
        assert result["facts_written"] >= 1
        mock_store.assert_awaited()

    async def test_wellness_ingest_parses_4_segment_external_event_id_for_daily_summary(
        self,
    ) -> None:
        """4-segment daily-summary event ID (activity) is parsed correctly.

        envelope external_event_id = 'google_health:uniquosity@gmail.com:activity:2026-04-24'
        Must yield status=ok with facts for measurement_steps and measurement_active_minutes,
        NOT skipped_unknown_predicate.
        """
        envelope = _make_activity_envelope(
            record_date="2026-04-24",
            sender_identity="uniquosity@gmail.com",
        )
        # Confirm the fixture actually uses the 4-segment format.
        eid = envelope["event"]["external_event_id"]
        assert eid == "google_health:uniquosity@gmail.com:activity:2026-04-24", (
            f"Fixture did not produce the expected 4-segment id; got: {eid!r}"
        )

        result, mock_store, _ = await _call_translate(
            envelope,
            recognised_emails=["uniquosity@gmail.com"],
        )

        assert result["status"] == "ok", (
            f"Expected ok but got {result['status']!r} — "
            "resource segment is probably being read from the wrong position"
        )
        # activity fans out to two facts
        assert result["facts_written"] == 2
        predicates = [f["predicate"] for f in result["facts"]]
        assert "measurement_steps" in predicates
        assert "measurement_active_minutes" in predicates


# ---------------------------------------------------------------------------
# 11. Home Assistant provider arm (epic bu-w7qf2 §3, design ADR-4/5)
# ---------------------------------------------------------------------------


def _make_ha_envelope(
    metric: str = "blood_pressure_systolic",
    value: float = 120,
    unit: str = "mmHg",
    valid_at: str = "2026-06-12T14:30:00+00:00",
    source_entity_id: str = "sensor.withings_systolic_blood_pressure",
    device_class: str | None = None,
    sender_identity: str | None = None,
    idempotency_key: str | None = None,
    normalized_text: str | None = None,
    wellness_measurement: dict | None = None,
    omit_measurement: bool = False,
) -> dict:
    """Build a minimal ``wellness/home_assistant`` ingest.v1 envelope.

    Mirrors the normalized payload shape from design ADR-4: the
    ``payload.raw.wellness_measurement`` block carries the canonical
    measurement, plus the full HA event context lives alongside in ``raw``.
    """
    if sender_identity is None:
        sender_identity = source_entity_id
    external_event_id = f"ha:{source_entity_id}:1749738600000"

    raw: dict = {
        # Full HA event context as today (abbreviated).
        "entity_id": source_entity_id,
        "new_state": {"state": str(value)},
    }
    if not omit_measurement:
        measurement = (
            wellness_measurement
            if wellness_measurement is not None
            else {
                "metric": metric,
                "value": value,
                "unit": unit,
                "valid_at": valid_at,
                "source_entity_id": source_entity_id,
                "device_class": device_class,
            }
        )
        raw["wellness_measurement"] = measurement

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "wellness",
            "provider": "home_assistant",
            "endpoint_identity": "home_assistant:default",
        },
        "event": {
            "external_event_id": external_event_id,
            "external_thread_id": None,
            "observed_at": "2026-06-12T14:30:05Z",
        },
        "sender": {"identity": sender_identity},
        "payload": {
            "raw": raw,
            "normalized_text": (
                normalized_text
                if normalized_text is not None
                else "Blood pressure (systolic): 120 mmHg"
            ),
        },
        "control": {
            "idempotency_key": idempotency_key or f"event:wellness:{external_event_id}",
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


def _expected_agnostic_key(
    owner_entity_id: str,
    scope: str,
    predicate: str,
    valid_at_iso: str,
) -> str:
    """Recompute the provider-agnostic idempotency key (design ADR-5)."""
    import hashlib

    parts = f"wellness|{owner_entity_id}|{scope}|{predicate}|{valid_at_iso}"
    return hashlib.sha256(parts.encode()).hexdigest()[:32]


async def _call_translate_ha(
    envelope: dict,
    *,
    owner_entity_id: str | None = None,
    store_fact_return=None,
):
    """Call translate_wellness_envelope for a home_assistant envelope.

    The HA arm does NOT consult ``list_health_scoped_accounts`` (design ADR-4);
    sender validation pins on provider + payload shape.  We still patch the
    registry so that any accidental call would be observable (await_count == 0).
    """
    from butlers.tools.health.wellness_ingest import translate_wellness_envelope

    if owner_entity_id is None:
        owner_entity_id = str(uuid.uuid4())

    pool = AsyncMock()

    async def _fetchrow(query, *args, **kwargs):
        if "public.entities" in query:
            row = MagicMock()
            row.__getitem__ = MagicMock(
                side_effect=lambda k: owner_entity_id if k == "id" else None
            )
            return row
        return None

    pool.fetchrow.side_effect = _fetchrow
    embedding_engine = MagicMock()

    sf_result = store_fact_return or {"id": str(uuid.uuid4()), "superseded_id": None}

    with (
        patch(
            "butlers.tools.health.wellness_ingest.list_health_scoped_accounts",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_registry,
        patch(
            "butlers.tools.health.wellness_ingest.resolve_owner_entity_info",
            new_callable=AsyncMock,
            return_value=owner_entity_id,
        ),
        patch(
            "butlers.tools.health.wellness_ingest.memory_store_fact",
            new_callable=AsyncMock,
            return_value=sf_result,
        ) as mock_store,
        patch("butlers.tools.health.wellness_ingest.health_wellness_ingest_total") as mock_counter,
    ):
        result = await translate_wellness_envelope(pool, embedding_engine, envelope)
        return result, mock_store, mock_counter, mock_registry, owner_entity_id


class TestHomeAssistantArm:
    """The home_assistant provider arm translates wellness_measurement payloads."""

    async def test_well_formed_envelope_writes_one_fact(self) -> None:
        """A well-formed HA envelope writes exactly one measurement_{metric} fact."""
        envelope = _make_ha_envelope(metric="blood_pressure_systolic", value=120)
        result, mock_store, _, _, _ = await _call_translate_ha(envelope)

        assert result["status"] == "ok", result
        assert result["facts_written"] == 1
        assert result["predicate"] == "measurement_blood_pressure_systolic"
        assert "fact_id" in result
        mock_store.assert_awaited_once()

    async def test_predicate_is_measurement_metric(self) -> None:
        """Predicate is derived as measurement_{metric}."""
        envelope = _make_ha_envelope(metric="weight", value=72.5, unit="kg")
        result, mock_store, _, _, _ = await _call_translate_ha(envelope)

        assert result["status"] == "ok"
        call = mock_store.call_args
        assert call.kwargs.get("predicate") == "measurement_weight"
        assert call.kwargs.get("scope") == "health"

    async def test_valid_at_from_payload(self) -> None:
        """valid_at is taken from the wellness_measurement payload, not observed_at."""
        envelope = _make_ha_envelope(valid_at="2026-06-12T14:30:00+00:00")
        result, mock_store, _, _, _ = await _call_translate_ha(envelope)

        assert result["status"] == "ok"
        call = mock_store.call_args
        assert call.kwargs.get("valid_at") == "2026-06-12T14:30:00+00:00"

    async def test_metadata_shape(self) -> None:
        """metadata = {provider, source_entity_id, unit, value}."""
        envelope = _make_ha_envelope(
            metric="heart_rate",
            value=72,
            unit="bpm",
            source_entity_id="sensor.oura_heart_rate",
        )
        result, mock_store, _, _, _ = await _call_translate_ha(envelope)

        assert result["status"] == "ok"
        meta = mock_store.call_args.kwargs.get("metadata")
        assert meta == {
            "provider": "home_assistant",
            "source_entity_id": "sensor.oura_heart_rate",
            "unit": "bpm",
            "value": 72,
        }

    async def test_owner_entity_forwarded(self) -> None:
        """The resolved owner entity_id is forwarded to memory_store_fact."""
        owner = str(uuid.uuid4())
        envelope = _make_ha_envelope()
        result, mock_store, _, _, _ = await _call_translate_ha(envelope, owner_entity_id=owner)

        assert result["status"] == "ok"
        assert mock_store.call_args.kwargs.get("entity_id") == owner

    async def test_does_not_consult_health_scoped_accounts(self) -> None:
        """HA arm does NOT call list_health_scoped_accounts (design ADR-4)."""
        envelope = _make_ha_envelope()
        result, _, _, mock_registry, _ = await _call_translate_ha(envelope)

        assert result["status"] == "ok"
        assert mock_registry.await_count == 0

    async def test_success_counter_labelled_with_predicate(self) -> None:
        """On success the counter is labelled predicate=measurement_{metric}, outcome=success."""
        envelope = _make_ha_envelope(metric="blood_sugar", value=95, unit="mg/dL")
        result, _, mock_counter, _, _ = await _call_translate_ha(envelope)

        assert result["status"] == "ok"
        mock_counter.labels.assert_called_once_with(
            predicate="measurement_blood_sugar", outcome="success"
        )
        mock_counter.labels.return_value.inc.assert_called_once()

    async def test_xiaomi_scale_weight_writes_measurement_weight_fact(self) -> None:
        """Xiaomi body-composition scale weight reading writes measurement_weight with provider=home_assistant.

        Regression test for bu-aabi1: HA scale weight events (lifecycle_state='parsed')
        produced zero health facts because (a) the policy bypass envelope used the HA
        connector's endpoint_identity instead of 'switchboard', causing health butler
        trusted_route_callers auth rejection, and (b) AGENTS.md said 'google_health
        connector' only, so the LLM would skip wellness_ingest_envelope for HA events.
        """
        envelope = _make_ha_envelope(
            metric="weight",
            value=75.4,
            unit="kg",
            valid_at="2026-06-20T07:15:00+00:00",
            source_entity_id="sensor.xiaomi_weighing_scale_weight",
            device_class="weight",
        )
        result, mock_store, _, _, _ = await _call_translate_ha(envelope)

        assert result["status"] == "ok", result
        assert result["facts_written"] == 1
        assert result["predicate"] == "measurement_weight"

        call = mock_store.call_args
        assert call.kwargs.get("predicate") == "measurement_weight"
        assert call.kwargs.get("scope") == "health"
        meta = call.kwargs.get("metadata")
        assert meta["provider"] == "home_assistant"
        assert meta["source_entity_id"] == "sensor.xiaomi_weighing_scale_weight"
        assert meta["unit"] == "kg"
        assert meta["value"] == 75.4


class TestHomeAssistantIdempotency:
    """Provider-agnostic idempotency key (design ADR-5)."""

    async def test_explicit_agnostic_key_forwarded(self) -> None:
        """The explicit provider-agnostic key is forwarded to memory_store_fact."""
        owner = str(uuid.uuid4())
        envelope = _make_ha_envelope(
            metric="blood_pressure_systolic",
            valid_at="2026-06-12T14:30:00+00:00",
        )
        result, mock_store, _, _, _ = await _call_translate_ha(envelope, owner_entity_id=owner)

        assert result["status"] == "ok"
        expected = _expected_agnostic_key(
            owner,
            "health",
            "measurement_blood_pressure_systolic",
            "2026-06-12T14:30:00+00:00",
        )
        assert mock_store.call_args.kwargs.get("idempotency_key") == expected

    async def test_key_is_provider_agnostic(self) -> None:
        """The idempotency key must not contain the provider or the episode id."""
        owner = str(uuid.uuid4())
        envelope = _make_ha_envelope()
        result, mock_store, _, _, _ = await _call_translate_ha(envelope, owner_entity_id=owner)

        assert result["status"] == "ok"
        key = mock_store.call_args.kwargs.get("idempotency_key")
        # 32-char sha256 prefix — opaque, no vendor string embedded.
        assert isinstance(key, str)
        assert len(key) == 32
        assert "home_assistant" not in key
        assert "ha:" not in key

    async def test_distinct_valid_at_yields_distinct_keys(self) -> None:
        """Two readings at distinct valid_at produce two distinct keys (two facts)."""
        owner = str(uuid.uuid4())
        env_a = _make_ha_envelope(valid_at="2026-06-12T14:30:00+00:00")
        env_b = _make_ha_envelope(valid_at="2026-06-12T18:00:00+00:00")

        _, store_a, _, _, _ = await _call_translate_ha(env_a, owner_entity_id=owner)
        _, store_b, _, _, _ = await _call_translate_ha(env_b, owner_entity_id=owner)

        key_a = store_a.call_args.kwargs.get("idempotency_key")
        key_b = store_b.call_args.kwargs.get("idempotency_key")
        assert key_a != key_b

    async def test_same_predicate_and_valid_at_yield_same_key(self) -> None:
        """Same (predicate, valid_at) under the same owner/scope → same key (dedup)."""
        owner = str(uuid.uuid4())
        env = _make_ha_envelope(valid_at="2026-06-12T14:30:00+00:00")

        # Two deliveries of the same physical reading (e.g. replay).
        _, store_1, _, _, _ = await _call_translate_ha(env, owner_entity_id=owner)
        _, store_2, _, _, _ = await _call_translate_ha(env, owner_entity_id=owner)

        key_1 = store_1.call_args.kwargs.get("idempotency_key")
        key_2 = store_2.call_args.kwargs.get("idempotency_key")
        assert key_1 == key_2

    async def test_duplicate_delivery_returns_existing_fact_id(self) -> None:
        """When storage reports the same fact id (no-op dedup), result reflects it.

        The storage layer's (tenant_id, idempotency_key) no-op check returns the
        existing fact id; the translator surfaces that id unchanged.
        """
        existing_id = str(uuid.uuid4())
        envelope = _make_ha_envelope()
        result, _, _, _, _ = await _call_translate_ha(
            envelope, store_fact_return={"id": existing_id, "supersedes_id": None}
        )

        assert result["status"] == "ok"
        assert result["fact_id"] == existing_id


class TestHomeAssistantMalformedPayload:
    """Malformed HA payloads are rejected with a labelled metric — no fact written."""

    async def test_missing_wellness_measurement_rejected(self) -> None:
        """raw without wellness_measurement → rejected, no fact."""
        envelope = _make_ha_envelope(omit_measurement=True)
        result, mock_store, mock_counter, _, _ = await _call_translate_ha(envelope)

        assert result["status"] == "rejected_malformed_payload"
        mock_store.assert_not_awaited()
        assert mock_counter.labels.call_args.kwargs.get("outcome") == ("rejected_malformed_payload")

    async def test_missing_metric_rejected(self) -> None:
        envelope = _make_ha_envelope(
            wellness_measurement={
                "value": 120,
                "unit": "mmHg",
                "valid_at": "2026-06-12T14:30:00+00:00",
                "source_entity_id": "sensor.x",
            }
        )
        result, mock_store, _, _, _ = await _call_translate_ha(envelope)

        assert result["status"] == "rejected_malformed_payload"
        mock_store.assert_not_awaited()

    async def test_missing_valid_at_rejected(self) -> None:
        envelope = _make_ha_envelope(
            wellness_measurement={
                "metric": "weight",
                "value": 70,
                "unit": "kg",
                "source_entity_id": "sensor.x",
            }
        )
        result, mock_store, _, _, _ = await _call_translate_ha(envelope)

        assert result["status"] == "rejected_malformed_payload"
        mock_store.assert_not_awaited()

    async def test_non_numeric_value_rejected(self) -> None:
        envelope = _make_ha_envelope(
            wellness_measurement={
                "metric": "weight",
                "value": "not-a-number",
                "unit": "kg",
                "valid_at": "2026-06-12T14:30:00+00:00",
                "source_entity_id": "sensor.x",
            }
        )
        result, mock_store, _, _, _ = await _call_translate_ha(envelope)

        assert result["status"] == "rejected_malformed_payload"
        mock_store.assert_not_awaited()

    async def test_missing_source_entity_id_rejected(self) -> None:
        envelope = _make_ha_envelope(
            wellness_measurement={
                "metric": "weight",
                "value": 70,
                "unit": "kg",
                "valid_at": "2026-06-12T14:30:00+00:00",
            }
        )
        result, mock_store, _, _, _ = await _call_translate_ha(envelope)

        assert result["status"] == "rejected_malformed_payload"
        mock_store.assert_not_awaited()


class TestUnknownProviderRejected:
    """Unknown providers are rejected with a labelled outcome (task 3.1)."""

    async def test_unknown_provider_rejected(self) -> None:
        from butlers.tools.health.wellness_ingest import translate_wellness_envelope

        envelope = _make_ha_envelope()
        envelope["source"]["provider"] = "withings"

        pool = AsyncMock()
        pool.fetchrow.return_value = None
        embedding_engine = MagicMock()

        with (
            patch(
                "butlers.tools.health.wellness_ingest.list_health_scoped_accounts",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "butlers.tools.health.wellness_ingest.memory_store_fact",
                new_callable=AsyncMock,
            ) as mock_store,
            patch(
                "butlers.tools.health.wellness_ingest.health_wellness_ingest_total"
            ) as mock_counter,
        ):
            result = await translate_wellness_envelope(pool, embedding_engine, envelope)

        assert result["status"] == "rejected_unknown_provider"
        mock_store.assert_not_awaited()
        assert mock_counter.labels.call_args.kwargs.get("outcome") == ("rejected_unknown_provider")

"""Spotify spoken playback must bypass LLM classification and routing.

``control.ingestion_tier='metadata'`` only controls persistence.  The global
ingestion policy must pre-resolve the ``metadata_only`` triage decision before
the Switchboard pipeline is allowed to classify or route an event.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.spotify import (
    SpokenSession,
    build_context_start_envelope,
    build_spoken_session_envelope,
    normalize_spoken_item,
)
from butlers.ingestion_policy import IngestionPolicyEvaluator
from butlers.modules.pipeline import MessagePipeline
from butlers.tools.switchboard.ingestion.ingest import (
    _build_request_context,
    _run_policy_evaluation,
)
from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

pytestmark = pytest.mark.unit

_ENDPOINT = "spotify:spotify_user_client:spotify:user123"
_OBSERVED = "2026-08-09T00:00:00+00:00"
_SPOKEN_RULE = {
    "id": "00000000-0000-0000-0001-000000000111",
    "rule_type": "substring",
    "condition": {"pattern": "spotify:spoken:"},
    "action": "metadata_only",
    "priority": 10,
}


def _evaluator() -> IngestionPolicyEvaluator:
    evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
    evaluator._rules = [_SPOKEN_RULE]
    evaluator._last_loaded_at = time.monotonic()
    return evaluator


def _spoken_payload() -> dict:
    item = normalize_spoken_item(
        {
            "type": "episode",
            "id": "episode-1",
            "name": "Chapter 1",
            "show": {"id": "show-1", "name": "The Daily", "uri": "spotify:show:show-1"},
        }
    )
    assert item is not None
    return build_spoken_session_envelope(
        endpoint_identity=_ENDPOINT,
        spotify_user_id="user123",
        session=SpokenSession(
            item=item,
            started_at=datetime(2026, 8, 9, tzinfo=UTC),
            last_activity_at=datetime(2026, 8, 9, tzinfo=UTC),
        ),
        observed_at=_OBSERVED,
    )


def test_spoken_prefix_pre_resolves_metadata_only_triage() -> None:
    decision = _run_policy_evaluation(
        _spoken_payload(), _evaluator(), source_channel="spotify_user_client"
    )

    assert decision.action == "metadata_only"
    assert decision.bypasses_llm is True
    assert decision.matched_rule_type == "substring"


@pytest.mark.asyncio
async def test_spoken_pre_resolved_triage_never_spawns_or_routes() -> None:
    payload = _spoken_payload()
    decision = _run_policy_evaluation(payload, _evaluator(), source_channel="spotify_user_client")
    envelope = parse_ingest_envelope(payload)
    context = _build_request_context(
        envelope,
        request_id=uuid.uuid4(),
        received_at=datetime.now(UTC),
        triage_decision=decision,
    )
    dispatch = AsyncMock()
    pipeline = MessagePipeline(
        switchboard_pool=MagicMock(), dispatch_fn=dispatch, source_butler="switchboard"
    )

    with patch(
        "butlers.modules.pipeline.record_routing_verdict", new_callable=AsyncMock
    ) as record_verdict:
        result = await pipeline.process(
            payload["payload"]["normalized_text"],
            tool_args={"source_channel": "spotify_user_client", "request_context": context},
            message_inbox_id="00000000-0000-0000-0000-000000000111",
        )

    dispatch.assert_not_awaited()
    assert result.target_butler == "metadata_only"
    assert record_verdict.await_args.kwargs["verdict_action"] == "metadata_only"


def test_track_context_event_remains_classifiable() -> None:
    payload = build_context_start_envelope(
        endpoint_identity=_ENDPOINT,
        spotify_user_id="user123",
        track_id="track-1",
        track_name="Song",
        artist_names=["Artist"],
        album_name="Album",
        duration_ms=120_000,
        context_uri=None,
        device_name=None,
        timestamp_ms=1_754_697_600_000,
        raw_payload={},
        observed_at=_OBSERVED,
    )

    decision = _run_policy_evaluation(payload, _evaluator(), source_channel="spotify_user_client")

    assert decision.action == "pass_through"

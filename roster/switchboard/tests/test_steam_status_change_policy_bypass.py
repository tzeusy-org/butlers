"""Routing-level test: Steam status_change bypasses LLM classification/routing.

bu-f7yk5: setting ``control.ingestion_tier='metadata'`` on the Steam
status_change envelope (``build_status_change_envelope``) only controls
*persistence* (payload.raw=null, initial message_inbox lifecycle state) — it
is never read by ``pipeline.process()``'s routing/classification decision.
The actual bypass is the pre-resolved ``triage_decision`` produced by
``IngestionPolicyEvaluator(scope='global')`` from an ``ingestion_rules`` row,
which is what this test drives end-to-end through the real
``_run_policy_evaluation`` / ``_make_ingestion_envelope`` production code
(mirroring ``roster/switchboard/tests/test_wellness_policy_bypass.py``).

The seeded rule (migration ``025_switchboard_steam_status_skip.py``) matches
on a ``substring`` of ``external_event_id`` (``"steam:status:"``) rather than
``source_channel='gaming'`` because the gaming channel carries other event
types (play_session, achievement_unlock, game_purchase, friend_change) that
must remain fully routable — this file asserts both halves of that contract.
"""

from __future__ import annotations

import time

import pytest

from butlers.connectors.steam import (
    _to_wire_envelope,
    build_achievement_unlock_envelope,
    build_play_session_envelope,
    build_status_change_envelope,
)
from butlers.ingestion_policy import IngestionPolicyEvaluator
from butlers.tools.switchboard.ingestion.ingest import (
    _make_ingestion_envelope,
    _run_policy_evaluation,
)
from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

pytestmark = pytest.mark.unit

_STEAM_ID = 76561198012345678
_ENDPOINT = f"gaming:steam:{_STEAM_ID}"
_POLL_TS = "2026-07-11T03:50:00+00:00"


def _make_evaluator_with_rules(rules: list[dict]) -> IngestionPolicyEvaluator:
    """Create an IngestionPolicyEvaluator with pre-loaded rules (no DB)."""
    evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
    evaluator._rules = rules
    evaluator._last_loaded_at = time.monotonic()
    return evaluator


def _steam_status_change_rule() -> dict:
    """The rule seeded by migration 025_switchboard_steam_status_skip.py."""
    return {
        "id": "00000000-0000-0000-0001-000000000110",
        "rule_type": "substring",
        "condition": {"pattern": "steam:status:"},
        "action": "metadata_only",
        "priority": 10,
    }


def _status_change_payload() -> dict:
    """Wire-format ingest.v1 payload for a Steam 'Came online' status_change."""
    envelope = build_status_change_envelope(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        persona_state=1,
        game_extra_info=None,
        prev_persona_state=0,
        prev_game_extra_info=None,
        poll_ts=_POLL_TS,
    )
    return _to_wire_envelope(envelope)


def _play_session_payload() -> dict:
    """Wire-format ingest.v1 payload for a Steam play_session event."""
    envelope = build_play_session_envelope(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        app_id=730,
        game_name="Counter-Strike 2",
        playtime_2weeks=120,
        playtime_delta=30,
        poll_ts=_POLL_TS,
        raw={"appid": 730},
    )
    return _to_wire_envelope(envelope)


def _achievement_payload() -> dict:
    """Wire-format ingest.v1 payload for a Steam achievement_unlock event."""
    envelope = build_achievement_unlock_envelope(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        app_id=730,
        game_name="Counter-Strike 2",
        achievement_api_name="WIN_100_ROUNDS",
        achievement_display_name="Centurion",
        achievement_description="Win 100 rounds",
        unlock_time=1_700_000_000,
        poll_ts=_POLL_TS,
    )
    return _to_wire_envelope(envelope)


class TestSteamStatusChangePolicyBypass:
    def test_status_change_matches_rule_as_metadata_only(self) -> None:
        """A Steam status_change envelope matches the seeded rule -> metadata_only."""
        payload = _status_change_payload()
        evaluator = _make_evaluator_with_rules([_steam_status_change_rule()])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="gaming")
        assert decision.action == "metadata_only"

    def test_status_change_rule_type_is_substring(self) -> None:
        """Matched rule type is 'substring' (external_event_id prefix), not source_channel."""
        payload = _status_change_payload()
        evaluator = _make_evaluator_with_rules([_steam_status_change_rule()])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="gaming")
        assert decision.matched_rule_type == "substring"

    def test_status_change_decision_bypasses_llm(self) -> None:
        """PolicyDecision.bypasses_llm is True -> no Switchboard LLM spawn, no routing/notify."""
        payload = _status_change_payload()
        evaluator = _make_evaluator_with_rules([_steam_status_change_rule()])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="gaming")
        assert decision.bypasses_llm is True

    def test_status_change_wire_envelope_is_contract_valid(self) -> None:
        """The stripped wire envelope still validates against ingest.v1."""
        payload = _status_change_payload()
        parse_ingest_envelope(payload)

    def test_play_session_still_passes_through(self) -> None:
        """A non-presence gaming event (play_session) does NOT match the status_change
        rule and must remain fully routable/classifiable."""
        payload = _play_session_payload()
        evaluator = _make_evaluator_with_rules([_steam_status_change_rule()])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="gaming")
        assert decision.action == "pass_through"

    def test_achievement_unlock_still_passes_through(self) -> None:
        """Another non-presence gaming event (achievement_unlock) also remains routable."""
        payload = _achievement_payload()
        evaluator = _make_evaluator_with_rules([_steam_status_change_rule()])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="gaming")
        assert decision.action == "pass_through"

    def test_no_rules_loaded_returns_pass_through(self) -> None:
        """Empty rule set falls through to pass_through regardless of source_channel
        (regression guard: absence of the migration must not silently break gaming)."""
        payload = _status_change_payload()
        evaluator = _make_evaluator_with_rules([])
        decision = _run_policy_evaluation(payload, evaluator, source_channel="gaming")
        assert decision.action == "pass_through"


class TestMakeIngestionEnvelopeGamingRawKey:
    def test_status_change_raw_key_is_external_event_id(self) -> None:
        """_make_ingestion_envelope surfaces external_event_id as raw_key for gaming."""
        payload = _status_change_payload()
        env = _make_ingestion_envelope(payload)
        assert env.source_channel == "gaming"
        assert env.raw_key == payload["event"]["external_event_id"]
        assert env.raw_key.startswith("steam:status:")

    def test_play_session_raw_key_does_not_match_status_prefix(self) -> None:
        """A play_session external_event_id does not carry the status_change prefix."""
        payload = _play_session_payload()
        env = _make_ingestion_envelope(payload)
        assert env.raw_key.startswith("steam:play:")
        assert not env.raw_key.startswith("steam:status:")

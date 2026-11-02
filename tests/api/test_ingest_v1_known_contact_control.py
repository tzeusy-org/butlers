"""bu-q7vx1q.88.3: ``ingest_v1`` honors ``control.known_contact`` in the global rule pass.

The connector classifies the sender against its loaded snapshot and sends the result as
``control.known_contact``. A flagged global skip rule is passed over only for ``true``; an absent,
``false`` or non-boolean value keeps today's behavior and the rule applies. The test stops
persistence at the partition step and reads the triage decision handed to the request context.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.ingestion_policy import IngestionPolicyEvaluator
from butlers.tools.switchboard.ingestion import ingest as ingest_module


def _evaluator(*, flagged: bool) -> IngestionPolicyEvaluator:
    evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
    evaluator._last_loaded_at = time.monotonic()
    evaluator._rules = [
        {
            "id": "r1",
            "rule_type": "sender_domain",
            "condition": {
                "domain": "bulk.example",
                "match": "exact",
                **({"except_known_contacts": True} if flagged else {}),
            },
            "action": "skip",
            "priority": 10,
            "name": None,
            "created_at": "2026-02-01T00:00:00Z",
            "created_by": "dashboard",
            "promoted_from_suggestion_id": None,
        }
    ]
    return evaluator


def _payload(control: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "email",
            "provider": "gmail",
            "endpoint_identity": "gmail:user:test@example.com",
        },
        "event": {
            "external_event_id": "<m1@example.com>",
            "external_thread_id": "thread1",
            "observed_at": "2026-02-15T12:00:00+00:00",
        },
        "sender": {"identity": "someone@bulk.example"},
        "payload": {"raw": {"headers": {}}, "normalized_text": "Subject: Hello"},
        "control": {"idempotency_key": "gmail:test:m1", **control},
    }


async def _decision(control: dict[str, Any], *, flagged: bool = True) -> str:
    """Run ``ingest_v1`` up to persistence and return the global triage action."""
    seen: list[Any] = []
    real = ingest_module._build_request_context

    def spy(envelope, **kwargs):
        seen.append(kwargs["triage_decision"])
        return real(envelope, **kwargs)

    pool = MagicMock()
    pool.execute = AsyncMock(side_effect=RuntimeError("stop before persistence"))
    with (
        patch.object(ingest_module, "_find_request_by_dedupe_key", AsyncMock(return_value=None)),
        patch.object(ingest_module, "_find_request_by_content_hash", AsyncMock(return_value=None)),
        patch.object(ingest_module, "_build_request_context", spy),
        pytest.raises(RuntimeError, match="stop before persistence"),
    ):
        await ingest_module.ingest_v1(
            pool,
            _payload(control),
            policy_evaluator=_evaluator(flagged=flagged),
            enable_thread_affinity=False,
        )
    (decision,) = seen
    return decision.action


async def test_a_known_contact_passes_a_flagged_global_skip() -> None:
    assert await _decision({"known_contact": True}) == "pass_through"


@pytest.mark.parametrize("control", [{}, {"known_contact": False}], ids=["absent", "not-a-contact"])
async def test_an_absent_or_false_classification_still_skips(control: dict[str, Any]) -> None:
    assert await _decision(control) == "skip"


async def test_an_unflagged_skip_still_applies_to_a_known_contact() -> None:
    assert await _decision({"known_contact": True}, flagged=False) == "skip"

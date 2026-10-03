"""Account-security sensor (bu-q7vx1q.10): classifier, evaluator carve-out, publish, answer door.

Synthetic fixtures only. The evaluator tests drive the real
``IngestionPolicyEvaluator.evaluate()``; the I/O tests use a mocked pool in the
sibling style of ``tests/core/test_fleet_cases.py`` (real-Postgres coverage is in
``tests/integration/test_account_security_roundtrip.py``).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from butlers.account_security import (
    KIND_DELETION_SCHEDULED,
    KIND_MFA_CHANGED,
    KIND_NEW_SIGN_IN,
    KIND_PASSWORD_CHANGED,
    KIND_RECOVERY_CHANGED,
    VERIFICATION_AUTHENTICATED,
    VERIFICATION_UNVERIFIED,
    classify_account_security,
    is_account_security_sender,
)
from butlers.core import account_security_events as sensor
from butlers.core.fleet_cases import FleetCaseError
from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator

pytestmark = pytest.mark.unit

GOOGLE = "no-reply@accounts.google.com"
MICROSOFT = "account-security-noreply@accountprotection.microsoft.com"
APPLE = "appleid@id.apple.com"


@pytest.mark.parametrize(
    ("sender", "subject", "provider", "kind"),
    [
        (GOOGLE, "Security alert", "google", KIND_NEW_SIGN_IN),
        (GOOGLE, "New sign-in on Pixel", "google", KIND_NEW_SIGN_IN),
        (GOOGLE, "Recovery email changed", "google", KIND_RECOVERY_CHANGED),
        (MICROSOFT, "Microsoft account password was changed", "microsoft", KIND_PASSWORD_CHANGED),
        (MICROSOFT, "Security info was updated", "microsoft", KIND_MFA_CHANGED),
        (APPLE, "Your Apple ID was used to sign in", "apple", KIND_NEW_SIGN_IN),
        (APPLE, "Your account is scheduled for deletion", "apple", KIND_DELETION_SCHEDULED),
    ],
)
def test_first_party_alerts_classify(sender, subject, provider, kind):
    verdict = classify_account_security(sender, subject)
    assert verdict is not None
    assert (verdict.provider, verdict.kind) == (provider, kind)
    assert verdict.sender_verification == VERIFICATION_UNVERIFIED


@pytest.mark.parametrize(
    ("sender", "subject"),
    [
        ("no-reply@accounts.google.com.evil.example", "Security alert"),
        ("no-reply@mail.accounts.google.com", "Security alert"),
        ("noreply@example.com", "Security alert"),
        (GOOGLE, "Your weekly digest"),
        (GOOGLE, ""),
        (GOOGLE, "Your verification code"),
    ],
)
def test_lookalikes_and_non_alerts_do_not_classify(sender, subject):
    assert classify_account_security(sender, subject) is None


def test_authentication_results_confirm_or_reject():
    ok = "mx.example; dkim=pass header.i=@accounts.google.com header.d=accounts.google.com"
    assert (
        classify_account_security(
            GOOGLE, "Security alert", headers={"authentication-results": ok}
        ).sender_verification
        == VERIFICATION_AUTHENTICATED
    )
    spoof = "mx.example; dmarc=fail header.from=accounts.google.com"
    assert (
        classify_account_security(
            GOOGLE, "Security alert", headers={"Authentication-Results": spoof}
        )
        is None
    )
    unaligned = "mx.example; dkim=pass header.d=attacker.example"
    assert (
        classify_account_security(
            GOOGLE, "Security alert", headers={"Authentication-Results": unaligned}
        ).sender_verification
        == VERIFICATION_UNVERIFIED
    )


def test_sender_helper_is_exact():
    assert is_account_security_sender(" No-Reply@Accounts.Google.com ")
    assert not is_account_security_sender("someone@accounts.google.com")


# ---------------------------------------------------------------------------
# Evaluator carve-out (real IngestionPolicyEvaluator.evaluate())
# ---------------------------------------------------------------------------


def _evaluator(rules, scope="global") -> IngestionPolicyEvaluator:
    evaluator = IngestionPolicyEvaluator(scope=scope, db_pool=None)
    evaluator._rules = rules
    evaluator._last_loaded_at = time.monotonic()
    return evaluator


_SKIP_RULE = {
    "id": "00000000-0000-0000-0000-0000000000a1",
    "rule_type": "sender_address",
    "condition": {"address": GOOGLE},
    "action": "skip",
    "priority": 1,
    "created_by": "promotion",
}


def _envelope(subject: str, sender: str = GOOGLE) -> IngestionEnvelope:
    return IngestionEnvelope(
        sender_address=sender, source_channel="email", headers={"Subject": subject}, raw_key=sender
    )


def test_promoted_skip_cannot_suppress_a_classified_alert():
    decision = _evaluator([_SKIP_RULE], scope="global").evaluate(_envelope("Security alert"))
    assert decision.action == "metadata_only"
    assert "account-security" in decision.reason


def test_skip_still_applies_to_unclassified_mail_from_the_same_sender():
    decision = _evaluator([_SKIP_RULE]).evaluate(_envelope("Tips for your Google account"))
    assert decision.action == "skip"


def test_carve_out_is_global_scope_only():
    decision = _evaluator([_SKIP_RULE], scope="connector:gmail:x").evaluate(
        _envelope("Security alert")
    )
    assert decision.action == "skip"


# ---------------------------------------------------------------------------
# Ingest-record classification
# ---------------------------------------------------------------------------


def test_classify_ingest_record_reads_subject_and_gmail_auth_header():
    raw = {
        "payload": {
            "headers": [{"name": "Authentication-Results", "value": "mx; dmarc=pass header.from=x"}]
        }
    }
    verdict = sensor.classify_ingest_record(
        source_channel="email",
        sender_address=GOOGLE,
        normalized_text="Subject: Security alert\n\nbody is never read",
        raw=raw,
    )
    assert verdict is not None
    assert verdict.sender_verification == VERIFICATION_AUTHENTICATED


def test_classify_ingest_record_metadata_tier_and_non_email():
    assert (
        sensor.classify_ingest_record(
            source_channel="email",
            sender_address=GOOGLE,
            normalized_text="Subject: Security alert ",
            raw=None,
        ).sender_verification
        == VERIFICATION_UNVERIFIED
    )
    assert (
        sensor.classify_ingest_record(
            source_channel="telegram_bot",
            sender_address=GOOGLE,
            normalized_text="Subject: Security alert",
            raw=None,
        )
        is None
    )


def test_event_payload_never_carries_message_text():
    """The published payload is built only from fixed-vocabulary fields."""
    verdict = classify_account_security(GOOGLE, "Security alert")
    assert set(vars(verdict)) == {"kind", "provider", "provider_domain", "sender_verification"}


# ---------------------------------------------------------------------------
# Publish + answer door (mocked pool)
# ---------------------------------------------------------------------------


async def test_publish_records_one_typed_event_and_dedups():
    verdict = classify_account_security(GOOGLE, "Security alert")
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=None)
    publish = AsyncMock(return_value={"status": "ok", "event_id": "ev-1", "deliveries": []})
    with patch("butlers.core_tools._domain_events.publish_domain_event", publish):
        event_id = await sensor.publish_security_event(
            pool,
            verdict,
            source_request_id="req-1",
            external_event_id="msg-1",
            observed_at=datetime(2026, 10, 3, tzinfo=UTC),
        )
        assert event_id == "ev-1"
        kwargs = publish.await_args.kwargs
        assert kwargs["event_type"] == "switchboard.security_event"
        assert kwargs["source_butler"] == "switchboard"
        assert kwargs["payload"]["kind"] == KIND_NEW_SIGN_IN
        assert kwargs["payload"]["external_event_id"] == "msg-1"

        pool.fetchval = AsyncMock(return_value=1)
        publish.reset_mock()
        assert (
            await sensor.publish_security_event(
                pool,
                verdict,
                source_request_id="req-2",
                external_event_id="msg-1",
                observed_at=None,
            )
            is None
        )
        publish.assert_not_awaited()


def test_event_contract_admits_the_payload_shape():
    from pathlib import Path

    from butlers.core.domain_event_contracts import load_contract_registry

    registry = load_contract_registry(Path(__file__).resolve().parents[2] / "roster")
    payload = {
        "kind": "new_sign_in",
        "provider": "google",
        "provider_domain": "accounts.google.com",
        "sender_verification": "authenticated",
        "source_request_id": "req-1",
        "external_event_id": "msg-1",
        "observed_at": "2026-10-03T00:00:00+00:00",
    }
    assert (
        registry.check_publish(
            event_type="switchboard.security_event", publisher="switchboard", payload=payload
        )
        is None
    )


def _event_pool(provider="google"):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value={"id": "ev-1", "payload": {"provider": provider, "kind": "new_sign_in"}}
    )
    return pool


async def test_answer_no_opens_case_with_recovery_door():
    pool = _event_pool()
    with (
        patch.object(sensor.fleet_cases, "open_case", AsyncMock(return_value={"id": "case-1"})),
        patch.object(
            sensor.fleet_cases, "contribute_evidence", AsyncMock(return_value=({}, True))
        ) as contribute,
    ):
        result = await sensor.record_security_answer(pool, event_id="ev-1", answer="NO")
    assert result == {"status": "ok", "answer": "no", "case_id": "case-1"}
    evidence = contribute.await_args.kwargs
    assert evidence["ref"] == "ev-1"
    assert evidence["payload"]["recovery_door"].startswith("https://")


async def test_repeated_no_reuses_the_open_case():
    pool = _event_pool()
    with (
        patch.object(
            sensor.fleet_cases, "open_case", AsyncMock(side_effect=FleetCaseError("exists"))
        ),
        patch.object(
            sensor.fleet_cases, "find_open_case", AsyncMock(return_value={"id": "case-1"})
        ),
        patch.object(
            sensor.fleet_cases, "contribute_evidence", AsyncMock(return_value=({}, False))
        ),
    ):
        result = await sensor.record_security_answer(pool, event_id="ev-1", answer="no")
    assert result["case_id"] == "case-1"


async def test_answer_yes_is_a_no_op_and_bad_input_is_refused():
    pool = _event_pool()
    with patch.object(sensor.fleet_cases, "open_case", AsyncMock()) as open_case:
        assert (await sensor.record_security_answer(pool, event_id="ev-1", answer="yes"))[
            "case_id"
        ] is None
        open_case.assert_not_awaited()
    assert (await sensor.record_security_answer(pool, event_id="ev-1", answer="maybe"))[
        "status"
    ] == "error"
    pool.fetchrow = AsyncMock(return_value=None)
    assert (await sensor.record_security_answer(pool, event_id="nope", answer="no"))[
        "status"
    ] == "error"

"""Security question outbox: presentation is bounded and never re-sent (bu-s11n0s.1 S5c).

REQ-endpoint-custody-holds-008. The state walk, the database fence and the dashboard answer are
covered on real PostgreSQL in ``tests/migrations/test_core_348_security_question_answer.py``.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core import security_questions as sq

pytestmark = pytest.mark.unit

URL = "https://butlers.example.test/dash"


def _pool(rows, *, claim=1) -> AsyncMock:
    pool = AsyncMock()
    # fetch: stale-presenting sweep first, then the pending batch.
    pool.fetch = AsyncMock(side_effect=[[], rows])
    pool.fetchval = AsyncMock(return_value=claim)
    pool.execute = AsyncMock()
    return pool


def _row(kind="new_sign_in") -> dict:
    return {"event_id": uuid.uuid4(), "provider": "google", "kind": kind}


def test_message_uses_fixed_vocabulary_and_the_link() -> None:
    message = sq.question_message("google", "password_changed", "https://x.test/security/1")
    assert message == (
        "Was this you? Your Google account reported a password change. "
        "Answer on your dashboard: https://x.test/security/1"
    )
    assert "security change" in sq.question_message("google", "unheard_of", "l")


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        (URL, f"{URL}/security/e1"),
        (URL + "/", f"{URL}/security/e1"),
        (None, None),
        ("", None),
        ("javascript:alert(1)", None),
    ],
)
def test_link_needs_an_http_base(base, expected) -> None:
    assert sq.question_link(base, "e1") == expected


async def test_a_sent_question_is_presented_once() -> None:
    row = _row()
    pool = _pool([row])
    notify = AsyncMock(return_value={"status": "sent", "notification_id": str(uuid.uuid4())})
    counts = await sq.present_pending(pool, notify=notify, dashboard_url=URL)
    assert counts == {"presented": 1, "unavailable": 0, "ambiguous": 0}
    notify.assert_awaited_once()
    assert f"{URL}/security/{row['event_id']}" in notify.await_args.args[0]
    final = pool.execute.await_args.args
    assert final[2] == "presented"


async def test_a_lost_claim_sends_nothing() -> None:
    notify = AsyncMock()
    counts = await sq.present_pending(_pool([_row()], claim=None), notify=notify, dashboard_url=URL)
    notify.assert_not_awaited()
    assert counts["presented"] == 0


async def test_a_raising_send_is_ambiguous_and_not_retried() -> None:
    pool = _pool([_row()])
    notify = AsyncMock(side_effect=RuntimeError("socket closed"))
    counts = await sq.present_pending(pool, notify=notify, dashboard_url=URL)
    assert counts["ambiguous"] == 1
    assert pool.execute.await_args.args[2] == "ambiguous"
    notify.assert_awaited_once()


async def test_an_unconfirmed_failure_is_ambiguous_a_terminal_one_unavailable() -> None:
    unconfirmed = await sq._send(AsyncMock(return_value={"status": "failed", "error": "x"}), "m")
    assert unconfirmed[0] == "ambiguous"
    with patch("butlers.telegram_api.terminal_telegram_metadata", return_value={"terminal": True}):
        terminal = await sq._send(AsyncMock(return_value={"status": "failed"}), "m")
    assert terminal[0] == "unavailable"


async def test_no_dashboard_link_is_unavailable_without_a_send() -> None:
    notify = AsyncMock()
    pool = _pool([_row()])
    counts = await sq.present_pending(pool, notify=notify, dashboard_url=None)
    notify.assert_not_awaited()
    assert counts["unavailable"] == 1


async def test_only_answered_no_questions_record_a_case() -> None:
    event_id = uuid.uuid4()
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[{"event_id": event_id}])
    record = AsyncMock(return_value={"status": "ok", "answer": "no", "case_id": str(uuid.uuid4())})
    with patch.object(sq, "record_security_answer", record):
        assert await sq.record_answered_cases(pool) == 1
    record.assert_awaited_once_with(pool, event_id=str(event_id), answer="no")
    assert "a.answer = 'no'" in pool.fetch.await_args.args[0]


async def test_a_failed_case_stays_unrecorded_for_the_next_run() -> None:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[{"event_id": uuid.uuid4()}])
    with patch.object(sq, "record_security_answer", AsyncMock(side_effect=RuntimeError("db"))):
        assert await sq.record_answered_cases(pool) == 0
    pool.execute.assert_not_awaited()


async def test_publish_admits_only_authenticated_events() -> None:
    from butlers.account_security import AccountSecurityClassification
    from butlers.core import account_security_events as sensor

    def classification(verification: str) -> AccountSecurityClassification:
        return AccountSecurityClassification(
            kind="new_sign_in",
            provider="google",
            provider_domain="google.com",
            sender_verification=verification,
        )

    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=None)
    publish = AsyncMock(return_value={"status": "ok", "event_id": "ev-1"})
    admit = AsyncMock(return_value=True)
    with (
        patch("butlers.core_tools._domain_events.publish_domain_event", publish),
        patch.object(sq, "admit_event", admit),
    ):
        for verification in ("unverified", "failed"):
            await sensor.publish_security_event(
                pool,
                classification(verification),
                source_request_id="r",
                external_event_id=None,
                observed_at=None,
            )
        admit.assert_not_awaited()
        await sensor.publish_security_event(
            pool,
            classification("authenticated"),
            source_request_id="r2",
            external_event_id=None,
            observed_at=None,
        )
        admit.assert_awaited_once()
        assert admit.await_args.kwargs["event_id"] == "ev-1"


async def test_an_admission_failure_does_not_lose_the_published_event() -> None:
    from butlers.account_security import AccountSecurityClassification
    from butlers.core import account_security_events as sensor

    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=None)
    verdict = AccountSecurityClassification("new_sign_in", "google", "google.com", "authenticated")
    with (
        patch(
            "butlers.core_tools._domain_events.publish_domain_event",
            AsyncMock(return_value={"status": "ok", "event_id": "ev-9"}),
        ),
        patch.object(sq, "admit_event", AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        assert (
            await sensor.publish_security_event(
                pool, verdict, source_request_id="r", external_event_id=None, observed_at=None
            )
            == "ev-9"
        )

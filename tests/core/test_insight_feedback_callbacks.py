"""Signed insight feedback callbacks reject cross-domain and hostile tokens."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from butlers.core.approval_callbacks import (
    mint_approval_callback_token,
    verify_approval_callback_token,
)
from butlers.core.decision_callbacks import (
    mint_decision_callback_token,
    verify_decision_callback_token,
)
from butlers.core.insight_feedback_callbacks import (
    mint_insight_feedback_token,
    parse_insight_feedback_token,
    verify_insight_feedback_token,
)

pytestmark = pytest.mark.unit
ID = UUID("12345678-1234-5678-1234-567812345678")
NOW = datetime(2026, 10, 16, tzinfo=UTC)
SECRET = "test-only-insight-secret"


def test_insight_feedback_tokens_bind_verdict_time_secret_and_domain():
    for verdict in ("useful", "not_now", "never"):
        token = mint_insight_feedback_token(
            insight_id=ID, verdict=verdict, created_at=NOW, secret=SECRET
        )
        verified = verify_insight_feedback_token(token, created_at=NOW, secret=SECRET)
        assert len(token.encode()) == 60
        assert (verified.insight_id, verified.verdict) == (ID, verdict)
        assert (
            verify_insight_feedback_token(
                token, created_at=NOW + timedelta(microseconds=1), secret=SECRET
            )
            is None
        )
        assert verify_insight_feedback_token(token, created_at=NOW, secret="wrong") is None
        assert verify_approval_callback_token(token, requested_at=NOW, secret=SECRET) is None
        assert verify_decision_callback_token(token, created_at=NOW, secret=SECRET) is None
        raw_id, _, signature = token.split(":")[1:]
        assert (
            verify_approval_callback_token(
                f"apr1:{raw_id}:a:{signature}", requested_at=NOW, secret=SECRET
            )
            is None
        )
        assert (
            verify_decision_callback_token(
                f"dsk1:{raw_id}:0:{signature}", created_at=NOW, secret=SECRET
            )
            is None
        )
    approval = mint_approval_callback_token(action_id=ID, verb="a", requested_at=NOW, secret=SECRET)
    decision = mint_decision_callback_token(
        prompt_id=ID, option_index=0, created_at=NOW, secret=SECRET
    )
    for foreign in (approval, decision):
        assert verify_insight_feedback_token(foreign, created_at=NOW, secret=SECRET) is None
        forged = "ifb1:" + foreign.split(":", 1)[1].replace(":a:", ":u:").replace(":0:", ":u:")
        assert parse_insight_feedback_token(forged) is not None
        assert verify_insight_feedback_token(forged, created_at=NOW, secret=SECRET) is None
    token = mint_insight_feedback_token(
        insight_id=ID, verdict="useful", created_at=NOW, secret=SECRET
    )
    for hostile in (
        None,
        "ifb1:\ud800",
        token + "x",
        token.replace(":u:", ":z:"),
        token.replace(str(ID), ID.hex),
        token.replace(":u:", ":x:"),
    ):
        assert verify_insight_feedback_token(hostile, created_at=NOW, secret=SECRET) is None

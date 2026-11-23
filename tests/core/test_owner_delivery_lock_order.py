"""Every owner-delivery writer locks the subject row before its presentations or tokens.

Two writers that take one subject's rows in opposite orders can deadlock (record_send used
to lock presentation, then subject; redeem locks subject first). This pins the order on the
real functions by recording the SQL they issue; the PostgreSQL concurrency proof lives in
``tests/integration/test_owner_delivery_ledger.py``.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from butlers.core import owner_delivery as od

pytestmark = pytest.mark.unit

_SUBJECT = uuid.uuid4()
_PRESENTATION = uuid.uuid4()
_OWNER = uuid.uuid4()
_DIGEST = "c" * 64
_LOCKS_CHILD = re.compile(
    r"(FOR UPDATE.*|^UPDATE\s+)public\.owner_delivery_(presentations|tokens)"
    r"|public\.owner_delivery_(presentations|tokens)\b.*FOR UPDATE",
    re.S,
)
_LOCKS_SUBJECT = re.compile(
    r"^(SELECT|UPDATE).*public\.owner_delivery_subjects.*(FOR UPDATE)?", re.S
)


class _Recorder:
    """Minimal asyncpg double returning a consistent subject/presentation/token."""

    def __init__(self, token_verifier: str | None = None) -> None:
        self.sql: list[str] = []
        self.verifier = token_verifier
        now = datetime.now(UTC)
        self.subject = {
            "id": _SUBJECT,
            "kind": "approval",
            "owning_schema": "general",
            "here": "general",
            "source_ref": "src",
            "owner_entity": _OWNER,
            "content_digest": _DIGEST,
            "action_digest": _DIGEST,
            "generation": 1,
            "state": "delivered",
        }
        self.presentation = {
            "id": _PRESENTATION,
            "subject_id": _SUBJECT,
            "generation": 1,
            "surface": "telegram",
            "route_ref": "9001",
            "provider_ref": None,
            "state": "unknown",
        }
        self.token = {
            "verifier": token_verifier,
            "subject_id": _SUBJECT,
            "presentation_id": _PRESENTATION,
            "action": "reject",
            "action_digest": _DIGEST,
            "owner_entity": _OWNER,
            "surface": "telegram",
            "generation": 1,
            "expires_at": now + timedelta(hours=1),
            "consumed_at": None,
            "outcome": None,
        }

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.sql.append(sql)
        if "subject_id FROM public.owner_delivery" in sql:
            return _SUBJECT
        if "clock_timestamp" in sql:
            return datetime.now(UTC)
        return None

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.sql.append(sql)
        if "FROM public.owner_delivery_subjects" in sql:
            return self.subject
        if "FROM public.owner_delivery_presentations" in sql:
            return self.presentation
        if "FROM public.owner_delivery_tokens" in sql:
            return self.token
        return None

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self.sql.append(sql)
        return []

    async def execute(self, sql: str, *args: Any) -> str:
        self.sql.append(sql)
        return "UPDATE 1"

    def assert_subject_locked_first(self) -> None:
        subject_lock = next(
            i
            for i, sql in enumerate(self.sql)
            if "owner_delivery_subjects" in sql and "FOR UPDATE" in sql
        )
        child = [i for i, sql in enumerate(self.sql) if _LOCKS_CHILD.search(sql)]
        assert child, self.sql
        assert subject_lock < min(child), self.sql


async def test_begin_send_locks_subject_first() -> None:
    conn = _Recorder()
    await od.begin_send(conn, _PRESENTATION)
    conn.assert_subject_locked_first()


@pytest.mark.parametrize("outcome", ["sent", "failed", "unknown"])
async def test_record_send_locks_subject_first(outcome: str) -> None:
    conn = _Recorder()
    await od.record_send(conn, _PRESENTATION, outcome=outcome, provider_ref="55")  # type: ignore[arg-type]
    conn.assert_subject_locked_first()


async def test_record_retraction_locks_subject_first() -> None:
    conn = _Recorder()
    conn.subject["state"] = "decided"
    await od.record_retraction(conn, _PRESENTATION, retracted=True)
    conn.assert_subject_locked_first()


async def test_redeem_locks_subject_before_its_token() -> None:
    token, verifier = od.mint_token()
    conn = _Recorder(verifier)

    async def decide(_conn: Any, _verb: str) -> dict[str, Any]:
        return {}

    outcome = await od.redeem(
        conn,
        token,
        kind="approval",
        source_ref="src",
        expected_action="reject",
        owner_entity=_OWNER,
        surface="telegram",
        current_action_digest=_DIGEST,
        decide=decide,
    )
    assert outcome.outcome == "won"
    conn.assert_subject_locked_first()


async def test_record_retraction_refuses_a_live_card() -> None:
    conn = _Recorder()
    conn.presentation["state"] = "sent"
    with pytest.raises(od.OwnerDeliveryRefused, match="presentation_live"):
        await od.record_retraction(conn, _PRESENTATION, retracted=True)
    assert not any(sql.startswith("UPDATE") for sql in conn.sql)


async def test_redeem_refuses_a_token_for_another_action() -> None:
    token, verifier = od.mint_token()
    conn = _Recorder(verifier)
    conn.token["action"] = "ack"
    called: list[str] = []

    async def decide(_conn: Any, verb: str) -> dict[str, Any]:
        called.append(verb)
        return {}

    outcome = await od.redeem(
        conn,
        token,
        kind="approval",
        source_ref="src",
        expected_action="approve",
        owner_entity=_OWNER,
        surface="telegram",
        current_action_digest=_DIGEST,
        decide=decide,
    )
    assert (outcome.outcome, outcome.reason) == ("refused", "action_mismatch")
    assert called == []
    assert not any("UPDATE public.owner_delivery_subjects" in sql for sql in conn.sql)


def test_lock_order_is_declared() -> None:
    assert od.LOCK_ORDER[:2] == ("authority", "subject")
    assert _LOCKS_SUBJECT.search("SELECT * FROM public.owner_delivery_subjects FOR UPDATE")

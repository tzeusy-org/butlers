"""Canonical owner-delivery token and card seams that need no database (bu-dhloi1.13)."""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.core import owner_delivery as od
from butlers.core.approval_delivery_worker import (
    ApprovalDeliveryWorker,
    DeliveryClaim,
    HandoffResult,
)
from butlers.modules.approvals.notifications import build_approval_request_envelope

pytestmark = pytest.mark.unit


def test_minted_token_fits_telegram_and_stores_only_its_verifier() -> None:
    token, verifier = od.mint_token()
    assert token.startswith("od1:")
    assert len(token.encode()) <= 64  # Telegram callback_data limit
    nonce = base64.urlsafe_b64decode(token.split(":", 1)[1] + "=")
    assert len(nonce) == 32
    assert verifier == hashlib.sha256(nonce).hexdigest()
    assert token not in verifier and token.split(":", 1)[1] not in verifier
    assert od.token_verifier(token) == verifier


def test_tokens_are_unpredictable() -> None:
    assert len({od.mint_token()[0] for _ in range(64)}) == 64


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        42,
        "",
        "od1:",
        "apr1:12345678-1234-5678-1234-567812345678:a:0123456789abcdef",
        "od2:" + "A" * 43,
        "od1:" + "A" * 42,
        "od1:" + "A" * 44,
        "od1:" + "!" * 43,
        # Same length, but the trailing character carries non-zero padding bits: a
        # second spelling of one nonce is refused, so a nonce has exactly one token.
        "od1:" + "A" * 42 + "B",
    ],
)
def test_malformed_tokens_have_no_verifier(candidate: object) -> None:
    assert od.token_verifier(candidate) is None


def test_digests_are_canonical_and_compared_in_constant_time() -> None:
    assert od.digest_of({"b": 1, "a": [1, 2]}) == od.digest_of({"a": [1, 2], "b": 1})
    assert od.digest_of({"a": 1}) != od.digest_of({"a": 2})
    digest = od.digest_of({"a": 1})
    assert od._same_digest(digest, digest)
    assert not od._same_digest(digest, od.digest_of({"a": 2}))
    assert not od._same_digest(digest, None)


def _action() -> dict[str, Any]:
    now = datetime(2026, 11, 6, 8, 0, tzinfo=UTC)
    return {
        "id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
        "tool_name": "telegram_send_message",
        "tool_args": {"chat_id": "42", "text": "hello"},
        "requested_at": now,
        "expires_at": now + timedelta(hours=72),
        "why": "Owner asked for it.",
        "blast_radius": "contact",
        "reversibility": "compensable",
    }


def _decision_tokens(envelope: dict[str, Any]) -> dict[str, str]:
    return {
        action["verb"]: action["callback_token"]
        for action in envelope["actions"]
        if action["verb"] in ("approve", "reject")
    }


def test_card_carries_owner_delivery_tokens_instead_of_legacy_hmac() -> None:
    approve, _ = od.mint_token()
    reject, _ = od.mint_token()
    envelope = build_approval_request_envelope(
        action=_action(),
        origin_butler="relationship",
        owner_recipient="9001",
        callback_secret="legacy-secret",
        owner_tokens={"approve": approve, "reject": reject},
    )
    assert _decision_tokens(envelope) == {"approve": approve, "reject": reject}


def test_card_without_mintable_tokens_offers_open_only() -> None:
    """A presentation already handed off yields no tokens: never fall back to apr1."""
    envelope = build_approval_request_envelope(
        action=_action(),
        origin_butler="relationship",
        owner_recipient="9001",
        callback_secret="legacy-secret",
        owner_tokens={},
    )
    assert _decision_tokens(envelope) == {}
    assert [action["verb"] for action in envelope["actions"]] == ["open_dashboard"]


def test_legacy_card_unchanged_while_owner_delivery_is_off() -> None:
    envelope = build_approval_request_envelope(
        action=_action(),
        origin_butler="relationship",
        owner_recipient="9001",
        callback_secret="legacy-secret",
    )
    assert all(token.startswith("apr1:") for token in _decision_tokens(envelope).values())


class _Repository:
    def __init__(self, tokens: dict[str, str] | None, *, define_prepare: bool = True) -> None:
        self.claim = DeliveryClaim(
            presentation_id=uuid.uuid4(),
            presentation_generation=1,
            presentation_key="key",
            presentation_mode="single",
            subject_kind="action",
            subject_key="action:key",
            claim_token=uuid.uuid4(),
            claim_fence=1,
            reconcile_only=False,
        )
        self.cancel_ineligible_presentations = AsyncMock(return_value=0)
        self.claim_next = AsyncMock(return_value=self.claim)
        self.load_render_subject = AsyncMock(return_value=object())
        self.record_prestart_retry = AsyncMock(return_value=True)
        self.mark_handoff_started = AsyncMock(return_value=True)
        self.heartbeat = AsyncMock(return_value=True)
        self.complete_handoff = AsyncMock(return_value=True)
        if define_prepare:
            self.prepare_owner_delivery = AsyncMock(return_value=tokens)


class _Renderer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def render_single(self, subject: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"envelope": True}

    def render_digest(self, subject: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("single presentation only")


class _Runtime:
    resolve_verified_owner_recipient = AsyncMock(return_value="9001")
    resolve_callback_secret = AsyncMock(return_value="secret")
    handoff = AsyncMock(return_value=HandoffResult("confirmed", provider_reference="77"))
    reconcile = AsyncMock()


async def test_worker_renders_the_owner_tokens_the_repository_prepared() -> None:
    repository = _Repository({"approve": "od1:a", "reject": "od1:r"})
    renderer = _Renderer()
    worker = ApprovalDeliveryWorker(repository, renderer, _Runtime())
    assert await worker.process_one() is True
    repository.prepare_owner_delivery.assert_awaited_once_with(repository.claim, "9001")
    assert renderer.calls[0]["owner_tokens"] == {"approve": "od1:a", "reject": "od1:r"}
    repository.mark_handoff_started.assert_awaited_once()


@pytest.mark.parametrize("define_prepare", [True, False])
async def test_worker_keeps_the_legacy_render_while_owner_delivery_is_off(
    define_prepare: bool,
) -> None:
    repository = _Repository(None, define_prepare=define_prepare)
    renderer = _Renderer()
    worker = ApprovalDeliveryWorker(repository, renderer, _Runtime())
    assert await worker.process_one() is True
    assert "owner_tokens" not in renderer.calls[0]


async def test_worker_retries_before_handoff_when_owner_delivery_refuses() -> None:
    repository = _Repository(None)
    repository.prepare_owner_delivery.side_effect = od.OwnerDeliveryRefused("owner_unavailable")
    worker = ApprovalDeliveryWorker(repository, _Renderer(), _Runtime())
    assert await worker.process_one() is True
    repository.record_prestart_retry.assert_awaited_once_with(
        repository.claim, "provider_preflight_failed"
    )
    repository.mark_handoff_started.assert_not_awaited()


def test_a_presentations_repr_never_shows_its_tokens() -> None:
    """A dataclass repr reaches logs and tracebacks; the raw token must not."""
    presentation = od.Presentation(
        uuid.uuid4(), uuid.uuid4(), 1, "dashboard", "sent", {"approve": "od1:synthetic-secret"}
    )

    assert "synthetic-secret" not in repr(presentation)
    assert presentation.tokens == {"approve": "od1:synthetic-secret"}

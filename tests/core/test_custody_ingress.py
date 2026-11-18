"""Endpoint custody at ingress (bu-s11n0s.1 slice 3a, REQ-endpoint-custody-holds-005)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from butlers.core import break_through
from butlers.core.custody_ingress import owner_source_custody_reason, owner_source_targets
from butlers.identity import ResolvedContact
from butlers.tools.switchboard.identity.inject import (
    resolve_and_inject_identity,
    resolve_sender_identities,
)
from butlers.tools.switchboard.routing.contracts import RouteRequestContextV1

pytestmark = pytest.mark.unit

OWNER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class FakeCustodyPool:
    """Answers ``custody_target_held`` from held targets; records every lookup."""

    def __init__(self, held=(), *, error: bool = False) -> None:
        self.held = set(held)
        self.error = error
        self.lookups: list[list[tuple[str, str]]] = []

    async def fetchval(self, sql, predicates, objects):
        assert "public.custody_target_held" in sql
        targets = list(zip(predicates, objects, strict=True))
        self.lookups.append(targets)
        if self.error:
            raise ConnectionError("database unavailable")
        return any(target in self.held for target in targets)


def _owner() -> ResolvedContact:
    return ResolvedContact(name="Owner", roles=["owner"], entity_id=OWNER_ID)


def test_owner_targets_use_the_definer_candidate_universe() -> None:
    assert ("has-handle", "telegram:111") in owner_source_targets("telegram", "111")
    assert owner_source_targets("email", "Me@Example.test") == [("has-email", "me@example.test")]
    whatsapp = owner_source_targets("whatsapp", "6587650123@s.whatsapp.net")
    assert whatsapp is not None
    assert ("has-handle", "6587650123@s.whatsapp.net") in whatsapp
    assert ("has-phone", "87650123") in whatsapp
    # A channel outside owner-channel authorization names no owner endpoint.
    assert owner_source_targets("linkedin", "someone") is None


@pytest.mark.parametrize(
    ("pool", "channel", "value", "expected"),
    [
        (FakeCustodyPool({("has-handle", "telegram:111")}), "telegram", "111", "endpoint_held"),
        (FakeCustodyPool(), "telegram", "111", None),
        (FakeCustodyPool(error=True), "telegram", "111", "endpoint_held"),
        (None, "telegram", "111", "endpoint_held"),
        (FakeCustodyPool(), "linkedin", "someone", "endpoint_held"),
        (FakeCustodyPool(), "telegram", None, "endpoint_held"),
    ],
    ids=["held", "unheld", "lookup-error", "no-pool", "unnameable-channel", "no-sender"],
)
async def test_custody_reason_fails_closed(pool, channel, value, expected) -> None:
    assert await owner_source_custody_reason(pool, channel, value) == expected


@pytest.mark.parametrize(
    ("pool", "expected"),
    [
        (FakeCustodyPool({("has-handle", "telegram:111")}), "endpoint_held"),
        (FakeCustodyPool(error=True), "endpoint_held"),
        (FakeCustodyPool(), None),
    ],
    ids=["held", "lookup-error", "unheld"],
)
async def test_owner_resolution_keeps_attribution_and_flags_custody(pool, expected) -> None:
    resolver = AsyncMock(return_value=_owner())
    result = await resolve_and_inject_identity(pool, "telegram", "111", resolver=resolver)
    # Attribution survives: the entity stays the owner, no duplicate is minted.
    assert (result.entity_id, result.is_owner) == (OWNER_ID, True)
    assert result.custody_reason == expected


async def test_non_owner_resolution_asks_no_custody() -> None:
    pool = FakeCustodyPool(error=True)
    friend = ResolvedContact(name="Chloe", roles=[], entity_id=uuid.uuid4())
    result = await resolve_and_inject_identity(
        pool, "telegram", "222", resolver=AsyncMock(return_value=friend)
    )
    assert result.custody_reason is None
    assert pool.lookups == []


async def test_batch_owner_speaker_is_flagged() -> None:
    pool = FakeCustodyPool({("has-handle", "telegram:111")})
    bulk = AsyncMock(return_value={"111": _owner(), "222": None})
    with patch(
        "butlers.tools.switchboard.identity.inject._inject_unknown_identity", AsyncMock()
    ) as unknown:
        unknown.return_value.custody_reason = None
        results = await resolve_sender_identities(
            pool, "telegram", ["111", "222"], bulk_resolver=bulk
        )
    assert results["111"].custody_reason == "endpoint_held"


def _request_context(**extra) -> dict:
    return {
        "request_id": "0190d7a2-6b3c-7cc0-8a51-1b2c3d4e5f60",
        "received_at": "2026-11-19T00:00:00+00:00",
        "source_channel": "telegram_bot",
        "source_endpoint_identity": "switchboard",
        "source_sender_identity": "111",
        **extra,
    }


def test_route_context_admits_only_the_closed_custody_value() -> None:
    parsed = RouteRequestContextV1.model_validate(
        _request_context(
            source_sender_entity_id=str(OWNER_ID), source_sender_custody="endpoint_held"
        )
    )
    assert parsed.source_sender_custody == "endpoint_held"
    assert RouteRequestContextV1.model_validate(_request_context()).source_sender_custody is None
    with pytest.raises(ValidationError):
        RouteRequestContextV1.model_validate(_request_context(source_sender_custody="owner"))


@pytest.mark.parametrize(
    ("custody", "expected"),
    [("endpoint_held", (None, None)), (None, (OWNER_ID, "Owner"))],
    ids=["held-narrows", "unheld-unchanged"],
)
async def test_break_through_only_narrows_for_a_held_owner(custody, expected) -> None:
    with (
        patch("butlers.identity.resolve_contact_by_channel", AsyncMock(return_value=_owner())),
        patch(
            "butlers.core.custody_ingress.owner_source_custody_reason",
            AsyncMock(return_value=custody),
        ),
    ):
        assert await break_through._resolve_sender(object(), "telegram", "111") == expected

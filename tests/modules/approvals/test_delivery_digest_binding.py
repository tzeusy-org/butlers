"""The rendered-call digest cache is bounded and a card never records a NULL digest."""

from __future__ import annotations

import importlib
import uuid
from typing import Any

import pytest

from butlers.core.approval_delivery_worker import DeliveryClaim
from butlers.modules.approvals.delivery_recovery import ApprovalDeliveryRepository
from butlers.modules.approvals.previews import args_digest

pytestmark = pytest.mark.unit

_ARGS = {"to": "dana@x.com", "subject": "Hi", "body": "Hello"}


class _Pool:
    async def fetchrow(self, *_args: Any) -> dict[str, Any]:
        return {
            "presentation_mode": "single",
            "origin_butler": "general",
            "id": uuid.uuid4(),
            "tool_name": "email_send_message",
            "requested_at": None,
            "expires_at": None,
            "why": "w",
            "blast_radius": "contact",
            "reversibility": "irreversible",
            "tool_args": _ARGS,
            "origin": None,
        }


def _claim(presentation_id: Any, fence: int, mode: str = "single") -> DeliveryClaim:
    return DeliveryClaim(
        presentation_id=presentation_id,
        presentation_generation=1,
        presentation_key="k",
        presentation_mode=mode,  # type: ignore[arg-type]
        subject_kind="action",
        subject_key="s",
        claim_token=uuid.uuid4(),
        claim_fence=fence,
        reconcile_only=False,
    )


async def test_reclaiming_a_presentation_supersedes_its_abandoned_render() -> None:
    repository = ApprovalDeliveryRepository(_Pool())
    presentation_id = uuid.uuid4()

    await repository.load_render_subject(_claim(presentation_id, 1))
    await repository.load_render_subject(_claim(presentation_id, 2))

    assert list(repository._rendered_digests.values()) == [args_digest(_ARGS)]
    assert len(repository._rendered_digests) == 1


async def test_digest_presentations_cache_no_call_digest() -> None:
    repository = ApprovalDeliveryRepository(_Pool())

    await repository.load_render_subject(_claim(uuid.uuid4(), 1, mode="burst_digest"))

    assert repository._rendered_digests == {}


def test_018_downgrade_refuses_while_a_presentation_digest_exists(monkeypatch) -> None:
    migration = importlib.import_module(
        "butlers.modules.approvals.migrations.018_presentation_args_digest"
    )
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append, raising=False)

    migration.downgrade()

    guard, drop = statements
    assert "WHERE args_digest IS NOT NULL" in guard
    assert "Cannot downgrade approvals_018" in guard
    assert drop.strip().startswith("ALTER TABLE approval_delivery_presentations DROP COLUMN")

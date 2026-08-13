"""Static contract for RFC 0023 recovery-presentation history isolation.

RFC 0023 is an OpenSpec-only planning packet.  This test keeps its future
implementation plan explicit about the existing Switchboard outbound-history
path, so a recovery-only approval presentation cannot silently become generic
conversation or LLM history.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).parent.parent.parent
_RFC = _REPO_ROOT / "about/legends-and-lore/rfcs/0023-durable-approval-delivery-intent-recovery.md"
_CORE_NOTIFY_DELTA = (
    _REPO_ROOT
    / "openspec/changes/durable-approval-delivery-intent-recovery/specs/core-notify/spec.md"
)
_IMPLEMENTATION_PLAN = (
    _REPO_ROOT / "docs/superpowers/plans/2026-08-13-durable-approval-delivery-intent-recovery.md"
)
_TASKS = _REPO_ROOT / "openspec/changes/durable-approval-delivery-intent-recovery/tasks.md"


def _read(path: Path) -> str:
    assert path.is_file(), f"required RFC 0023 artifact is missing: {path}"
    return path.read_text(encoding="utf-8")


def _normalise_whitespace(text: str) -> str:
    return " ".join(text.split())


def test_recovery_notify_contract_excludes_message_inbox_and_generic_history() -> None:
    """Recovery-mode ``notify.v1`` must bypass the generic outbound-history path."""
    text = _normalise_whitespace(_read(_CORE_NOTIFY_DELTA))

    required_contract_terms = (
        "switchboard.message_inbox",
        "MUST NOT call `_write_outbound_message_inbox()`",
        "MUST NOT insert an outbound `switchboard.message_inbox` row",
        "generic conversation-history",
        "LLM-history",
        "All generic conversation/LLM-history readers SHALL be unable to retrieve recovery",
        "rendered message",
        "recipient-derived thread identity",
        "callback material",
    )

    for term in required_contract_terms:
        assert term in text, (
            "RFC 0023 core-notify delta must explicitly isolate recovery-mode "
            f"approval presentation data from generic history: missing {term!r}"
        )


def test_recovery_plan_requires_negative_persistence_and_history_proof() -> None:
    """The future real-PostgreSQL test must prove no generic-history exposure."""
    combined_plan = _normalise_whitespace(
        "\n".join((_read(_RFC), _read(_IMPLEMENTATION_PLAN), _read(_TASKS)))
    )

    required_plan_terms = (
        "negative integration test",
        "rendered-text sentinel",
        "recipient-derived thread-identity sentinel",
        "callback-material sentinel",
        "_load_realtime_history",
        "_load_email_history",
        "_load_conversation_history",
        "no `switchboard.message_inbox` row",
    )

    for term in required_plan_terms:
        assert term in combined_plan, (
            "RFC 0023 implementation packet must require the negative recovery "
            f"persistence/history proof: missing {term!r}"
        )

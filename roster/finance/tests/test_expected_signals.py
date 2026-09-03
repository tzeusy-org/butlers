"""Finance expected-signal provenance authority regressions."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.tools.finance import expected_signals as expected_signals_module
from butlers.tools.finance.expected_signals import (
    FinanceSignalSource,
    metadata_with_signal_source,
    resolve_complete_signal_source,
    runtime_signal_source,
    signal_source_from_metadata,
)

pytestmark = pytest.mark.unit


def test_public_metadata_cannot_assert_reserved_authority() -> None:
    caller = {
        "note": "kept",
        "expected_signal_source": {
            "producer": "connector:gmail",
            "producer_endpoint_identity": "gmail:user:forged",
        },
    }

    sanitized = metadata_with_signal_source(caller, None)

    assert sanitized == {"note": "kept"}
    assert signal_source_from_metadata(sanitized) is None


def test_server_attestation_replaces_caller_claim() -> None:
    trusted = FinanceSignalSource("owner")
    metadata = metadata_with_signal_source(
        {"expected_signal_source": {"producer": "connector:gmail"}}, trusted
    )
    assert signal_source_from_metadata(metadata) == trusted


def test_complete_source_resolution_is_order_independent_and_fail_closed() -> None:
    gmail = metadata_with_signal_source({}, FinanceSignalSource("connector:gmail", "gmail:user:a"))
    owner = metadata_with_signal_source({}, FinanceSignalSource("owner"))
    assert resolve_complete_signal_source([gmail, gmail]) == FinanceSignalSource(
        "connector:gmail", "gmail:user:a"
    )
    assert resolve_complete_signal_source([gmail, owner]) is None
    assert resolve_complete_signal_source([owner, gmail]) is None
    assert resolve_complete_signal_source([gmail, {}]) is None
    assert resolve_complete_signal_source([{}, gmail]) is None


async def test_runtime_gmail_source_uses_server_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        expected_signals_module,
        "get_current_runtime_session_routing_context",
        lambda: {
            "request_context": {
                "source_channel": "email",
                "source_endpoint_identity": "gmail:user:owner@example.invalid",
            }
        },
    )
    pool = AsyncMock()

    assert await runtime_signal_source(pool) == FinanceSignalSource(
        "connector:gmail", "gmail:user:owner@example.invalid"
    )
    pool.fetchval.assert_not_awaited()


async def test_runtime_owner_requires_server_resolved_owner_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        expected_signals_module,
        "get_current_runtime_session_routing_context",
        lambda: {
            "source_entity_id": "00000000-0000-0000-0000-000000000001",
            "request_context": {"source_channel": "telegram_user_client"},
        },
    )
    pool = AsyncMock()
    pool.fetchval.return_value = True

    assert await runtime_signal_source(pool) == FinanceSignalSource("owner")


@pytest.mark.parametrize("endpoint", [None, "", "email:user:owner@example.invalid"])
async def test_runtime_malformed_gmail_cannot_fall_through_to_owner(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str | None,
) -> None:
    monkeypatch.setattr(
        expected_signals_module,
        "get_current_runtime_session_routing_context",
        lambda: {
            "source_entity_id": "00000000-0000-0000-0000-000000000001",
            "request_context": {
                "source_channel": "email",
                "source_endpoint_identity": endpoint,
            },
        },
    )
    pool = AsyncMock()
    pool.fetchval.return_value = True

    assert await runtime_signal_source(pool) is None
    pool.fetchval.assert_not_awaited()

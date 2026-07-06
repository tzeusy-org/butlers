"""Tests for DiscretionDispatcher auth-health visibility (bu-ur7go).

From bu-ofo3i's diagnosis: connector /status only checked bridge/socket
connectivity, never whether the discretion runtime (e.g. codex) had a valid
auth file — a never-provisioned ``~/.codex/auth.json`` in standalone
connector containers caused every discretion call to 401, and weight<0.5
senders were silently dropped as IGNORE for weeks with zero visibility.

Covers:
- ``get_auth_health()`` before any call has been attempted reports
  ``status="unknown"`` rather than fabricating "ok".
- A provider/auth-classified failure (as detected by the shared
  ``classify_failover_eligibility``) is recorded regardless of same-tier
  failover eligibility, and increments ``discretion_auth_failures_total``.
- ``get_auth_health()`` reflects on-disk auth-file presence/absence for the
  runtime last attempted, without querying the DB or spawning a subprocess.
- Runtimes with no matching on-disk CLI auth artifact (api_key mode, e.g.
  ``claude``) report ``auth_file_present=None`` rather than a false
  "missing file" degradation.
- A later successful call recovers ``status`` back to "ok".
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.discretion_dispatcher import (
    DiscretionDispatcher,
    discretion_auth_failures_total,
)
from butlers.core.model_routing import QuotaStatus

pytestmark = pytest.mark.unit

_MODULE = "butlers.connectors.discretion_dispatcher"


def _allowed_quota() -> QuotaStatus:
    return QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)


def _make_adapter(side_effect: list[object]) -> MagicMock:
    adapter = MagicMock()
    adapter.invoke = AsyncMock(side_effect=side_effect)
    adapter.last_process_info = None
    return adapter


# ---------------------------------------------------------------------------
# Baseline: nothing attempted yet
# ---------------------------------------------------------------------------


def test_get_auth_health_before_any_call_is_unknown() -> None:
    """A freshly constructed dispatcher has not resolved a runtime yet — the
    snapshot must say "unknown", not fabricate "ok" for an idle connector.
    """
    dispatcher = DiscretionDispatcher(pool=MagicMock())

    health = dispatcher.get_auth_health()

    assert health == {
        "runtime_type": None,
        "auth_file_present": None,
        "last_discretion_success_at": None,
        "last_auth_failure_at": None,
        "status": "unknown",
    }


# ---------------------------------------------------------------------------
# Successful call -> ok, runtime_type recorded
# ---------------------------------------------------------------------------


async def test_get_auth_health_ok_after_successful_call_with_no_registered_provider() -> None:
    """A successful call records runtime_type + last_discretion_success_at.

    The "api" runtime has no registered CLI auth provider, so
    auth_file_present stays None (not applicable) rather than False.
    """
    dispatcher = DiscretionDispatcher(pool=MagicMock())
    catalog = ("api", "claude-haiku-4-5-20251001", [], uuid.uuid4(), 30, "specialty")
    adapter = _make_adapter(side_effect=[("FORWARD", [], {"input_tokens": 5, "output_tokens": 2})])

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()),
    ):
        result = await dispatcher.call("hi")

    assert result == "FORWARD"

    health = dispatcher.get_auth_health()
    assert health["runtime_type"] == "api"
    assert health["auth_file_present"] is None
    assert health["last_auth_failure_at"] is None
    assert health["last_discretion_success_at"] is not None
    assert health["status"] == "ok"


# ---------------------------------------------------------------------------
# Provider/auth-classified failure -> recorded + metric, regardless of
# same-tier failover eligibility
# ---------------------------------------------------------------------------


async def test_call_records_auth_failure_and_increments_metric() -> None:
    """A 401-style RuntimeError classified as provider_auth_error by the
    shared classifier must set last_auth_failure_at, flip status to
    "degraded", and increment discretion_auth_failures_total — even though
    the same failure is *also* eligible for same-tier failover (it's not
    an either/or: auth-health tracking is independent of the retry path).
    """
    dispatcher = DiscretionDispatcher(pool=MagicMock())
    catalog = ("codex", "gpt-5-codex", [], uuid.uuid4(), 30, "specialty")
    adapter = _make_adapter(
        side_effect=[
            RuntimeError("Codex CLI exited with code 1: unexpected status 401 Unauthorized")
        ]
    )

    before = discretion_auth_failures_total.labels(runtime_type="codex")._value.get()

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.next_same_tier_candidate", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()),
    ):
        with pytest.raises(RuntimeError, match="same_tier_failover_exhausted"):
            await dispatcher.call("hi")

    after = discretion_auth_failures_total.labels(runtime_type="codex")._value.get()
    assert after == before + 1

    health = dispatcher.get_auth_health()
    assert health["runtime_type"] == "codex"
    assert health["last_auth_failure_at"] is not None
    assert health["status"] == "degraded"


async def test_call_does_not_record_auth_failure_for_availability_error() -> None:
    """bu-ujm9d: a connectivity/availability failure (connection refused,
    service unavailable, etc.) is failover-eligible via the classifier's
    ``provider_unavailable`` bucket, but must NOT flip auth-health to
    "degraded" or increment ``discretion_auth_failures_total`` — those are
    reserved for the genuine ``provider_auth_error`` bucket. Before the
    marker split, this exact message collapsed into the auth bucket and would
    have falsely reported a healthy-but-unreachable provider as an auth
    failure.
    """
    dispatcher = DiscretionDispatcher(pool=MagicMock())
    catalog = ("codex", "gpt-5-codex", [], uuid.uuid4(), 30, "specialty")
    adapter = _make_adapter(
        side_effect=[RuntimeError("Connection refused: could not reach provider")]
    )

    before = discretion_auth_failures_total.labels(runtime_type="codex")._value.get()

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.next_same_tier_candidate", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()),
    ):
        with pytest.raises(RuntimeError, match="same_tier_failover_exhausted"):
            await dispatcher.call("hi")

    after = discretion_auth_failures_total.labels(runtime_type="codex")._value.get()
    assert after == before

    health = dispatcher.get_auth_health()
    assert health["last_auth_failure_at"] is None


async def test_call_does_not_record_auth_failure_for_unrelated_error() -> None:
    """A non-auth business error must not be misclassified as an auth failure
    or increment discretion_auth_failures_total.
    """
    dispatcher = DiscretionDispatcher(pool=MagicMock())
    catalog = ("codex", "gpt-5-codex", [], uuid.uuid4(), 30, "specialty")
    adapter = _make_adapter(side_effect=[ValueError("Unrecognisable discretion verdict: 'WAT'")])

    before = discretion_auth_failures_total.labels(runtime_type="codex")._value.get()

    with (
        patch(f"{_MODULE}.resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
        patch(f"{_MODULE}.check_token_quota", AsyncMock(return_value=_allowed_quota())),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
        patch(f"{_MODULE}.record_token_usage", AsyncMock()),
    ):
        with pytest.raises(ValueError, match="Unrecognisable"):
            await dispatcher.call("hi")

    after = discretion_auth_failures_total.labels(runtime_type="codex")._value.get()
    assert after == before

    health = dispatcher.get_auth_health()
    assert health["last_auth_failure_at"] is None


# ---------------------------------------------------------------------------
# On-disk auth-file presence
# ---------------------------------------------------------------------------


def test_get_auth_health_reports_missing_auth_file(tmp_path) -> None:
    """When the resolved runtime has a registered device-code CLI auth
    provider whose token file is absent, auth_file_present is False and
    status is "degraded" — this is the exact bu-ofo3i failure mode (auth.json
    never provisioned in a standalone connector container).
    """
    dispatcher = DiscretionDispatcher(pool=MagicMock())
    dispatcher._last_runtime_type = "codex"

    fake_provider = MagicMock()
    fake_provider.token_path = tmp_path / "auth.json"  # does not exist

    with patch(f"{_MODULE}.providers_for_runtime", return_value=[fake_provider]):
        health = dispatcher.get_auth_health()

    assert health["auth_file_present"] is False
    assert health["status"] == "degraded"


def test_get_auth_health_reports_present_auth_file(tmp_path) -> None:
    """When the token file exists, auth_file_present is True and status is
    "ok" (absent any recorded auth failure).
    """
    dispatcher = DiscretionDispatcher(pool=MagicMock())
    dispatcher._last_runtime_type = "codex"

    token_path = tmp_path / "auth.json"
    token_path.write_text("{}")
    fake_provider = MagicMock()
    fake_provider.token_path = token_path

    with patch(f"{_MODULE}.providers_for_runtime", return_value=[fake_provider]):
        health = dispatcher.get_auth_health()

    assert health["auth_file_present"] is True
    assert health["status"] == "ok"


def test_get_auth_health_no_file_based_provider_is_not_applicable() -> None:
    """Runtimes authenticated purely via env var / credential store (e.g.
    "claude", an api_key-mode provider with no token_path) have nothing on
    disk to check — auth_file_present must be None, not a fabricated False.
    """
    dispatcher = DiscretionDispatcher(pool=MagicMock())
    dispatcher._last_runtime_type = "claude"  # real registry entry, no token_path

    health = dispatcher.get_auth_health()

    assert health["auth_file_present"] is None
    assert health["status"] == "ok"


# ---------------------------------------------------------------------------
# Recovery: a later success clears the degraded status
# ---------------------------------------------------------------------------


def test_get_auth_health_recovers_to_ok_after_later_success() -> None:
    """Once a success is recorded *after* the last auth failure, status
    returns to "ok" — a transient auth blip must not permanently pin the
    connector as degraded once credentials are fixed and a call succeeds.

    ``providers_for_runtime`` is patched to return no providers so this test
    isolates the success/failure-timestamp ordering logic from on-disk
    auth-file state (covered separately above) — otherwise this would read
    whatever ``~/.codex/auth.json`` happens to exist on the machine running
    the test, which is not hermetic.
    """
    dispatcher = DiscretionDispatcher(pool=MagicMock())
    dispatcher._last_runtime_type = "codex"

    now = time.time()
    dispatcher._last_auth_failure_at = now - 10

    with patch(f"{_MODULE}.providers_for_runtime", return_value=[]):
        assert dispatcher.get_auth_health()["status"] == "degraded"

        dispatcher._last_success_at = now
        health = dispatcher.get_auth_health()

    assert health["status"] == "ok"
    assert health["last_auth_failure_at"] is not None
    assert health["last_discretion_success_at"] is not None

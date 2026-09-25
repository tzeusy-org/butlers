"""Typed transport semantics for route() and deliver().

REQ-core-notify-027 requires that a confirmed Messenger send survives every
post-send bookkeeping failure, and that a proven not-attempted failure stays
distinguishable from an ambiguous one.  REQ-runtime-attention-outbox-002 builds
at-most-once delivery on exactly that distinction, and REQ-database-security-007
keeps the bookkeeping failure from widening anyone's database permissions.
"""

from __future__ import annotations

import asyncio
import errno
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.tools.switchboard.notification.deliver import deliver
from butlers.tools.switchboard.routing.route import route
from butlers.tools.switchboard.routing.transport import (
    TransportErrorClass,
    TransportErrorDetail,
    TransportNotAttempted,
    TransportOutcome,
    TransportRejected,
    TransportResult,
    TransportUncertain,
    classify_transport_exception,
    proves_transport_not_attempted,
    transport_result_from_envelope,
)

pytestmark = pytest.mark.unit

_TARGET_ROW = {"endpoint_url": "http://localhost:9999/mcp/"}


def _mock_pool(**overrides: Any) -> AsyncMock:
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)
    for name, value in overrides.items():
        setattr(pool, name, value)
    return pool


def _resolves_target() -> Any:
    return patch(
        "butlers.tools.switchboard.routing.route.resolve_routing_target",
        new=AsyncMock(return_value=(_TARGET_ROW, None)),
    )


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class TestTransportVocabulary:
    def test_only_not_attempted_is_retryable(self) -> None:
        transport = sys.modules["butlers.tools.switchboard.routing.transport"]

        assert transport.RECIPIENT_UNAVAILABLE.retryable is True
        assert transport.POLICY_DENIED.retryable is True
        assert transport.PROVIDER_REJECTED.retryable is False
        assert transport.TRANSPORT_TIMEOUT.retryable is False
        assert transport.TRANSPORT_CONNECTION_LOST.retryable is False
        assert transport.WORKER_RECOVERY.retryable is False
        assert transport.CONFIRMED.retryable is False

    def test_evidence_pairs_outside_the_database_allowlist_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            TransportResult(
                TransportOutcome.UNCERTAIN,
                TransportErrorClass.PRE_TRANSPORT,
                TransportErrorDetail.RECIPIENT_UNAVAILABLE,
            )

    def test_confirmed_transport_carries_no_failure_evidence(self) -> None:
        with pytest.raises(ValueError):
            TransportResult(
                TransportOutcome.CONFIRMED,
                TransportErrorClass.PRE_TRANSPORT,
                TransportErrorDetail.POLICY_DENIED,
            )

    def test_failure_outcome_requires_evidence(self) -> None:
        with pytest.raises(ValueError):
            TransportResult(TransportOutcome.UNCERTAIN)

    def test_refused_connect_proves_not_attempted_but_reset_does_not(self) -> None:
        refused = OSError(errno.ECONNREFUSED, "Connection refused")
        reset = OSError(errno.ECONNRESET, "Connection reset by peer")

        assert proves_transport_not_attempted(refused) is True
        assert proves_transport_not_attempted(reset) is False
        assert classify_transport_exception(reset) is not None
        assert classify_transport_exception(reset).outcome is TransportOutcome.UNCERTAIN, (
            "a mid-flight reset may have delivered the request"
        )

    def test_fastmcp_connect_wrapper_is_unwrapped(self) -> None:
        wrapper = RuntimeError("Client failed to connect: nope")
        wrapper.__cause__ = OSError(errno.ECONNREFUSED, "Connection refused")

        assert proves_transport_not_attempted(wrapper) is True
        assert classify_transport_exception(wrapper).outcome is TransportOutcome.NOT_ATTEMPTED

    def test_unknown_exception_classifies_as_uncertain(self) -> None:
        assert classify_transport_exception(ValueError("???")).outcome is (
            TransportOutcome.UNCERTAIN
        )

    def test_peer_error_and_timeout_are_distinct(self) -> None:
        assert classify_transport_exception(TransportRejected("bad args")).outcome is (
            TransportOutcome.REJECTED
        )
        assert classify_transport_exception(TimeoutError()).error_detail is (
            TransportErrorDetail.TRANSPORT_TIMEOUT
        )
        assert classify_transport_exception(TransportUncertain("empty")).error_detail is (
            TransportErrorDetail.TRANSPORT_CONNECTION_LOST
        )

    def test_envelope_roundtrip(self) -> None:
        transport = sys.modules["butlers.tools.switchboard.routing.transport"]

        for result in (
            transport.CONFIRMED,
            transport.RECIPIENT_UNAVAILABLE,
            transport.PROVIDER_REJECTED,
            transport.TRANSPORT_TIMEOUT,
        ):
            assert transport_result_from_envelope({"transport": result.as_dict()}) == result

    def test_legacy_envelope_has_no_transport_classification(self) -> None:
        assert transport_result_from_envelope({"error": "boom", "retryable": True}) is None


# ---------------------------------------------------------------------------
# route()
# ---------------------------------------------------------------------------


class TestRouteTransportClassification:
    async def test_default_route_retains_legacy_authority_without_cutover(
        self, monkeypatch
    ) -> None:
        monkeypatch.delenv("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER", raising=False)
        pool = _mock_pool()
        call_fn = AsyncMock(return_value={"status": "accepted"})
        with (
            _resolves_target(),
            patch(
                "butlers.tools.switchboard.routing.route.resolve_control_plane_target",
                new_callable=AsyncMock,
            ) as receiver_resolver,
        ):
            result = await route(pool, "finance", "route.execute", {}, call_fn=call_fn)

        assert result["transport"]["outcome"] == "confirmed"
        call_fn.assert_awaited_once()
        receiver_resolver.assert_not_awaited()

    async def test_flagged_stale_route_rechecks_once_without_policy_bypass(
        self, monkeypatch
    ) -> None:
        from butlers.core.control_plane_identity import ExpectedDaemon, ProbeOutcome
        from butlers.tools.switchboard.registry.registry import ControlPlaneTargetDecision

        monkeypatch.setenv("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER", "1")
        expected = ExpectedDaemon("finance", 41107, "localhost")
        stale = ControlPlaneTargetDecision(
            "stale", "stale_observation", expected, endpoint_url="http://localhost:41107/mcp"
        )
        ready = ControlPlaneTargetDecision(
            "ready", "ready", expected, endpoint_url="http://localhost:41107/mcp"
        )
        pool = _mock_pool()
        call_fn = AsyncMock(return_value={"status": "accepted"})
        with (
            patch(
                "butlers.tools.switchboard.routing.route.expected_route_target",
                return_value=expected,
            ),
            patch(
                "butlers.tools.switchboard.routing.route.resolve_control_plane_target",
                new=AsyncMock(side_effect=[stale, ready]),
            ) as receiver_resolver,
            patch(
                "butlers.tools.switchboard.routing.route.probe_once",
                new=AsyncMock(return_value=ProbeOutcome("healthy", True)),
            ) as probe,
            patch("butlers.tools.switchboard.routing.route.httpx.AsyncClient"),
        ):
            result = await route(pool, "finance", "route.execute", {}, call_fn=call_fn)

        assert result["transport"]["outcome"] == "confirmed"
        assert receiver_resolver.await_count == 2
        probe.assert_awaited_once()
        call_fn.assert_awaited_once()
        assert call_fn.await_args.args[0] == "http://localhost:41107/mcp"

        denied = ControlPlaneTargetDecision("denied", "policy_denied", expected)
        with (
            patch(
                "butlers.tools.switchboard.routing.route.expected_route_target",
                return_value=expected,
            ),
            patch(
                "butlers.tools.switchboard.routing.route.resolve_control_plane_target",
                new=AsyncMock(return_value=denied),
            ),
            patch(
                "butlers.tools.switchboard.routing.route.probe_once",
                new_callable=AsyncMock,
            ) as probe,
        ):
            refused = await route(
                pool,
                "finance",
                "route.execute",
                {},
                allow_stale=True,
                allow_quarantined=True,
                call_fn=call_fn,
            )
        assert refused["transport"]["outcome"] == "not_attempted"
        assert refused["retryable"] is False
        probe.assert_not_awaited()
        call_fn.assert_awaited_once()

        with (
            patch(
                "butlers.tools.switchboard.routing.route.expected_route_target",
                return_value=expected,
            ),
            patch(
                "butlers.tools.switchboard.routing.route.resolve_control_plane_target",
                new=AsyncMock(return_value=stale),
            ),
            patch(
                "butlers.tools.switchboard.routing.route.probe_once",
                new=AsyncMock(return_value=ProbeOutcome("timeout", False)),
            ) as failed_probe,
            patch("butlers.tools.switchboard.routing.route.httpx.AsyncClient"),
        ):
            unavailable = await route(pool, "finance", "route.execute", {}, call_fn=call_fn)
        assert unavailable["transport"]["outcome"] == "not_attempted"
        assert unavailable["error"] == "target_unavailable"
        failed_probe.assert_awaited_once()
        call_fn.assert_awaited_once()

        with (
            patch(
                "butlers.tools.switchboard.routing.route.expected_route_target",
                return_value=expected,
            ),
            patch(
                "butlers.tools.switchboard.routing.route.resolve_control_plane_target",
                new=AsyncMock(side_effect=[stale, denied]),
            ),
            patch(
                "butlers.tools.switchboard.routing.route.probe_once",
                new=AsyncMock(return_value=ProbeOutcome("healthy", True)),
            ),
            patch("butlers.tools.switchboard.routing.route.httpx.AsyncClient"),
        ):
            newly_held = await route(pool, "finance", "route.execute", {}, call_fn=call_fn)
        assert newly_held["transport"]["outcome"] == "not_attempted"
        assert newly_held["retryable"] is False
        call_fn.assert_awaited_once()

        with patch(
            "butlers.tools.switchboard.routing.route.expected_route_target",
            side_effect=ValueError("bad private roster path"),
        ):
            unknown = await route(pool, "finance", "route.execute", {}, call_fn=call_fn)
        assert unknown["transport"]["outcome"] == "not_attempted"
        assert "private roster path" not in unknown["error"]
        call_fn.assert_awaited_once()

    async def test_internal_context_folds_into_route_envelope_without_target_keyword(self) -> None:
        pool = _mock_pool()
        routed_calls: list[dict[str, Any]] = []

        async def call_fn(_url: str, _tool: str, args: dict[str, Any]) -> Any:
            routed_calls.append(dict(args))
            return {"ok": True}

        internal_context = {
            "conceptual_message": {
                "excerpts": [{"sender_entity_id": "11111111-1111-1111-1111-111111111111"}]
            }
        }
        with _resolves_target():
            await route(
                pool,
                "finance",
                "expense_log",
                {"amount": 42},
                internal_context=internal_context,
                call_fn=call_fn,
            )

        assert routed_calls == [{"amount": 42}]

        with _resolves_target():
            await route(
                pool,
                "finance",
                "route.execute",
                {
                    "schema_version": "route.v1",
                    "input": {
                        "prompt": "Store the conceptual fact.",
                        "context": {"existing": "value"},
                    },
                },
                internal_context=internal_context,
                call_fn=call_fn,
            )

        forwarded = routed_calls[1]
        assert "internal_context" not in forwarded
        assert "__conceptual_message" not in forwarded
        assert forwarded["input"]["context"] == {
            "existing": "value",
            **internal_context,
        }

    async def test_internal_context_preserves_existing_string_input_context(self) -> None:
        pool = _mock_pool()
        forwarded: dict[str, Any] = {}

        async def call_fn(_url: str, _tool: str, args: dict[str, Any]) -> Any:
            forwarded.update(args)
            return {"ok": True}

        internal_context = {"conceptual_message": {"excerpts": []}}
        with _resolves_target():
            await route(
                pool,
                "finance",
                "route.execute",
                {
                    "schema_version": "route.v1",
                    "input": {"prompt": "Store a fact.", "context": "existing context"},
                },
                internal_context=internal_context,
                call_fn=call_fn,
            )

        assert forwarded["input"]["context"] == {
            "input_context": "existing context",
            **internal_context,
        }

    async def test_target_failure_is_content_blind(self, caplog: pytest.LogCaptureFixture) -> None:
        pool = _mock_pool()
        sentinel = "15551234567@s.whatsapp.net SQL SELECT secret_message"

        async def call_fn(_url: str, _tool: str, _args: dict[str, Any]) -> Any:
            raise RuntimeError(sentinel)

        with _resolves_target(), caplog.at_level("DEBUG"):
            result = await route(
                pool,
                "finance",
                "route.execute",
                {"input": {"prompt": "Store the fact."}},
                internal_context={"conceptual_message": {"excerpts": []}},
                call_fn=call_fn,
            )

        assert sentinel not in result["error"]
        assert sentinel not in caplog.text
        assert all(sentinel not in repr(call) for call in pool.method_calls)

    async def test_unrelated_target_failure_preserves_existing_error_envelope(self) -> None:
        pool = _mock_pool()

        async def call_fn(_url: str, _tool: str, _args: dict[str, Any]) -> Any:
            raise RuntimeError("ordinary caller detail")

        with _resolves_target():
            result = await route(pool, "finance", "expense_log", {}, call_fn=call_fn)

        assert result["error"] == "RuntimeError: ordinary caller detail"
        assert result["retryable"] is False
        assert result["transport"]["outcome"] == "uncertain"

    async def test_success_reports_confirmed(self) -> None:
        pool = _mock_pool()

        async def call_fn(_url: str, _tool: str, _args: dict[str, Any]) -> Any:
            return {"ok": True}

        with _resolves_target():
            result = await route(pool, "messenger", "route.execute", {}, call_fn=call_fn)

        assert result["result"] == {"ok": True}
        assert result["transport"]["outcome"] == "confirmed"
        assert result["transport"]["retryable"] is False

    async def test_unresolvable_target_is_proven_not_attempted(self) -> None:
        pool = _mock_pool()
        with patch(
            "butlers.tools.switchboard.routing.route.resolve_routing_target",
            new=AsyncMock(return_value=(None, "Butler 'messenger' is quarantined")),
        ):
            result = await route(pool, "messenger", "route.execute", {}, call_fn=AsyncMock())

        assert result["transport"]["outcome"] == "not_attempted"
        assert result["transport"]["error_class"] == "pre_transport"
        assert result["transport"]["error_detail"] == "recipient_unavailable"
        assert result["transport"]["retryable"] is True

    async def test_rejected_wake_callback_is_policy_denied(self) -> None:
        pool = _mock_pool()
        with patch(
            "butlers.tools.switchboard.routing.route._verify_delegate_wake_callback",
            new=AsyncMock(return_value="ledger mismatch"),
        ):
            result = await route(
                pool, "messenger", "delegate_wake", {"ledger_id": "x", "wake_key": "y"}
            )

        assert result["transport"]["outcome"] == "not_attempted"
        assert result["transport"]["error_detail"] == "policy_denied"

    async def test_timeout_is_uncertain_not_retryable_transport(self) -> None:
        pool = _mock_pool()

        async def call_fn(_url: str, _tool: str, _args: dict[str, Any]) -> Any:
            raise TimeoutError("deadline")

        with _resolves_target():
            result = await route(pool, "messenger", "route.execute", {}, call_fn=call_fn)

        assert result["transport"]["outcome"] == "uncertain"
        assert result["transport"]["error_detail"] == "transport_timeout"
        assert result["transport"]["retryable"] is False

    async def test_refused_connect_is_not_attempted(self) -> None:
        pool = _mock_pool()

        async def call_fn(_url: str, _tool: str, _args: dict[str, Any]) -> Any:
            raise TransportNotAttempted("peer never accepted a connection")

        with _resolves_target():
            result = await route(pool, "messenger", "route.execute", {}, call_fn=call_fn)

        assert result["transport"]["outcome"] == "not_attempted"

    async def test_peer_error_is_rejected_not_uncertain(self) -> None:
        pool = _mock_pool()

        async def call_fn(_url: str, _tool: str, _args: dict[str, Any]) -> Any:
            raise TransportRejected("tool 'x' rejected the request")

        with _resolves_target():
            result = await route(pool, "messenger", "route.execute", {}, call_fn=call_fn)

        assert result["transport"]["outcome"] == "rejected"
        assert result["transport"]["error_detail"] == "provider_rejected"

    async def test_legacy_retryable_field_is_preserved(self) -> None:
        """Typed results are additive: existing callers keep their boolean."""
        pool = _mock_pool()

        async def call_fn(_url: str, _tool: str, _args: dict[str, Any]) -> Any:
            raise ConnectionError("socket died")

        with _resolves_target():
            result = await route(pool, "messenger", "route.execute", {}, call_fn=call_fn)

        assert result["retryable"] is True
        assert result["error"].startswith("ConnectionError: ")


class TestRoutePostSendBookkeeping:
    """A confirmed send must survive every write that happens after it."""

    @pytest.mark.parametrize("failing_write", ["routing_log", "registry"])
    async def test_confirmed_send_survives_bookkeeping_acl_failure(
        self, failing_write: str
    ) -> None:
        denied = asyncpg.exceptions.InsufficientPrivilegeError(
            "permission denied for table switchboard.routing_log"
        )
        calls: list[str] = []

        async def execute(sql: str, *_args: Any, **_kwargs: Any) -> None:
            statement = "routing_log" if "routing_log" in sql else "registry"
            calls.append(statement)
            if statement == failing_write:
                raise denied

        pool = _mock_pool(execute=AsyncMock(side_effect=execute))
        transport_calls = 0

        async def call_fn(_url: str, _tool: str, _args: dict[str, Any]) -> Any:
            nonlocal transport_calls
            transport_calls += 1
            return {"receipt": "ok"}

        with _resolves_target():
            result = await route(pool, "messenger", "route.execute", {}, call_fn=call_fn)

        assert transport_calls == 1, "bookkeeping failure must not re-send"
        assert result == {
            "result": {"receipt": "ok"},
            "transport": {
                "outcome": "confirmed",
                "retryable": False,
            },
        }
        assert failing_write in calls

    async def test_bookkeeping_failure_logs_no_raw_provider_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        secret_bearing = RuntimeError("permission denied; dsn=postgres://user:hunter2@db/butlers")

        async def execute(sql: str, *_args: Any, **_kwargs: Any) -> None:
            raise secret_bearing

        pool = _mock_pool(execute=AsyncMock(side_effect=execute))

        async def call_fn(_url: str, _tool: str, _args: dict[str, Any]) -> Any:
            return {"receipt": "ok"}

        with _resolves_target(), caplog.at_level("WARNING"):
            result = await route(pool, "messenger", "route.execute", {}, call_fn=call_fn)

        assert result["transport"]["outcome"] == "confirmed"
        assert "hunter2" not in caplog.text
        assert "routing_log" in caplog.text or "bookkeeping" in caplog.text

    async def test_pre_send_failure_still_logs_routing(self) -> None:
        """Only the post-send path is best-effort; failures keep their log."""
        pool = _mock_pool()

        with patch(
            "butlers.tools.switchboard.routing.route.resolve_routing_target",
            new=AsyncMock(return_value=(None, "unknown butler")),
        ):
            await route(pool, "messenger", "route.execute", {})

        assert pool.execute.await_count == 1
        assert "routing_log" in pool.execute.await_args.args[0]


# ---------------------------------------------------------------------------
# deliver()
# ---------------------------------------------------------------------------


def _notify_envelope() -> dict[str, Any]:
    return {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "recipient": "123456",
            "message": "Breaker opened.",
        },
    }


def _confirmed_notify_route_result(*, delivery_id: str = "tg-42") -> dict[str, Any]:
    return {
        "result": {
            "schema_version": "route_response.v1",
            "status": "ok",
            "result": {
                "notify_response": {
                    "schema_version": "notify_response.v1",
                    "status": "ok",
                    "delivery": {
                        "channel": "telegram",
                        "delivery_id": delivery_id,
                    },
                }
            },
        },
        "transport": {"outcome": "confirmed", "retryable": False},
    }


def _recovery_envelope() -> dict[str, Any]:
    subject = "approval:relationship:00000000-0000-0000-0000-000000000000"
    return {
        "schema_version": "notify.v1",
        "origin_butler": "relationship",
        "delivery": {
            "intent": "approval_request",
            "channel": "telegram",
            "recipient": "owner-synthetic",
            "message": "Synthetic approval.",
        },
        "actions": [{"verb": "open_dashboard", "dashboard_url": "https://dashboard.example.test"}],
        "recovery": {
            "operation": "handoff",
            "subject_kind": "action",
            "subject_key": subject,
            "presentation_key": f"{subject}:p:1",
            "presentation_generation": 1,
            "presentation_mode": "single",
        },
    }


class TestDeliverPostSendBookkeeping:
    async def test_null_recovery_uses_generic_delivery(self) -> None:
        pool = _mock_pool()
        envelope = {**_notify_envelope(), "recovery": None}

        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.route",
                new=AsyncMock(return_value=_confirmed_notify_route_result()),
            ) as routed,
            patch(
                "butlers.tools.switchboard.notification.deliver.log_notification",
                new=AsyncMock(return_value="notif-1"),
            ) as logged,
            patch(
                "butlers.tools.switchboard.notification.deliver._write_outbound_message_inbox",
                new=AsyncMock(),
            ) as inbox,
        ):
            result = await deliver(pool, notify_request=envelope, source_butler="health")

        assert result["status"] == "sent"
        assert result["delivery_id"] == "tg-42"
        routed.assert_awaited_once()
        assert logged.await_args.kwargs["status"] == "sent"
        assert logged.await_args.kwargs["metadata"]["delivery_id"] == "tg-42"
        assert "recovery" not in logged.await_args.kwargs["metadata"]["notify_request"]
        inbox.assert_awaited_once()

    async def test_nested_route_application_error_is_never_reported_sent(self) -> None:
        pool = _mock_pool()
        route_result = {
            "result": {
                "schema_version": "route_response.v1",
                "status": "error",
                "error": {
                    "class": "validation_error",
                    "message": "Approval recovery authority rejected.",
                    "retryable": False,
                },
            },
            "transport": {"outcome": "confirmed", "retryable": False},
        }

        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.route",
                new=AsyncMock(return_value=route_result),
            ),
            patch(
                "butlers.tools.switchboard.notification.deliver.log_notification",
                new=AsyncMock(return_value="notif-1"),
            ) as logged,
            patch(
                "butlers.tools.switchboard.notification.deliver._write_outbound_message_inbox",
                new=AsyncMock(),
            ) as inbox,
        ):
            result = await deliver(pool, notify_request=_notify_envelope(), source_butler="health")

        assert result["status"] == "failed"
        assert result["error_class"] == "validation_error"
        assert result["error"] == "Approval recovery authority rejected."
        assert logged.await_args.kwargs["status"] == "failed"
        inbox.assert_not_awaited()

    async def test_nested_timeout_without_transport_remains_uncertain(self) -> None:
        pool = _mock_pool()
        route_result = {
            "result": {
                "schema_version": "route_response.v1",
                "status": "error",
                "error": {
                    "class": "timeout",
                    "message": "Telegram delivery failed.",
                    "retryable": False,
                },
            },
            "transport": {"outcome": "confirmed", "retryable": False},
        }

        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.route",
                new=AsyncMock(return_value=route_result),
            ),
            patch(
                "butlers.tools.switchboard.notification.deliver.log_notification",
                new=AsyncMock(return_value="notif-1"),
            ),
        ):
            result = await deliver(pool, notify_request=_notify_envelope(), source_butler="health")

        assert result["status"] == "failed"
        assert result["transport"]["outcome"] == "uncertain"
        assert result["transport"]["error_detail"] == "transport_connection_lost"
        assert result["transport"]["retryable"] is False

    @pytest.mark.parametrize(
        "route_response",
        [
            {"schema_version": "route_response.v1", "status": "accepted"},
            {"schema_version": "route_response.v1", "status": "ok", "result": {}},
            {
                "schema_version": "route_response.v1",
                "status": "ok",
                "result": {
                    "notify_response": {
                        "schema_version": "notify_response.v1",
                        "status": "ok",
                        "delivery": {"channel": "telegram", "delivery_id": ""},
                    }
                },
            },
            {
                "schema_version": "route_response.v1",
                "status": "ok",
                "result": {
                    "notify_response": {
                        "schema_version": "notify_response.v1",
                        "status": "ok",
                        "delivery": {"channel": "email", "delivery_id": "mail-1"},
                    }
                },
            },
        ],
    )
    async def test_sent_requires_complete_matching_messenger_receipt(
        self, route_response: dict[str, Any]
    ) -> None:
        pool = _mock_pool()
        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.route",
                new=AsyncMock(
                    return_value={
                        "result": route_response,
                        "transport": {"outcome": "confirmed", "retryable": False},
                    }
                ),
            ),
            patch(
                "butlers.tools.switchboard.notification.deliver.log_notification",
                new=AsyncMock(return_value="notif-1"),
            ) as logged,
            patch(
                "butlers.tools.switchboard.notification.deliver._write_outbound_message_inbox",
                new=AsyncMock(),
            ) as inbox,
        ):
            result = await deliver(pool, notify_request=_notify_envelope(), source_butler="health")

        assert result["status"] == "failed"
        assert logged.await_args.kwargs["status"] == "failed"
        inbox.assert_not_awaited()

    @pytest.mark.parametrize(
        ("decision_state", "reason", "admitted"),
        [
            ("ready", "ready", True),
            ("stale", "stale_observation", False),
            ("denied", "policy_denied", False),
            ("denied", "missing_target", False),
            (None, None, False),  # No roster authority for this sender.
        ],
    )
    async def test_flagged_recovery_admits_only_ready_sender_without_generic_effects(
        self, monkeypatch, decision_state: str | None, reason: str | None, admitted: bool
    ) -> None:
        from butlers.core.control_plane_identity import ExpectedDaemon
        from butlers.tools.switchboard.registry.registry import ControlPlaneTargetDecision

        monkeypatch.setenv("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER", "1")
        expected = ExpectedDaemon("relationship", 41107, "localhost")
        decision = (
            ControlPlaneTargetDecision(decision_state, reason, expected)
            if decision_state is not None and reason is not None
            else None
        )
        pool = _mock_pool()
        route_result = {"result": {"notify_response": {"handoff": {"classification": "confirmed"}}}}
        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.expected_route_target",
                return_value=None if decision_state is None else expected,
            ) as expected_target,
            patch(
                "butlers.tools.switchboard.notification.deliver.resolve_control_plane_target",
                new=AsyncMock(return_value=decision),
            ) as resolve_target,
            patch(
                "butlers.tools.switchboard.notification.deliver.route",
                new=AsyncMock(return_value=route_result),
            ) as routed,
            patch(
                "butlers.tools.switchboard.notification.deliver.log_notification",
                new=AsyncMock(),
            ) as logged,
            patch(
                "butlers.tools.switchboard.notification.deliver._write_outbound_message_inbox",
                new=AsyncMock(),
            ) as inbox,
        ):
            result = await deliver(
                pool,
                notify_request=_recovery_envelope(),
                source_butler="relationship",
                trusted_source="relationship",
            )

        expected_target.assert_called_once_with("relationship")
        if admitted:
            assert result == {"status": "recovery", "handoff": {"classification": "confirmed"}}
            resolve_target.assert_awaited_once_with(pool, expected)
            routed.assert_awaited_once()
        else:
            assert result == {
                "status": "failed",
                "error": "Approval recovery authority rejected.",
                "retryable": False,
            }
            if decision_state is None:
                resolve_target.assert_not_awaited()
            else:
                resolve_target.assert_awaited_once_with(pool, expected)
            routed.assert_not_awaited()
        logged.assert_not_awaited()
        inbox.assert_not_awaited()
        assert pool.method_calls == []

    async def test_flagged_recovery_fails_closed_when_receiver_lookup_fails(self, monkeypatch):
        from butlers.core.control_plane_identity import ExpectedDaemon

        monkeypatch.setenv("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER", "1")
        pool = _mock_pool()
        expected = ExpectedDaemon("relationship", 41107, "localhost")
        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.expected_route_target",
                return_value=expected,
            ),
            patch(
                "butlers.tools.switchboard.notification.deliver.resolve_control_plane_target",
                new=AsyncMock(side_effect=RuntimeError("private receiver failure")),
            ),
            patch(
                "butlers.tools.switchboard.notification.deliver.route", new=AsyncMock()
            ) as routed,
            patch(
                "butlers.tools.switchboard.notification.deliver.log_notification",
                new=AsyncMock(),
            ) as logged,
            patch(
                "butlers.tools.switchboard.notification.deliver._write_outbound_message_inbox",
                new=AsyncMock(),
            ) as inbox,
        ):
            result = await deliver(
                pool,
                notify_request=_recovery_envelope(),
                source_butler="relationship",
                trusted_source="relationship",
            )

        assert result == {
            "status": "failed",
            "error": "Approval recovery authority rejected.",
            "retryable": False,
        }
        assert "private receiver failure" not in repr(result)
        routed.assert_not_awaited()
        logged.assert_not_awaited()
        inbox.assert_not_awaited()
        assert pool.method_calls == []

    async def test_recovery_uses_trusted_internal_context_and_bypasses_generic_records(
        self,
    ) -> None:
        pool = _mock_pool(fetchval=AsyncMock(return_value="relationship"))
        route_result = {
            "result": {
                "notify_response": {
                    "status": "ok",
                    "handoff": {"classification": "confirmed"},
                }
            },
            "transport": {"outcome": "confirmed", "retryable": False},
        }
        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.route",
                new=AsyncMock(return_value=route_result),
            ) as mock_route,
            patch(
                "butlers.tools.switchboard.notification.deliver.log_notification",
                new=AsyncMock(),
            ) as mock_log,
            patch(
                "butlers.tools.switchboard.notification.deliver._write_outbound_message_inbox",
                new=AsyncMock(),
            ) as mock_inbox,
        ):
            result = await deliver(
                pool,
                notify_request=_recovery_envelope(),
                source_butler="relationship",
                trusted_source="relationship",
            )

        assert result == {"status": "recovery", "handoff": {"classification": "confirmed"}}
        mock_log.assert_not_awaited()
        mock_inbox.assert_not_awaited()
        trusted = mock_route.await_args.kwargs["internal_context"]["_trusted_approval_recovery"]
        assert trusted["issuer"] == trusted["owning_schema"] == "relationship"
        assert "claim_token" not in trusted and "claim_fence" not in trusted

    async def test_recovery_rejects_untrusted_or_malformed_source_before_observability(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        pool = _mock_pool(fetchval=AsyncMock(return_value="relationship"))
        tracer = MagicMock()
        malformed = _recovery_envelope()
        malformed["recovery"]["presentation_key"] += ":private_payload_sentinel"
        mismatched_origin = _recovery_envelope()
        mismatched_origin["origin_butler"] = "caller_secret_sentinel"
        cases = (
            (_recovery_envelope(), "relationship", None),
            (_recovery_envelope(), "relationship", "general"),
            (_recovery_envelope(), "caller_source_sentinel", "relationship"),
            (mismatched_origin, "relationship", "relationship"),
            (malformed, "relationship", "relationship"),
        )

        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.route", new=AsyncMock()
            ) as mock_route,
            patch(
                "butlers.tools.switchboard.notification.deliver.trace.get_tracer",
                return_value=tracer,
            ),
            caplog.at_level("DEBUG"),
        ):
            results = [
                await deliver(
                    pool,
                    notify_request=envelope,
                    source_butler=source,
                    trusted_source=trusted,
                )
                for envelope, source, trusted in cases
            ]

        assert results == [
            {
                "status": "failed",
                "error": "Approval recovery authority rejected.",
                "retryable": False,
            }
        ] * len(cases)
        assert "private_payload_sentinel" not in caplog.text
        assert "caller_secret_sentinel" not in caplog.text
        assert "caller_source_sentinel" not in caplog.text
        assert all("private_payload_sentinel" not in repr(result) for result in results)
        assert all("caller_secret_sentinel" not in repr(result) for result in results)
        assert all("caller_source_sentinel" not in repr(result) for result in results)
        tracer.start_as_current_span.assert_not_called()
        assert pool.method_calls == []
        mock_route.assert_not_awaited()

    async def test_confirmed_delivery_survives_notification_log_failure(self) -> None:
        pool = _mock_pool()
        route_result = _confirmed_notify_route_result()

        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.route",
                new=AsyncMock(return_value=route_result),
            ) as mock_route,
            patch(
                "butlers.tools.switchboard.notification.deliver.log_notification",
                new=AsyncMock(side_effect=asyncpg.exceptions.InsufficientPrivilegeError("denied")),
            ),
        ):
            result = await deliver(pool, notify_request=_notify_envelope(), source_butler="health")

        assert result["status"] == "sent"
        assert result["notification_id"] is None
        assert result["transport"]["outcome"] == "confirmed"
        assert mock_route.await_count == 1, "a logging failure must not re-deliver"

    async def test_confirmed_delivery_survives_message_inbox_failure(self) -> None:
        pool = _mock_pool(execute=AsyncMock(side_effect=RuntimeError("inbox down")))
        route_result = _confirmed_notify_route_result()

        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.route",
                new=AsyncMock(return_value=route_result),
            ),
            patch(
                "butlers.tools.switchboard.notification.deliver.log_notification",
                new=AsyncMock(return_value="notif-1"),
            ),
        ):
            result = await deliver(pool, notify_request=_notify_envelope(), source_butler="health")

        assert result["status"] == "sent"
        assert result["notification_id"] == "notif-1"

    async def test_uncertain_route_failure_is_not_reported_as_retryable(self) -> None:
        pool = _mock_pool()
        transport_mod = sys.modules["butlers.tools.switchboard.routing.transport"]

        route_result = {
            "error": "TimeoutError: deadline",
            "retryable": True,
            "transport": transport_mod.TRANSPORT_TIMEOUT.as_dict(),
        }

        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.route",
                new=AsyncMock(return_value=route_result),
            ),
            patch(
                "butlers.tools.switchboard.notification.deliver.log_notification",
                new=AsyncMock(return_value="notif-1"),
            ),
        ):
            result = await deliver(pool, notify_request=_notify_envelope(), source_butler="health")

        assert result["status"] == "failed"
        assert result["transport"]["outcome"] == "uncertain"
        assert result["transport"]["retryable"] is False

    async def test_pre_send_failure_stays_distinguishable(self) -> None:
        pool = _mock_pool()
        transport_mod = sys.modules["butlers.tools.switchboard.routing.transport"]

        route_result = {
            "error": "Butler 'messenger' not found in registry",
            "retryable": False,
            "transport": transport_mod.RECIPIENT_UNAVAILABLE.as_dict(),
        }

        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.route",
                new=AsyncMock(return_value=route_result),
            ),
            patch(
                "butlers.tools.switchboard.notification.deliver.log_notification",
                new=AsyncMock(return_value="notif-1"),
            ),
        ):
            result = await deliver(pool, notify_request=_notify_envelope(), source_butler="health")

        assert result["status"] == "failed"
        assert result["transport"]["outcome"] == "not_attempted"
        assert result["transport"]["retryable"] is True

    async def test_messenger_business_error_is_provider_rejected(self) -> None:
        pool = _mock_pool()
        route_result = {
            "result": {
                "schema_version": "route_response.v1",
                "status": "error",
                "error": {
                    "class": "delivery_error",
                    "message": "chat not found",
                    "retryable": False,
                },
                "result": {
                    "notify_response": {
                        "status": "error",
                        "error": {"class": "delivery_error", "message": "chat not found"},
                    }
                },
            },
            "transport": {"outcome": "confirmed", "retryable": False},
        }

        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.route",
                new=AsyncMock(return_value=route_result),
            ),
            patch(
                "butlers.tools.switchboard.notification.deliver.log_notification",
                new=AsyncMock(return_value="notif-1"),
            ),
        ):
            result = await deliver(pool, notify_request=_notify_envelope(), source_butler="health")

        assert result["status"] == "failed"
        assert result["transport"]["outcome"] == "rejected"
        assert result["transport"]["retryable"] is False


class TestCallButlerToolExceptionTypes:
    """The MCP hop must raise types the classifier can tell apart."""

    async def test_peer_error_raises_transport_rejected(self) -> None:
        route_mod = sys.modules["butlers.tools.switchboard.routing.route"]

        class _Block:
            text = "tool blew up"

        class _Result:
            isError = True
            content = [_Block()]

        with patch.object(
            route_mod,
            "_call_tool_with_router_client",
            new=AsyncMock(return_value=_Result()),
        ):
            with pytest.raises(TransportRejected):
                await route_mod._call_butler_tool("http://x/mcp/", "t", {})

    async def test_empty_content_raises_transport_uncertain(self) -> None:
        route_mod = sys.modules["butlers.tools.switchboard.routing.route"]

        class _Result:
            isError = False
            content: list[Any] = []

        with patch.object(
            route_mod,
            "_call_tool_with_router_client",
            new=AsyncMock(return_value=_Result()),
        ):
            with pytest.raises(TransportUncertain):
                await route_mod._call_butler_tool("http://x/mcp/", "t", {})

    async def test_double_connect_failure_raises_transport_not_attempted(self) -> None:
        route_mod = sys.modules["butlers.tools.switchboard.routing.route"]

        refused = OSError(errno.ECONNREFUSED, "Connection refused")

        with patch.object(
            route_mod,
            "_get_cached_router_client",
            new=AsyncMock(side_effect=refused),
        ):
            with pytest.raises(TransportNotAttempted):
                await route_mod._call_tool_with_router_client("http://x/mcp/", "t", {})

    async def test_ambiguous_failure_stays_plain_connection_error(self) -> None:
        route_mod = sys.modules["butlers.tools.switchboard.routing.route"]

        reset = OSError(errno.ECONNRESET, "Connection reset by peer")

        with patch.object(
            route_mod,
            "_get_cached_router_client",
            new=AsyncMock(side_effect=reset),
        ):
            with pytest.raises(ConnectionError) as excinfo:
                await route_mod._call_tool_with_router_client("http://x/mcp/", "t", {})

        assert not isinstance(excinfo.value, TransportNotAttempted)


class TestNoEventLoopStarvation:
    """Guard against a bookkeeping shield that silently swallows cancellation."""

    async def test_cancellation_during_bookkeeping_still_propagates(self) -> None:
        pool = _mock_pool(execute=AsyncMock(side_effect=asyncio.CancelledError()))

        async def call_fn(_url: str, _tool: str, _args: dict[str, Any]) -> Any:
            return {"ok": True}

        with _resolves_target():
            with pytest.raises(asyncio.CancelledError):
                await route(pool, "messenger", "route.execute", {}, call_fn=call_fn)

"""An ``ingest.v1`` validation failure never echoes envelope content (bu-66sw8k).

Pydantic's own text quotes the offending input, and connectors re-log the tool error, so the
marker below must reach neither the raised error, any Switchboard log record (every string
attribute, extras included), nor the ``ingest`` tool's error result.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest

from butlers.core_tools._base import ToolContext
from butlers.tools.switchboard.ingestion.ingest import IngestPersistenceError, ingest_v1

pytestmark = pytest.mark.unit

MARKER = "MARKER-9d2f-private-body"


def _envelope() -> dict[str, Any]:
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram_bot",
            "provider": "telegram",
            "endpoint_identity": "telegram_bot_main",
        },
        "event": {
            "external_event_id": "1",
            "external_conversation_id": "telegram:12345",
            "reply_target_ref": "12345:1",
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {"identity": "user_12345"},
        "payload": {"raw": {"message": {"text": "hi"}}, "normalized_text": "hi"},
        "control": {"policy_tier": "default"},
    }


def _bad_schema_version(env: dict) -> None:
    env["schema_version"] = MARKER


def _bad_provider(env: dict) -> None:
    env["source"]["provider"] = MARKER


def _bad_timestamp(env: dict) -> None:
    env["event"]["observed_at"] = MARKER


def _wrong_type_with_content(env: dict) -> None:
    env["payload"]["normalized_text"] = {"nested": MARKER}


def _missing_field_with_content(env: dict) -> None:
    del env["sender"]
    env["payload"]["raw"] = {"message": {"text": MARKER}}


def _unexpected_key_is_content(env: dict) -> None:
    env["payload"][MARKER] = "x"
    env["sender"][MARKER] = "x"


BREAKERS = [
    _bad_schema_version,
    _bad_provider,
    _bad_timestamp,
    _wrong_type_with_content,
    _missing_field_with_content,
    _unexpected_key_is_content,
]


def _strings(record: logging.LogRecord) -> list[str]:
    values: list[str] = [record.getMessage(), str(record.args), record.name]
    values.extend(str(v) for v in record.__dict__.values())
    if record.exc_info:
        values.append("".join(map(str, record.exc_info)))
        values.append(str(record.exc_info[1]))
    return values


def _no_marker_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    for record in caplog.records:
        for value in _strings(record):
            assert MARKER not in value, record.getMessage()


@pytest.mark.parametrize("breaker", BREAKERS, ids=lambda f: f.__name__)
async def test_validation_error_text_and_logs_carry_no_envelope_content(breaker, caplog) -> None:
    envelope = _envelope()
    breaker(envelope)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ValueError, match="Invalid ingest.v1 envelope") as raised:
            await ingest_v1(None, envelope)

    assert MARKER not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    assert any("validation failed" in r.getMessage() for r in caplog.records)
    _no_marker_in_logs(caplog)


async def test_error_lists_field_paths_and_types_only() -> None:
    envelope = _envelope()
    del envelope["sender"]

    with pytest.raises(ValueError) as raised:
        await ingest_v1(None, envelope)

    assert str(raised.value) == "Invalid ingest.v1 envelope: sender: missing"


async def test_unexpected_field_name_is_not_echoed() -> None:
    envelope = _envelope()
    envelope["sender"][MARKER] = "x"

    with pytest.raises(ValueError) as raised:
        await ingest_v1(None, envelope)

    assert (
        str(raised.value)
        == "Invalid ingest.v1 envelope: sender.<unexpected field>: extra_forbidden"
    )


async def test_non_mapping_payload_reports_only_the_exception_class() -> None:
    with pytest.raises(ValueError) as raised:
        await ingest_v1(None, MARKER)  # type: ignore[arg-type]

    assert MARKER not in str(raised.value)
    assert str(raised.value).startswith("Invalid ingest.v1 envelope: ")


def _grab_ingest_tool(monkeypatch):
    import butlers.ingestion_policy as ingestion_policy

    class _Evaluator:
        def __init__(self, *_a, **_k) -> None:
            pass

        async def ensure_loaded(self) -> None:
            return None

    monkeypatch.setattr(ingestion_policy, "IngestionPolicyEvaluator", _Evaluator)

    from butlers.core_tools._switchboard import register_switchboard_tools

    registered: dict[str, Any] = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    ctx = ToolContext(
        daemon=SimpleNamespace(_pipeline=SimpleNamespace(), _buffer=SimpleNamespace()),
        pool=None,
        spawner=None,
        butler_name="switchboard",
        butler_type=None,
        is_switchboard=True,
        is_messenger=False,
        route_metrics=None,
    )
    register_switchboard_tools(ctx, SimpleNamespace(), _core_tool)
    return registered["ingest"]


@pytest.mark.parametrize("breaker", BREAKERS, ids=lambda f: f.__name__)
async def test_ingest_tool_error_result_carries_no_envelope_content(
    breaker, monkeypatch, caplog
) -> None:
    ingest = _grab_ingest_tool(monkeypatch)
    envelope = _envelope()
    breaker(envelope)

    with caplog.at_level(logging.DEBUG):
        result = await ingest(
            schema_version=envelope.get("schema_version"),
            source=envelope.get("source"),
            event=envelope.get("event"),
            sender=envelope.get("sender"),
            payload=envelope.get("payload"),
            control=envelope.get("control"),
        )

    assert result["status"] == "error"
    assert result["retryable"] is False
    assert result["error"].startswith("Invalid ingest.v1 envelope")
    assert MARKER not in str(result)
    _no_marker_in_logs(caplog)


async def test_ingest_tool_reports_persistence_failure_as_transient(monkeypatch) -> None:
    """A transient DB failure is a typed reply, not a raised (string-only) tool error."""

    async def _failing_ingest_v1(*_a, **_k):
        raise IngestPersistenceError(f"Failed to persist ingest envelope: {MARKER}")

    monkeypatch.setattr("butlers.tools.switchboard.ingestion.ingest.ingest_v1", _failing_ingest_v1)
    ingest = _grab_ingest_tool(monkeypatch)

    result = await ingest(**_envelope())

    assert result["status"] == "transient_error"
    assert result["retryable"] is True
    assert MARKER not in str(result)


class _PartitionFailingPool:
    """Pool whose dedupe lookups miss and whose partition DDL raises ``failure``."""

    def __init__(self, failure: Exception) -> None:
        self._failure = failure

    async def fetchrow(self, *_a: Any) -> None:
        return None

    async def execute(self, *_a: Any) -> str:
        raise self._failure


def _drive_real_ingest_v1(monkeypatch, failure: Exception) -> None:
    """Route the tool to the real ``ingest_v1`` against a pool that raises ``failure``."""
    pool = _PartitionFailingPool(failure)

    async def _ingest_v1(_pool, envelope, **_kwargs):
        return await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

    monkeypatch.setattr("butlers.tools.switchboard.ingestion.ingest.ingest_v1", _ingest_v1)


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionResetError(MARKER),
        asyncpg.exceptions.SerializationError(MARKER),
        asyncpg.exceptions.AdminShutdownError(MARKER),
    ],
    ids=lambda e: type(e).__name__,
)
async def test_ingest_tool_reports_transport_driver_failure_as_transient(
    failure, monkeypatch
) -> None:
    _drive_real_ingest_v1(monkeypatch, failure)
    ingest = _grab_ingest_tool(monkeypatch)

    result = await ingest(**_envelope())

    assert result["status"] == "transient_error"
    assert result["retryable"] is True
    assert MARKER not in str(result)


@pytest.mark.parametrize(
    "failure",
    [
        asyncpg.exceptions.UniqueViolationError(MARKER),
        asyncpg.exceptions.DataError(MARKER),
        asyncpg.exceptions.StringDataRightTruncationError(MARKER),
    ],
    ids=lambda e: type(e).__name__,
)
async def test_ingest_tool_reports_deterministic_driver_failure_as_non_retryable(
    failure, monkeypatch
) -> None:
    """A poison envelope must not read as transient, or connectors hold it forever."""
    _drive_real_ingest_v1(monkeypatch, failure)
    ingest = _grab_ingest_tool(monkeypatch)

    result = await ingest(**_envelope())

    assert result["status"] == "error"
    assert result["retryable"] is False
    assert MARKER not in str(result)

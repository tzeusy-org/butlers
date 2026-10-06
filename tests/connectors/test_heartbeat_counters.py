"""Prometheus counter hygiene for connector heartbeat snapshots."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig

pytestmark = pytest.mark.unit


def _heartbeat() -> ConnectorHeartbeat:
    return ConnectorHeartbeat(
        config=HeartbeatConfig(
            connector_type="gmail",
            endpoint_identity="gmail:user:owner@example.com",
        ),
        mcp_client=AsyncMock(),
        metrics=MagicMock(),
        get_health_state=MagicMock(return_value=("healthy", None)),
    )


def _sample(name: str, value: object, **labels: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, value=value, labels=labels)


def _family(name: str, samples: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(name=name, samples=samples)


def test_collect_counters_counts_only_total_samples() -> None:
    heartbeat = _heartbeat()
    families = [
        _family(
            "connector_ingest_submissions",
            [
                _sample(
                    "connector_ingest_submissions_total",
                    "7",
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                    status="success",
                ),
                _sample(
                    "connector_ingest_submissions_created",
                    1_735_689_600,
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                    status="success",
                ),
                _sample(
                    "connector_ingest_submissions_total",
                    2,
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                    status="error",
                ),
                _sample(
                    "connector_ingest_submissions_total",
                    1,
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                    status="duplicate",
                ),
            ],
        ),
        _family(
            "connector_source_api_calls",
            [
                _sample(
                    "connector_source_api_calls_total",
                    11,
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                ),
                _sample(
                    "connector_source_api_calls_created",
                    1_735_689_600,
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                ),
            ],
        ),
        _family(
            "connector_checkpoint_saves",
            [
                _sample(
                    "connector_checkpoint_saves_total",
                    3,
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                    status="success",
                ),
                _sample(
                    "connector_checkpoint_saves_created",
                    1_735_689_600,
                    connector_type="gmail",
                    endpoint_identity="gmail:user:owner@example.com",
                    status="success",
                ),
            ],
        ),
    ]

    with patch("prometheus_client.REGISTRY") as registry:
        registry.collect.return_value = families
        counters = heartbeat._collect_counters()

    assert counters["messages_ingested"] == 7
    assert counters["messages_failed"] == 2
    assert counters["dedupe_accepted"] == 1
    assert counters["source_api_calls"] == 11
    assert counters["checkpoint_saves"] == 3
    assert counters.availability == "available"
    assert counters.unavailable_fields == frozenset()


@pytest.mark.parametrize("raw", ["malformed", "NaN", "inf", "-inf", float("nan"), float("inf")])
def test_collect_counters_ignores_invalid_total_samples(raw: object) -> None:
    heartbeat = _heartbeat()
    family = _family(
        "connector_ingest_submissions",
        [
            _sample(
                "connector_ingest_submissions_total",
                raw,
                connector_type="gmail",
                endpoint_identity="gmail:user:owner@example.com",
                status="success",
            )
        ],
    )

    with patch("prometheus_client.REGISTRY") as registry:
        registry.collect.return_value = [family]
        counters = heartbeat._collect_counters()

    assert counters["messages_ingested"] == 0
    assert counters.availability == "unavailable"
    assert "messages_ingested" in counters.unavailable_fields


def test_collect_counters_marks_missing_source_unavailable_instead_of_claiming_zero() -> None:
    heartbeat = _heartbeat()

    with patch("prometheus_client.REGISTRY") as registry:
        registry.collect.return_value = []
        counters = heartbeat._collect_counters()

    assert counters == {
        "messages_ingested": 0,
        "messages_failed": 0,
        "source_api_calls": 0,
        "checkpoint_saves": 0,
        "dedupe_accepted": 0,
    }
    assert counters.availability == "unavailable"
    assert counters.unavailable_fields == frozenset(counters)


def test_collect_counters_ignores_unnamed_created_style_sample() -> None:
    """A family name cannot turn an unnamed timestamp into a total."""
    heartbeat = _heartbeat()
    family = _family(
        "connector_ingest_submissions",
        [
            SimpleNamespace(
                name=None,
                value=1_735_689_600,
                labels={
                    "connector_type": "gmail",
                    "endpoint_identity": "gmail:user:owner@example.com",
                    "status": "success",
                },
            )
        ],
    )

    with patch("prometheus_client.REGISTRY") as registry:
        registry.collect.return_value = [family]
        counters = heartbeat._collect_counters()

    assert counters["messages_ingested"] == 0
    assert "messages_ingested" in counters.unavailable_fields


def test_collect_counters_ignores_samples_without_a_label_mapping() -> None:
    """Malformed labels lower the field to unavailable instead of aborting a heartbeat."""
    heartbeat = _heartbeat()
    family = _family(
        "connector_ingest_submissions",
        [
            SimpleNamespace(
                name="connector_ingest_submissions_total",
                value=7,
                labels=None,
            )
        ],
    )

    with patch("prometheus_client.REGISTRY") as registry:
        registry.collect.return_value = [family]
        counters = heartbeat._collect_counters()

    assert counters["messages_ingested"] == 0
    assert "messages_ingested" in counters.unavailable_fields


# REQ-connector-base-spec-002: actual producer lifecycle; server admission SQL is V4.
async def test_classification_publication_serializes_exact_ack_and_retires_late_results(caplog):
    """Actual publisher, query generations and event-controlled transport responses."""
    import asyncio
    from uuid import uuid4

    from butlers.connectors.gmail_policy import GmailPolicyEvaluator
    from butlers.connectors.known_contact_state import CLASSIFICATION_KEY

    db = AsyncMock()
    db.fetch.return_value = []
    evaluator = GmailPolicyEvaluator(db)
    await evaluator.get_snapshot()
    entered, release = asyncio.Event(), asyncio.Event()
    transmitted = []
    epoch1, epoch2 = str(uuid4()), str(uuid4())

    def response(sent, *, epoch=None, admitted=True, **changes):
        ack = {
            "admitted": admitted,
            "instance_id": sent["instance_id"],
            "generation": sent["generation"],
            "request_admission_epoch": sent["admission_epoch"],
            "admission_epoch": (epoch or sent["admission_epoch"]) if admitted else None,
            "reason": "none" if admitted else "epoch",
        }
        return {"status": "accepted", "classification_ack": {**ack, **changes}}

    async def send(_name, envelope):
        sent = envelope["capabilities"][CLASSIFICATION_KEY]
        transmitted.append(sent)
        if len(transmitted) == 1:
            entered.set()
            await release.wait()
        return response(sent, epoch=epoch1)

    publisher = ConnectorHeartbeat(
        HeartbeatConfig("gmail", "gmail:user:synthetic@example.test"),
        SimpleNamespace(call_tool=send),
        MagicMock(),
        lambda: ("healthy", None),
        get_capabilities=lambda: {"backfill": True},
        get_contact_snapshot=evaluator.peek_snapshot,
    )
    first = asyncio.create_task(publisher.publish_once(timeout_s=1))
    await entered.wait()
    assert transmitted[0]["generation"] == 0 and transmitted[0]["admission_epoch"] is None
    evaluator._cache_loaded_at -= 901
    latest = await evaluator.get_snapshot()
    queued = asyncio.create_task(publisher.publish_once(timeout_s=1))
    # A queue deadline includes lock acquisition and cannot retire its owner.
    assert await publisher.publish_once(timeout_s=0.01) is False
    assert len(transmitted) == 1 and publisher._active_attempt is not None
    release.set()
    assert await first and await queued
    assert transmitted[1]["generation"] == latest.generation
    assert transmitted[1]["admission_epoch"] == epoch1
    assert publisher._last_acknowledged_generation == latest.generation
    assert evaluator.peek_snapshot() == latest

    # Normal exact-tuple failures never change admission or query truth.
    for changes in [
        {"instance_id": str(uuid4())},
        {"generation": True},
        {"generation": latest.generation - 1},
        {"request_admission_epoch": str(uuid4())},
        {"admission_epoch": str(uuid4())},
        {"reason": "private-contact TOKEN-error"},
    ]:

        async def malformed(_name, envelope):
            return response(envelope["capabilities"][CLASSIFICATION_KEY], **changes)

        publisher._mcp_client.call_tool = malformed
        assert await publisher.publish_once() is False
        assert publisher._admission_epoch == epoch1 and evaluator.peek_snapshot() == latest

    for missing in [{"status": "accepted"}, {"status": "accepted", "classification_ack": []}]:
        publisher._mcp_client.call_tool = AsyncMock(return_value=missing)
        assert await publisher.publish_once() is False
        assert publisher._admission_epoch == epoch1 and evaluator.peek_snapshot() == latest

    async def mutated_arguments(_name, envelope):
        altered = envelope["capabilities"][CLASSIFICATION_KEY]
        altered["generation"] += 100
        return response(altered)

    publisher._mcp_client.call_tool = mutated_arguments
    assert await publisher.publish_once() is False
    assert publisher._admission_epoch == epoch1 and evaluator.peek_snapshot() == latest
    assert publisher._last_acknowledged_generation == latest.generation

    # Hold an old normal reply while a genuine query completes a newer generation.
    entered.clear()
    release.clear()

    async def held_normal(_name, envelope):
        sent = envelope["capabilities"][CLASSIFICATION_KEY]
        entered.set()
        await release.wait()
        return response(sent)

    publisher._mcp_client.call_tool = held_normal
    old = asyncio.create_task(publisher.publish_once(timeout_s=1))
    await entered.wait()
    evaluator._cache_loaded_at -= 901
    newest = await evaluator.get_snapshot()
    release.set()
    assert await old
    assert publisher._last_acknowledged_generation == latest.generation
    assert evaluator.peek_snapshot() == newest

    # Current refusal alone clears the sent epoch, then an unloaded handshake
    # restores admission without changing genuine successful query history.
    async def refused(_name, envelope):
        return response(envelope["capabilities"][CLASSIFICATION_KEY], admitted=False)

    publisher._mcp_client.call_tool = refused
    assert await publisher.publish_once() is False
    assert publisher._admission_epoch is None and evaluator.peek_snapshot() == newest

    for late_admitted in [True, False]:
        entered.clear()
        release.clear()
        completed = asyncio.Event()

        async def stubborn(_name, envelope):
            sent = envelope["capabilities"][CLASSIFICATION_KEY]
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            completed.set()
            return response(sent, epoch=epoch1, admitted=late_admitted)

        publisher._admission_epoch = None
        publisher._mcp_client.call_tool = stubborn
        expired = asyncio.create_task(publisher.publish_once(timeout_s=0.02))
        await entered.wait()
        assert await expired is False
        assert not publisher._publication_lock.locked()

        async def recovered(_name, envelope):
            return response(envelope["capabilities"][CLASSIFICATION_KEY], epoch=epoch2)

        publisher._mcp_client.call_tool = recovered
        assert await publisher.publish_once()
        assert publisher._admission_epoch == epoch2
        release.set()
        await completed.wait()
        assert publisher._admission_epoch == epoch2 and evaluator.peek_snapshot() == newest
        assert await publisher.publish_once()
        assert publisher._last_acknowledged_generation == newest.generation

    entered.clear()
    release.clear()
    publisher._mcp_client.call_tool = held_normal
    cancelled = asyncio.create_task(publisher.publish_once())
    await entered.wait()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert not publisher._publication_lock.locked() and publisher._admission_epoch == epoch2
    await publisher.stop()
    # Stop the actual periodic owner while its transport is active. The first
    # cancellation retires the transport; stop must also drain that new tail.
    active_entered, active_release = asyncio.Event(), asyncio.Event()
    first_cancel_seen = asyncio.Event()
    active_cancellations = 0
    active_transports = []

    async def active_transport(_name, envelope):
        nonlocal active_cancellations
        active_transports.append(asyncio.current_task())
        active_entered.set()
        try:
            await active_release.wait()
        except asyncio.CancelledError:
            active_cancellations += 1
            first_cancel_seen.set()
            try:
                await active_release.wait()
            except asyncio.CancelledError:
                active_cancellations += 1
                raise
        return response(envelope["capabilities"][CLASSIFICATION_KEY])

    publisher._config.interval_s = 0
    publisher._mcp_client.call_tool = active_transport
    publisher.start()
    try:
        await asyncio.wait_for(active_entered.wait(), timeout=1)
        await publisher.stop()
        await asyncio.wait_for(first_cancel_seen.wait(), timeout=1)
        first_stop_pending = sum(not task.done() for task in publisher._transport_tails)
        first_stop_cancellations = active_cancellations
        assert publisher._task is None and not publisher._publication_lock.locked()
        assert publisher._admission_epoch == epoch2
        assert evaluator.peek_snapshot() == newest
        # The second stop is both idempotence and a planted cleanup positive:
        # old code reaches the tail only on this call. Capture first-stop truth.
        await publisher.stop()
        assert not publisher._transport_tails and active_cancellations == 2
        assert first_stop_pending == 0
        assert first_stop_cancellations == 2
    finally:
        await publisher.stop()
        active_release.set()
        await asyncio.gather(*active_transports, return_exceptions=True)

    publisher._config.enabled = False
    sent_before = len(transmitted)
    assert await publisher.publish_once() is False
    assert len(transmitted) == sent_before
    assert "private-contact" not in caplog.text and "TOKEN-error" not in caplog.text
    # Generic defaults still send ordinary capabilities/counters with no ACK.
    generic = _heartbeat()
    generic._get_capabilities = lambda: {"backfill": True}
    generic._mcp_client.call_tool.return_value = {"status": "accepted"}
    await generic._send_heartbeat()
    ordinary = generic._mcp_client.call_tool.call_args.args[1]
    assert ordinary["capabilities"] == {"backfill": True}
    assert generic._admission_epoch is None

    # Generic periodic shutdown retains its cooperative cancellation behavior.
    generic_entered, generic_release = asyncio.Event(), asyncio.Event()

    async def generic_transport(_name, envelope):
        generic_entered.set()
        await generic_release.wait()
        return {"status": "accepted"}

    generic._config.interval_s = 0
    generic._mcp_client.call_tool = generic_transport
    generic.start()
    try:
        await asyncio.wait_for(generic_entered.wait(), timeout=1)
        await generic.stop()
        assert generic._task is None and not generic._transport_tails
        await generic.stop()
        assert generic._admission_epoch is None
    finally:
        generic_release.set()
        await generic.stop()

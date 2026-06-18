"""Regression tests for policy_tier threading through the hot-path enqueue.

Prior regression (bu-yh1jt): the switchboard ``ingest`` tool built a tiered
``DurableBuffer.enqueue(...)`` call but never passed ``policy_tier``, so the
tier set by connectors on the ingest.v1 envelope (``control.policy_tier``) was
silently dropped and every event landed in the ``default`` queue — priority
senders never got expedited dispatch.

These tests pin that the tier on the envelope reaches ``buffer.enqueue`` and
that the absence of a tier falls back to the default.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from butlers.core_tools._base import ToolContext


class _FakeBuffer:
    def __init__(self) -> None:
        self.enqueue_calls: list[dict] = []

    def enqueue(self, **kwargs):
        self.enqueue_calls.append(kwargs)
        return True


class _FakeEvaluator:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def ensure_loaded(self) -> None:
        return None


def _register_and_grab_ingest(monkeypatch, buffer):
    """Register switchboard tools with a fake buffer and capture ``ingest``."""

    # The ingest tool calls ingest_v1(pool, envelope, ...) — stub it to return a
    # non-duplicate accepted result so the enqueue branch is exercised.
    request_id = uuid.uuid4()

    async def _fake_ingest_v1(_pool, _envelope, **_kwargs):
        return SimpleNamespace(
            duplicate=False,
            request_id=request_id,
            triage_decision=None,
            triage_target=None,
            model_dump=lambda mode="json": {"status": "accepted"},
        )

    import butlers.ingestion_policy as _ip_mod
    import butlers.tools.switchboard.ingestion.ingest as _ingest_mod

    monkeypatch.setattr(_ingest_mod, "ingest_v1", _fake_ingest_v1)
    # Avoid touching a real DB pool when the evaluator is constructed.
    monkeypatch.setattr(_ip_mod, "IngestionPolicyEvaluator", _FakeEvaluator)

    from butlers.core_tools._switchboard import register_switchboard_tools

    registered: dict[str, callable] = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    ctx = ToolContext(
        daemon=SimpleNamespace(
            _pipeline=SimpleNamespace(),  # truthy → buffer branch is taken
            _buffer=buffer,
        ),
        pool=None,
        spawner=None,
        butler_name="switchboard",
        butler_type=None,
        is_switchboard=True,
        is_messenger=False,
        route_metrics=None,
    )
    register_switchboard_tools(ctx, SimpleNamespace(), _core_tool)
    return registered["ingest"], request_id


def _envelope_kwargs(policy_tier: str | None):
    control: dict | None = {"idempotency_key": "k1"}
    if policy_tier is not None:
        control["policy_tier"] = policy_tier
    return {
        "schema_version": "ingest.v1",
        "source": {"channel": "gmail", "provider": "gmail", "endpoint_identity": "acct"},
        "event": {"external_event_id": "evt-1", "observed_at": "2026-06-18T00:00:00+00:00"},
        "sender": {"identity": "vip@example.com"},
        "payload": {"raw": {}, "normalized_text": "Subject: hello\n\nbody"},
        "control": control,
    }


async def test_high_priority_tier_reaches_enqueue(monkeypatch):
    buffer = _FakeBuffer()
    ingest, _request_id = _register_and_grab_ingest(monkeypatch, buffer)

    await ingest(**_envelope_kwargs("high_priority"))

    assert len(buffer.enqueue_calls) == 1
    assert buffer.enqueue_calls[0]["policy_tier"] == "high_priority"


async def test_missing_tier_falls_back_to_default(monkeypatch):
    buffer = _FakeBuffer()
    ingest, _request_id = _register_and_grab_ingest(monkeypatch, buffer)

    await ingest(**_envelope_kwargs(None))

    assert len(buffer.enqueue_calls) == 1
    assert buffer.enqueue_calls[0]["policy_tier"] == "default"


async def test_no_control_falls_back_to_default(monkeypatch):
    buffer = _FakeBuffer()
    ingest, _request_id = _register_and_grab_ingest(monkeypatch, buffer)

    kwargs = _envelope_kwargs(None)
    kwargs["control"] = None
    await ingest(**kwargs)

    assert len(buffer.enqueue_calls) == 1
    assert buffer.enqueue_calls[0]["policy_tier"] == "default"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

"""Unit tests for butlers.core.domain_event_wake (bu-ac4yc).

Mocked-pool style mirroring tests/core/test_delegation_wake.py. These pin the
*descriptive-only* validity contract the module docstring states: a derived
advisory's ``valid_until`` is a producer convention inside an open JSONB
payload, never a bus-level delivery predicate. See
tests/integration/test_domain_event_bus_roundtrip.py for the real-Postgres
fan-out coverage.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from butlers.core import domain_event_wake
from butlers.core.domain_event_wake import (
    _build_wake_task_prompt,
    handle_receive_domain_event,
)

pytestmark = pytest.mark.unit

_EXPIRED_ADVISORY = {
    "state": "depleted",
    "max_severity": 8,
    "valid_until": "2020-01-01T00:00:00+00:00",
}


def _assert_fixture_is_genuinely_expired(payload: dict) -> datetime:
    """Guard the fixture itself: a TTL test on a non-expired advisory proves nothing."""
    valid_until = datetime.fromisoformat(payload["valid_until"])
    now = datetime.now(UTC)
    assert valid_until < now, (
        f"fixture is not expired: valid_until={valid_until.isoformat()} is not "
        f"before the delivery clock {now.isoformat()}"
    )
    return now


class TestDescriptiveOnlyValiditySemantics:
    """`valid_until` is descriptive; the wake path must never act on it."""

    async def test_expired_advisory_is_still_delivered(self, monkeypatch):
        now = _assert_fixture_is_genuinely_expired(_EXPIRED_ADVISORY)
        task_id = uuid.uuid4()
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        schedule_create = AsyncMock(return_value=task_id)
        monkeypatch.setattr(domain_event_wake, "schedule_create", schedule_create)

        result = await handle_receive_domain_event(
            pool,
            event_id="11111111-1111-1111-1111-111111111111",
            event_type="health.recovery_state",
            source_butler="health",
            payload=_EXPIRED_ADVISORY,
            subscriber_butler="lifestyle",
        )

        assert result["status"] == "ok"
        assert result["state"] == "task_created"
        assert result["task_id"] == str(task_id)
        assert schedule_create.await_count == 1, (
            "an advisory past its own valid_until must still schedule a wake -- "
            "the bus makes no delivery decision from payload content"
        )
        # The wake is scheduled forward from the delivery clock, never clamped to
        # the (already lapsed) validity window carried in the payload.
        until_at = schedule_create.await_args.kwargs["until_at"]
        assert until_at > now

    async def test_expired_valid_until_is_embedded_verbatim(self):
        _assert_fixture_is_genuinely_expired(_EXPIRED_ADVISORY)

        prompt = _build_wake_task_prompt(
            event_id="22222222-2222-2222-2222-222222222222",
            event_type="health.recovery_state",
            source_butler="health",
            subscriber_butler="lifestyle",
            payload=_EXPIRED_ADVISORY,
        )

        # Delivered verbatim: the bus neither strips nor rewrites the window it
        # declines to enforce, so the subscriber can judge freshness itself.
        assert json.dumps(_EXPIRED_ADVISORY, sort_keys=True) in prompt

    def test_prompt_tells_the_session_to_recheck_the_validity_window(self):
        prompt = _build_wake_task_prompt(
            event_id="33333333-3333-3333-3333-333333333333",
            event_type="finance.budget_pressure",
            source_butler="finance",
            subscriber_butler="general",
            payload={"category": "groceries", "valid_until": "2020-01-01T00:00:00+00:00"},
        )

        assert "valid_until" in prompt
        assert "compare it against the current time first" in prompt

        # The caveat is trusted bus text, so it must sit OUTSIDE the fence --
        # inside it the prompt itself forbids reading it as an instruction.
        fence_end = prompt.index("</domain_event>")
        assert prompt.index("compare it against the current time first") > fence_end


class TestWakeSchedulingIgnoresPayloadContent:
    async def test_scheduling_is_derived_from_the_delivery_clock_only(self, monkeypatch):
        """Two payloads with opposite validity windows schedule the identical wake."""
        frozen_now = datetime(2026, 8, 24, 12, 30, 0, tzinfo=UTC)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now if tz is None else frozen_now.astimezone(tz)

        monkeypatch.setattr(domain_event_wake, "datetime", _FrozenDatetime)

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        schedule_create = AsyncMock(return_value=uuid.uuid4())
        monkeypatch.setattr(domain_event_wake, "schedule_create", schedule_create)

        far_future = (frozen_now + timedelta(days=365)).isoformat()
        payloads = (
            {"valid_until": "2020-01-01T00:00:00+00:00"},
            {"valid_until": far_future},
        )
        for index, payload in enumerate(payloads):
            await handle_receive_domain_event(
                pool,
                event_id=f"4444444{index}-4444-4444-4444-444444444444",
                event_type="health.recovery_state",
                source_butler="health",
                payload=payload,
                subscriber_butler="lifestyle",
            )

        assert schedule_create.await_count == 2, (
            "both events must schedule a wake regardless of their validity windows"
        )
        expired_call, fresh_call = schedule_create.await_args_list
        # cron is the 3rd positional arg of schedule_create(pool, name, cron, prompt).
        assert expired_call.args[2] == "31 12 24 8 *"
        assert expired_call.kwargs["until_at"] == frozen_now + timedelta(minutes=2)
        assert (expired_call.args[2], expired_call.kwargs["until_at"]) == (
            fresh_call.args[2],
            fresh_call.kwargs["until_at"],
        ), "payload validity must not steer the wake's cron or expiry"


class TestReactionLifecycleIsOpened:
    """A wake that is scheduled must show up in the reaction ledger (bu-6jv4m.8)."""

    async def test_a_fresh_wake_opens_a_scheduled_receipt(self, monkeypatch):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        monkeypatch.setattr(
            domain_event_wake, "schedule_create", AsyncMock(return_value=uuid.uuid4())
        )
        recorded: list[dict] = []

        async def _record(_pool, **kwargs):
            recorded.append(kwargs)
            return "reaction-id"

        monkeypatch.setattr(domain_event_wake, "record_reaction", _record)

        result = await handle_receive_domain_event(
            pool,
            event_id="11111111-1111-1111-1111-111111111111",
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={"trip_id": "t-1"},
            subscriber_butler="finance",
        )

        assert result["status"] == "ok"
        assert len(recorded) == 1
        assert recorded[0]["status"] == "scheduled"
        assert recorded[0]["subscriber_butler"] == "finance"
        assert recorded[0]["task_name"] == result["task_name"]

    async def test_a_duplicate_delivery_does_not_reopen_the_lifecycle(self, monkeypatch):
        """Positive control: a re-delivered wake must not stack a second 'scheduled'."""
        task_name = domain_event_wake.task_name_for(
            "11111111-1111-1111-1111-111111111111", "finance"
        )
        prompt = domain_event_wake._build_wake_task_prompt(
            event_id="11111111-1111-1111-1111-111111111111",
            event_type="travel.trip_booked",
            source_butler="travel",
            subscriber_butler="finance",
            payload={"trip_id": "t-1"},
        )
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            return_value={"id": uuid.uuid4(), "name": task_name, "prompt": prompt}
        )
        recorded: list[dict] = []

        async def _record(_pool, **kwargs):
            recorded.append(kwargs)
            return "reaction-id"

        monkeypatch.setattr(domain_event_wake, "record_reaction", _record)

        result = await handle_receive_domain_event(
            pool,
            event_id="11111111-1111-1111-1111-111111111111",
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={"trip_id": "t-1"},
            subscriber_butler="finance",
        )

        assert result["reconciled"] is True
        assert recorded == []

    async def test_a_ledger_write_failure_never_loses_the_wake(self, monkeypatch):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        monkeypatch.setattr(
            domain_event_wake, "schedule_create", AsyncMock(return_value=uuid.uuid4())
        )

        async def _boom(_pool, **_kwargs):
            raise RuntimeError("ledger unavailable")

        monkeypatch.setattr(domain_event_wake, "record_reaction", _boom)

        result = await handle_receive_domain_event(
            pool,
            event_id="11111111-1111-1111-1111-111111111111",
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={"trip_id": "t-1"},
            subscriber_butler="finance",
        )
        assert result["status"] == "ok"


class TestWakePromptClosesTheLoop:
    def test_the_prompt_asks_the_session_to_file_a_receipt(self):
        prompt = domain_event_wake._build_wake_task_prompt(
            event_id="11111111-1111-1111-1111-111111111111",
            event_type="travel.trip_booked",
            source_butler="travel",
            subscriber_butler="finance",
            payload={"trip_id": "t-1"},
        )
        assert "report_event_reaction" in prompt
        assert "ignored" in prompt

    def test_the_receipt_instruction_stays_outside_the_untrusted_fence(self):
        prompt = domain_event_wake._build_wake_task_prompt(
            event_id="11111111-1111-1111-1111-111111111111",
            event_type="travel.trip_booked",
            source_butler="travel",
            subscriber_butler="finance",
            payload={"trip_id": "t-1"},
        )
        fence = prompt[prompt.index("<domain_event>") : prompt.index("</domain_event>")]
        assert "report_event_reaction" not in fence

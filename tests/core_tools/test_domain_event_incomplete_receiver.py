"""Regression: the live Travel -> Finance incomplete-receiver failure (bu-6jv4m.8).

The production ledger carries two ``failed_permanent`` Travel ->
Finance deliveries of ``travel.trip_booked`` whose recorded error is
``receive_domain_event on 'finance' returned an incomplete success payload:
None`` -- the receiver answered the route without the task provenance the
publisher needs to record what it did.

These tests reconstruct that shape deterministically from the two route
envelopes that produce it, with no live connector, no replay, and no
mutation of any running runtime. They pin two things: the failure is
classified as terminal rather than retried forever, and -- the part this
bead adds -- a delivery that ends this way never acquires a reaction
receipt, because nothing on the subscriber ever ran.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from butlers.core_tools import _domain_events
from butlers.core_tools._domain_events import (
    _dispatch_and_record_delivery,
    _unwrap_route_result,
)

pytestmark = pytest.mark.unit

_INCOMPLETE_ENVELOPES = {
    "result_is_none": {"result": None},
    "result_is_not_a_mapping": {"result": "ok"},
    "result_omits_task_provenance": {"result": {"status": "ok"}},
}


class TestTheObservedEnvelopeShape:
    @pytest.mark.parametrize("name", sorted(_INCOMPLETE_ENVELOPES))
    def test_an_incomplete_success_never_unwraps_to_usable_provenance(self, name: str) -> None:
        data, error, retryable = _unwrap_route_result(_INCOMPLETE_ENVELOPES[name])
        assert error is None, "route() reported success; the gap is in the payload, not the route"
        assert retryable is False
        assert not (data or {}).get("task_id")

    def test_a_complete_success_does_unwrap(self) -> None:
        """Positive control: fails if the unwrap rejects a genuinely good payload."""
        data, error, _retryable = _unwrap_route_result(
            {"result": {"state": "task_created", "task_id": "t-1", "task_name": "n-1"}}
        )
        assert error is None
        assert data is not None
        assert data["task_id"] == "t-1"


class TestTheDeliveryOutcome:
    @pytest.mark.parametrize("name", sorted(_INCOMPLETE_ENVELOPES))
    async def test_an_incomplete_receiver_is_recorded_failed_permanent(
        self, name: str, monkeypatch
    ) -> None:
        unwrapped = _unwrap_route_result(_INCOMPLETE_ENVELOPES[name])
        monkeypatch.setattr(
            _domain_events,
            "_dispatch_receive_via_switchboard",
            AsyncMock(return_value=unwrapped),
        )
        mark_failed = AsyncMock(return_value="failed_permanent")
        monkeypatch.setattr(_domain_events, "mark_delivery_failed", mark_failed)
        monkeypatch.setattr(_domain_events, "mark_delivery_delivered", AsyncMock())

        outcome = await _dispatch_and_record_delivery(
            AsyncMock(),
            AsyncMock(),
            delivery={"id": uuid.uuid4(), "status": "pending"},
            subscriber_butler="finance",
            event_id=str(uuid.uuid4()),
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={"trip_id": "trip-synthetic"},
        )

        assert outcome["status"] == "failed_permanent"
        assert "incomplete success payload" in outcome["error"]
        assert outcome.get("retryable") is not True
        assert mark_failed.await_args.kwargs["retryable"] is False

    async def test_the_live_error_text_is_reproduced_exactly(self, monkeypatch) -> None:
        monkeypatch.setattr(
            _domain_events,
            "_dispatch_receive_via_switchboard",
            AsyncMock(return_value=_unwrap_route_result({"result": None})),
        )
        monkeypatch.setattr(
            _domain_events, "mark_delivery_failed", AsyncMock(return_value="failed_permanent")
        )

        outcome = await _dispatch_and_record_delivery(
            AsyncMock(),
            AsyncMock(),
            delivery={"id": uuid.uuid4(), "status": "pending"},
            subscriber_butler="finance",
            event_id=str(uuid.uuid4()),
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={"trip_id": "trip-synthetic"},
        )

        assert outcome["error"] == (
            "receive_domain_event on 'finance' returned an incomplete success payload: None"
        )

    async def test_a_never_delivered_wake_earns_no_reaction_receipt(self, monkeypatch) -> None:
        """The subscriber never ran, so there is nothing to report -- and nothing is."""
        monkeypatch.setattr(
            _domain_events,
            "_dispatch_receive_via_switchboard",
            AsyncMock(return_value=_unwrap_route_result({"result": None})),
        )
        monkeypatch.setattr(
            _domain_events, "mark_delivery_failed", AsyncMock(return_value="failed_permanent")
        )
        record_reaction = AsyncMock()
        monkeypatch.setattr(_domain_events, "record_reaction", record_reaction)

        await _dispatch_and_record_delivery(
            AsyncMock(),
            AsyncMock(),
            delivery={"id": uuid.uuid4(), "status": "pending"},
            subscriber_butler="finance",
            event_id=str(uuid.uuid4()),
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={"trip_id": "trip-synthetic"},
        )

        record_reaction.assert_not_awaited()

"""Fail-closed contract admission and reaction receipts (bu-6jv4m.8).

Two boundaries live here. Admission: a publish or a subscription that the
publisher's git-declared contract does not permit must be refused *before*
any row is written, in both directions. Receipts: a waking session closes
its own wake with a typed outcome, and no path lets it -- or anything else
-- claim ``unreported``, which belongs to the correlation sweep alone.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.config import ButlerType
from butlers.core import domain_event_contracts as contracts_module
from butlers.core.domain_event_contracts import (
    DomainEventContractRegistry,
    set_contract_registry,
)
from butlers.core_tools import _domain_events
from butlers.core_tools._base import ToolContext
from butlers.core_tools._domain_events import (
    publish_domain_event,
    publish_domain_event_once,
    register_domain_event_tools,
)

pytestmark = pytest.mark.unit

_EVENT_ID = "11111111-1111-1111-1111-111111111111"


def _declaration(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "travel.trip_booked",
        "schema_version": 1,
        "summary": "A brand-new trip container was created.",
        "retention_policy": "standard",
        "reaction_expectation": "expected",
        "reaction_contract": "Consider a pre-budget check for the newly booked trip.",
        "permitted_subscribers": ["finance"],
        "required_fields": ["trip_id"],
        "optional_fields": ["destination"],
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def declared_registry():
    """Pin admission to one synthetic declaration, not the live roster."""
    set_contract_registry(
        DomainEventContractRegistry.from_declarations([("travel", _declaration())])
    )
    yield
    set_contract_registry(None)


def _register(butler_name: str, switchboard_client=None, pool=None):
    registered: dict[str, object] = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    ctx = ToolContext(
        daemon=SimpleNamespace(switchboard_client=switchboard_client),
        pool=pool if pool is not None else AsyncMock(),
        spawner=None,
        butler_name=butler_name,
        butler_type=ButlerType.BUTLER,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    register_domain_event_tools(ctx, SimpleNamespace(), _core_tool)
    return registered


class TestPublishAdmission:
    async def test_an_undeclared_event_type_is_refused_before_the_log_write(
        self, monkeypatch
    ) -> None:
        record_event = AsyncMock()
        monkeypatch.setattr(_domain_events, "record_event", record_event)

        result = await publish_domain_event(
            AsyncMock(),
            AsyncMock(),
            event_type="travel.trip_cancelled",
            source_butler="travel",
            payload={},
        )

        assert result["status"] == "error"
        assert "no publisher-owned contract" in result["error"]
        record_event.assert_not_awaited()

    async def test_an_undeclared_payload_field_is_refused(self, monkeypatch) -> None:
        record_event = AsyncMock()
        monkeypatch.setattr(_domain_events, "record_event", record_event)

        result = await publish_domain_event(
            AsyncMock(),
            AsyncMock(),
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={"trip_id": "t-1", "passport_number": "synthetic-value"},
        )

        assert result["status"] == "error"
        assert "passport_number" in result["error"]
        record_event.assert_not_awaited()

    async def test_a_foreign_publisher_cannot_publish_into_another_namespace(
        self, monkeypatch
    ) -> None:
        record_event = AsyncMock()
        monkeypatch.setattr(_domain_events, "record_event", record_event)

        result = await publish_domain_event(
            AsyncMock(),
            AsyncMock(),
            event_type="travel.trip_booked",
            source_butler="finance",
            payload={"trip_id": "t-1"},
        )

        assert result["status"] == "error"
        assert "owned by butler 'travel'" in result["error"]
        record_event.assert_not_awaited()

    async def test_a_declared_publish_still_goes_through(self, monkeypatch) -> None:
        """Positive control: admission refuses violations, not publishing itself."""
        monkeypatch.setattr(_domain_events, "record_event", AsyncMock(return_value=_EVENT_ID))
        monkeypatch.setattr(
            _domain_events, "fan_out_event", AsyncMock(return_value={"deliveries": []})
        )

        result = await publish_domain_event(
            AsyncMock(),
            AsyncMock(),
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={"trip_id": "t-1", "destination": "Kyoto"},
        )

        assert result == {"status": "ok", "event_id": _EVENT_ID, "deliveries": []}

    async def test_publish_once_is_refused_before_the_dedup_claim(self, monkeypatch) -> None:
        claim = AsyncMock()
        monkeypatch.setattr(_domain_events, "_claim_and_record_event", claim)

        result = await publish_domain_event_once(
            AsyncMock(),
            AsyncMock(),
            event_type="travel.trip_booked",
            source_butler="travel",
            dedup_namespace="travel.trip_booked",
            dedup_key="t-1",
            payload={"trip_id": "t-1", "undeclared_field": "x"},
        )

        assert result is not None
        assert result["status"] == "error"
        claim.assert_not_awaited()


class TestSubscriptionAdmission:
    async def test_an_unpermitted_subscriber_is_refused_before_the_upsert(
        self, monkeypatch
    ) -> None:
        upsert = AsyncMock()
        monkeypatch.setattr(_domain_events, "upsert_subscription", upsert)
        tools = _register("lifestyle")

        result = await tools["subscribe_to_event"]("travel.trip_booked")

        assert result["status"] == "error"
        assert "lifestyle" in result["error"]
        upsert.assert_not_awaited()

    async def test_a_permitted_subscriber_is_admitted(self, monkeypatch) -> None:
        upsert = AsyncMock(return_value={"subscriber_butler": "finance"})
        monkeypatch.setattr(_domain_events, "upsert_subscription", upsert)
        tools = _register("finance")

        result = await tools["subscribe_to_event"]("travel.trip_booked")

        assert result["status"] == "ok"
        upsert.assert_awaited_once()


class TestReactionReceiptTool:
    async def test_a_session_closes_its_wake_with_its_own_session_id(self, monkeypatch) -> None:
        record = AsyncMock(return_value=str(uuid.uuid4()))
        monkeypatch.setattr(_domain_events, "record_reaction", record)
        monkeypatch.setattr(_domain_events, "get_current_runtime_session_id", lambda: "session-xyz")
        tools = _register("finance")

        result = await tools["report_event_reaction"](
            event_id=_EVENT_ID,
            status="acted",
            note="Opened a pre-budget.",
            evidence=[{"kind": "task", "ref": "pre-budget-trip-42"}],
        )

        assert result["status"] == "ok"
        kwargs = record.await_args.kwargs
        assert kwargs["status"] == "acted"
        assert kwargs["subscriber_butler"] == "finance"
        assert kwargs["session_id"] == "session-xyz"

    async def test_a_session_cannot_declare_itself_unreported(self, monkeypatch) -> None:
        """`unreported` is the sweep's finding about silence, never a self-report."""
        record = AsyncMock()
        monkeypatch.setattr(_domain_events, "record_reaction", record)
        tools = _register("finance")

        result = await tools["report_event_reaction"](event_id=_EVENT_ID, status="unreported")

        assert result["status"] == "error"
        assert "unreported" in result["error"]
        record.assert_not_awaited()

    async def test_untyped_evidence_is_refused(self, monkeypatch) -> None:
        tools = _register("finance")
        result = await tools["report_event_reaction"](
            event_id=_EVENT_ID, status="acted", evidence=[{"kind": "vibes", "ref": "x"}]
        )
        assert result["status"] == "error"
        assert "vibes" in result["error"]

    async def test_closing_an_already_closed_wake_reports_the_conflict(self, monkeypatch) -> None:
        from butlers.core.domain_event_reactions import DomainEventReactionError

        monkeypatch.setattr(
            _domain_events,
            "record_reaction",
            AsyncMock(side_effect=DomainEventReactionError("already closed")),
        )
        tools = _register("finance")

        result = await tools["report_event_reaction"](event_id=_EVENT_ID, status="ignored")

        assert result["status"] == "error"
        assert "already closed" in result["error"]


class TestRegistrySeam:
    def test_the_process_registry_falls_back_to_the_live_roster(self) -> None:
        """Clearing the override must re-read git, not leave admission wide open."""
        set_contract_registry(None)
        registry = contracts_module.get_contract_registry()
        assert registry.get("travel.trip_booked") is not None
        assert len(registry) >= 4

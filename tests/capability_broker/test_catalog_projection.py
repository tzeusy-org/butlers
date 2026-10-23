"""Actual registered declarations -> broker data, not fleet/currentness authority.

Registration uses the existing real roster contract helper with database connect
stubbed. The policy consumer below is real; no handler, PG transaction, owning
approval receipt, current-fleet reader or live operation is exercised.
"""

import json
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.capability_broker.contracts import (
    BrokerRefused,
    Disposition,
    PolicyEvidence,
    Principal,
)
from butlers.capability_broker.policy import decide
from butlers.core.goal_admission import Grant
from butlers.core.tool_catalog import (
    ToolCatalog,
    ToolCatalogError,
    _canonical_bytes,
    validate_authorization_catalog,
)
from butlers.core.tool_presentation_inventory import STORE_WRITERS
from tests.contracts.test_tool_authorization_catalog import _roster_catalog

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("owner", ["messenger", "relationship"])
async def test_registered_calendar_mode_survives_configuration_and_parks_with_matching_rule(
    monkeypatch, owner
):
    """REQ-capability-authorization-002 and REQ-capability-authorization-017."""
    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    final = await _roster_catalog(owner, expiry_hours=49)
    response = final["calendar_respond"]
    # This assertion is the actual pre-implementation producer RED, before the
    # new adapter is imported. No hand-built descriptor supplies the mode.
    assert response.approval_policy["mode"] == "always_per_event"
    assert response.approval_policy["enabled"] is True
    assert response.approval_policy["configured_gate"] is True
    assert response.approval_policy["expiry_hours"] == 49
    from butlers.capability_broker.catalog import project_catalog

    projected = project_catalog(final, domain=owner)
    assert projected.digest == final.generation_digest
    capability = next(c for c in projected.capabilities if c.name == "calendar_respond")
    assert capability.always_per_event and capability.approvals_available
    assert capability.approval_required and capability.idempotency == "key_required"
    assert dict(capability.selection_bound) == dict(response.selection_bound)
    assert capability.resource_params == response.resource_params
    assert capability.recipient_params == response.recipient_params
    assert capability.input_schema == json.loads(_canonical_bytes(response.input_schema))
    for exposed in (False, True):
        principal = Principal(uuid4(), uuid4(), 0, "owner", exposed, projected.digest, ())
        result = decide(
            principal,
            capability,
            Grant(capability.identity),
            PolicyEvidence("owner", standing_rule=True, template_pinned=True),
        )
        assert result.disposition is Disposition.PARK
        assert result.allow_standing_rule is False
    assert all(c.name != "calendar_response_fence_status" for c in projected.capabilities)


async def test_projection_refuses_missing_mode_gate_and_partial_resource_marks(monkeypatch):
    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    final = await _roster_catalog("messenger")
    from butlers.capability_broker.catalog import project_catalog

    response = final["calendar_respond"]
    policy = dict(response.approval_policy)
    variants = [
        replace(response, approval_policy={k: v for k, v in policy.items() if k != "mode"}),
        replace(response, approval_policy=policy | {"mode": "legacy"}),
        replace(response, approval_policy=policy | {"mode": True}),
        replace(response, approval_policy=None),
        replace(response, approval_policy=policy | {"mode": "default"}),
        replace(response, approval_policy=policy | {"enabled": False}),
        replace(response, approval_policy=policy | {"configured_gate": False}),
        replace(
            response,
            approval_policy={k: v for k, v in policy.items() if k != "configured_gate"},
        ),
        replace(response, approval_policy={k: v for k, v in policy.items() if k != "enabled"}),
        replace(response, approval_policy=policy | {"configured_gate": 1}),
        replace(response, selection_bound={}),
    ]
    for changed in variants:
        bad = ToolCatalog((changed,), True, final.generation_digest)
        with pytest.raises(BrokerRefused):
            project_catalog(bad, domain="messenger")
        with pytest.raises(ToolCatalogError):
            validate_authorization_catalog(bad, store_writers=STORE_WRITERS)
    with pytest.raises(BrokerRefused):
        project_catalog(final, domain="unrelated")


async def test_default_projection_preserves_final_schema_without_mutable_aliases(monkeypatch):
    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    final = await _roster_catalog("messenger")
    from butlers.capability_broker.catalog import project_catalog

    first = project_catalog(final, domain="messenger")
    read = next(c for c in first.capabilities if c.name == "calendar_list_calendars")
    assert final[read.name].approval_policy["mode"] == "default"
    assert not read.always_per_event and not read.approval_required
    assert read.approvals_available
    assert read.selection_bound == () and read.recipient_resolver == "none"
    read.input_schema["properties"]["injected"] = {"type": "string"}
    assert "injected" not in final[read.name].input_schema["properties"]
    again = next(
        c for c in project_catalog(final, domain="messenger").capabilities if c.name == read.name
    )
    assert "injected" not in again.input_schema["properties"]
    owner_final = await _roster_catalog("relationship")
    notify = next(
        c
        for c in project_catalog(owner_final, domain="relationship").capabilities
        if c.name == "notify"
    )
    assert notify.recipient_resolver == "core_notify"
    renamed = replace(owner_final["notify"], module_name="other", namespace="other.notifications")
    other = project_catalog(
        ToolCatalog((renamed,), True, owner_final.generation_digest), domain="relationship"
    )
    assert other.capabilities[0].recipient_resolver == "none"


async def test_actual_broker_call_consumes_projection_and_rejects_changed_catalog(monkeypatch):
    """Real service/ledger consumer, controlled rows and domain (no PG/receipt claim)."""
    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    final = await _roster_catalog("messenger")
    from butlers.capability_broker.catalog import project_catalog
    from tests.capability_broker.test_broker import build

    projected = project_catalog(final, domain="messenger")
    capability = next(c for c in projected.capabilities if c.name == "calendar_list_calendars")
    broker, connection, authority, domains, token = build(
        capability=capability, grant=Grant(capability.identity)
    )
    original_fetchrow = connection.fetchrow

    async def fetchrow(sql, *args):
        row = await original_fetchrow(sql, *args)
        if "FROM switchboard.goals" in sql:
            row["catalog_digest"] = projected.digest
        return row

    connection.fetchrow = fetchrow

    async def current_catalog(conn, principal):
        assert conn.in_transaction
        return projected

    authority.catalog = current_catalog
    listed = await broker.list_tools(connection.goal, token)
    assert tuple(c.identity for c in listed) == (capability.identity,)
    result = await broker.call(connection.goal, token, capability.identity, {})
    assert result.status == "returned" and len(domains.calls) == 1
    assert domains.calls[0][0].capability == capability
    # A current digest change cannot reuse the admitted registration snapshot.
    original_projection = projected
    projected = replace(projected, digest="changed-registration")

    # Keep the stored goal's admission bound to the original final definition.
    async def stored_goal(sql, *args):
        row = await original_fetchrow(sql, *args)
        if "FROM switchboard.goals" in sql:
            row["catalog_digest"] = original_projection.digest
        return row

    connection.fetchrow = stored_goal
    with pytest.raises(BrokerRefused):
        await broker.call(connection.goal, token, capability.identity, {})
    assert len(domains.calls) == 1


async def test_declaration_mode_changes_generation_but_configuration_cannot_erase_it(monkeypatch):
    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    from butlers.config import load_config as real_load_config
    from butlers.modules.calendar import CalendarModule

    def configured(path):
        config = real_load_config(path)
        config.modules["approvals"]["gated_tools"]["calendar_respond"]["mode"] = "default"
        return config

    monkeypatch.setattr("tests.contracts.test_tool_authorization_catalog.load_config", configured)
    first = await _roster_catalog("messenger")
    assert first["calendar_respond"].approval_policy["mode"] == "always_per_event"
    original = CalendarModule.tool_metadata

    def changed(module):
        declarations = original(module)
        if "calendar_respond" in declarations:
            declarations["calendar_respond"].approval_policy = {"mode": "default"}
        return declarations

    monkeypatch.setattr(CalendarModule, "tool_metadata", changed)
    second = await _roster_catalog("messenger")
    assert second.generation_digest != first.generation_digest
    assert (
        second["calendar_respond"].input_schema_digest
        == first["calendar_respond"].input_schema_digest
    )
    # Changing source declaration cannot erase calendar's governing forced mode.
    assert not second["calendar_respond"].authorization_complete

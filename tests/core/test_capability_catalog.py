"""Actual FastMCP publication/reader software contracts; not PostgreSQL evidence."""

from dataclasses import fields, replace

import pytest

from butlers.core.capability_catalog import BrokerCatalogReader
from butlers.core.catalog_publication import CatalogPublisher, generation, plain
from butlers.core.catalog_reader import DOMAINS
from butlers.core.goal_admission import EvidenceRefused
from butlers.core.tool_catalog import RecipientParameters
from tests.contracts.test_catalog_publication import (
    Connection,
    Pool,
    complete_fleet,
    registered_catalog,
)


async def test_final_registry_maps_exact_metadata_without_owner_authority():
    store, owners = await complete_fleet()
    _, original = await registered_catalog("health")
    item = replace(
        original.descriptors[0],
        effect_class="communicate",
        resource_params=("query",),
        selection_bound={"query": True},
        safety_args=("query",),
        output_sensitivity="sensitive",
        recipient_params=RecipientParameters(argument_names=("query",)),
        approval_policy={"mode": "always_per_event", "enabled": True, "configured_gate": True},
        idempotency="key_required",
    )
    native = replace(
        original,
        descriptors=(item,),
        generation_digest=generation(
            [{f.name: plain(getattr(item, f.name)) for f in fields(item)}]
        ),
    )
    await CatalogPublisher(Pool(store, "health"), "health").publish(native)
    connection = Connection(store, "switchboard")
    async with connection.transaction():
        mapped = await BrokerCatalogReader().load(connection)
    assert len(mapped.capabilities) == len(DOMAINS)
    capability = next(c for c in mapped.capabilities if c.domain == "health")
    assert capability.approval_mode == "always_per_event"
    assert capability.descriptor.approval_policy["enabled"] is True
    assert capability.descriptor.approval_policy["configured_gate"] is True
    assert capability.descriptor.idempotency == "key_required"
    assert capability.descriptor.resource_params == ("query",)
    assert dict(capability.descriptor.selection_bound) == {"query": True}
    assert capability.descriptor.recipient_params.argument_names == ("query",)
    descriptor = capability.descriptor
    assert descriptor.safety_args == ("query",)
    assert descriptor.output_sensitivity == "sensitive"
    assert capability.input_schema == plain(item.input_schema)
    assert any("AS registrations" in sql for sql, _ in connection.calls)


@pytest.mark.parametrize("fault", ["missing", "expired", "altered", "disabled", "membership"])
async def test_unavailable_or_altered_fleet_refuses_with_positive_control(fault):
    store, _ = await complete_fleet()
    connection = Connection(store, "switchboard")
    async with connection.transaction():
        assert len((await BrokerCatalogReader().load(connection)).capabilities) == len(DOMAINS)
        if fault == "missing":
            del store.current["health"]
        elif fault == "expired":
            store.current["health"]["live"] = False
        elif fault == "altered":
            store.current["health"]["body_digest"] = "0" * 64
        elif fault == "disabled":
            store.enabled = False
        else:
            store.members.pop()
        with pytest.raises(EvidenceRefused, match="catalog_unavailable"):
            await BrokerCatalogReader().load(connection)


async def test_outside_transaction_refuses():
    store, _ = await complete_fleet()
    with pytest.raises(EvidenceRefused):
        await BrokerCatalogReader().load(Connection(store, "switchboard"))


def _republish(row) -> None:
    """Re-seal an edited body so only the descriptor fault can refuse it.

    Every digest the reader checks is recomputed, so a refusal is the reader's
    verdict on the descriptor, not a stale-digest mismatch.
    """
    from butlers.core.catalog_publication import (
        authorization_digest,
        decode_publication,
        digest,
    )

    row["body"]["generation_digest"] = generation(row["body"]["descriptors"])
    row["body_digest"] = digest(row["body"])
    try:
        row["authorization_digest"] = authorization_digest(
            decode_publication(row["body"], row["domain"])
        )
    except Exception:  # noqa: BLE001 - a body the decoder refuses keeps its seal
        pass


@pytest.mark.parametrize(
    "fault",
    [
        "missing_mode",
        "unknown_mode",
        "foreign_owner",
        "recipient",
        "missing_categories",
        "unknown_category",
    ],
)
async def test_invalid_final_authority_descriptor_refuses(fault):
    store, _ = await complete_fleet()
    connection = Connection(store, "switchboard")
    async with connection.transaction():
        assert (await BrokerCatalogReader().load(connection)).capabilities
        row = store.current["health"]
        item = row["body"]["descriptors"][0]
        if fault == "missing_mode":
            item["approval_policy"].pop("mode")
        elif fault == "unknown_mode":
            item["approval_policy"]["mode"] = "sometimes"
        elif fault == "foreign_owner":
            item["owner_domain"] = "finance"
        elif fault == "missing_categories":
            item["categories"] = None
        elif fault == "unknown_category":
            item["categories"] = ["horoscope"]
        else:
            item["recipient_params"]["argument_names"] = ["nonexistent"]
        _republish(row)
        with pytest.raises(EvidenceRefused):
            await BrokerCatalogReader().load(connection)


async def test_the_broker_reads_declared_categories_and_refuses_a_missing_declaration():
    """REQ-capability-authorization-019: missing categories are ungrantable."""
    from butlers.capability_broker.catalog import PublishedCatalogSource
    from butlers.capability_broker.contracts import BrokerRefused, Reason

    store, _ = await complete_fleet()
    connection = Connection(store, "switchboard")
    async with connection.transaction():
        row = store.current["health"]
        item = row["body"]["descriptors"][0]
        catalog = await PublishedCatalogSource().load(connection)
        capability = catalog.get(f"health__{item['canonical_name']}")
        assert capability is not None
        assert capability.categories == frozenset(item["categories"])
        item["categories"] = None
        _republish(row)
        with pytest.raises(BrokerRefused) as refused:
            await PublishedCatalogSource().load(connection)
        assert refused.value.reason is Reason.UNAVAILABLE


async def test_real_messenger_finalizer_publication_maps_declared_mode(monkeypatch):
    from unittest.mock import AsyncMock

    from tests.contracts.test_tool_authorization_catalog import _roster_catalog

    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    native = await _roster_catalog("messenger")
    store, _ = await complete_fleet()
    await CatalogPublisher(Pool(store, "messenger"), "messenger").publish(native)
    connection = Connection(store, "switchboard")
    async with connection.transaction():
        mapped = await BrokerCatalogReader().load(connection)
    response = next(c for c in mapped.capabilities if c.identity == "messenger__calendar_respond")
    assert response.approval_mode == "always_per_event"
    assert response.descriptor.approval_policy["enabled"] is True
    descriptor = response.descriptor
    assert descriptor.approval_policy["mode"] == "always_per_event"
    assert response.descriptor.recipient_params == descriptor.recipient_params

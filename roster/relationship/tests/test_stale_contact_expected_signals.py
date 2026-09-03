"""Relationship producer resolution and server-attestation contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.core.expected_signals import ExpectedSignalState
from butlers.identity import ResolvedContact
from butlers.tools.relationship import stale_contacts

pytestmark = pytest.mark.unit


def _fact(attestation: object) -> dict[str, object]:
    return {"metadata": {"expected_signal_source": attestation}}


def _connector_attestation(
    *, endpoint: str = "gmail:owner@example.com", source_identity: str = "friend@example.com"
) -> dict[str, str]:
    return {
        "producer": "connector:gmail",
        "source_channel": "email",
        "source_endpoint_identity": endpoint,
        "source_identity": source_identity,
        "writer": "interaction_sync",
    }


def _evaluation(**kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(
        state=kwargs.get("state", ExpectedSignalState.ABSENT),
        unmeasurable_reason=kwargs.get("unmeasurable_reason"),
    )


def _mock_identity_producers(
    monkeypatch: pytest.MonkeyPatch,
    *producers: str,
) -> None:
    monkeypatch.setattr(
        stale_contacts,
        "_active_identity_producers",
        AsyncMock(return_value=set(producers)),
    )


def test_interaction_sync_attestation_requires_exact_endpoint() -> None:
    assert (
        stale_contacts.interaction_sync_attestation(
            source_channel="email",
            source_endpoint_identity=None,
            source_identity="friend@example.com",
        )
        is None
    )


def test_interaction_sync_attestation_maps_only_supported_channels() -> None:
    assert (
        stale_contacts.interaction_sync_attestation(
            source_channel="calendar",
            source_endpoint_identity="calendar:one",
            source_identity="friend@example.com",
        )
        is None
    )
    assert stale_contacts.interaction_sync_attestation(
        source_channel="telegram_user_client",
        source_endpoint_identity="telegram:account-a",
        source_identity="12345",
    ) == {
        "producer": "connector:telegram_user_client",
        "source_channel": "telegram_user_client",
        "source_endpoint_identity": "telegram:account-a",
        "source_identity": "12345",
        "writer": "interaction_sync",
    }


async def test_exact_corroborated_endpoint_is_forwarded_to_shared_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact_id = uuid4()
    entity_id = uuid4()
    observed_at = datetime(2026, 9, 1, tzinfo=UTC)
    pool = AsyncMock()
    pool.fetch.return_value = [_fact(_connector_attestation())]
    resolver = AsyncMock(
        return_value={
            ("email", "friend@example.com"): ResolvedContact(
                contact_id=None,
                name="Friend",
                roles=[],
                entity_id=entity_id,
            )
        }
    )
    upsert = AsyncMock(return_value=_evaluation())
    _mock_identity_producers(monkeypatch, "connector:gmail")
    monkeypatch.setattr(stale_contacts, "resolve_contacts_by_channel_bulk", resolver)
    monkeypatch.setattr(stale_contacts, "upsert_expected_signal", upsert)

    signal = await stale_contacts.evaluate_stale_contact_signal(
        pool,
        contact_id=contact_id,
        entity_id=entity_id,
        expected_cadence=timedelta(days=14),
        last_observed_at=observed_at,
    )

    assert signal.is_overdue is True
    resolver.assert_awaited_once_with(
        pool,
        [("email", "friend@example.com")],
        raise_on_error=True,
    )
    assert upsert.await_args.kwargs["producer"] == "connector:gmail"
    assert upsert.await_args.kwargs["producer_endpoint_identity"] == "gmail:owner@example.com"


@pytest.mark.parametrize(
    ("source_channel", "producer", "endpoint", "source_identity"),
    [
        ("email", "connector:gmail", "gmail:dead-account", "friend@example.com"),
        (
            "telegram_user_client",
            "connector:telegram_user_client",
            "telegram:dead-account",
            "12345",
        ),
        (
            "whatsapp_user_client",
            "connector:whatsapp_user_client",
            "whatsapp:dead-account",
            "6591234567@s.whatsapp.net",
        ),
    ],
)
async def test_healthy_sibling_cannot_replace_attested_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    source_channel: str,
    producer: str,
    endpoint: str,
    source_identity: str,
) -> None:
    """Relationship passes the observed endpoint, never a provider aggregate."""
    entity_id = uuid4()
    _mock_identity_producers(monkeypatch, producer)
    pool = AsyncMock()
    pool.fetch.return_value = [
        _fact(
            {
                "producer": producer,
                "source_channel": source_channel,
                "source_endpoint_identity": endpoint,
                "source_identity": source_identity,
                "writer": "interaction_sync",
            }
        )
    ]
    monkeypatch.setattr(
        stale_contacts,
        "resolve_contacts_by_channel_bulk",
        AsyncMock(
            return_value={
                (source_channel, source_identity): ResolvedContact(
                    contact_id=None,
                    name="Friend",
                    roles=[],
                    entity_id=entity_id,
                )
            }
        ),
    )

    async def _exact_endpoint_helper(_pool: object, **kwargs: object) -> SimpleNamespace:
        assert kwargs["producer_endpoint_identity"] == endpoint
        assert kwargs["producer"] == producer
        return _evaluation(
            state=ExpectedSignalState.UNMEASURABLE,
            unmeasurable_reason="producer_not_healthy",
        )

    monkeypatch.setattr(stale_contacts, "upsert_expected_signal", _exact_endpoint_helper)
    signal = await stale_contacts.evaluate_stale_contact_signal(
        pool,
        contact_id=uuid4(),
        entity_id=entity_id,
        expected_cadence=timedelta(days=14),
        last_observed_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    assert signal.evaluation.state is ExpectedSignalState.UNMEASURABLE


async def test_tied_latest_endpoints_fail_closed_independent_of_row_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_id = uuid4()
    _mock_identity_producers(monkeypatch, "connector:gmail")
    rows = [
        _fact(_connector_attestation(endpoint="gmail:a@example.com")),
        _fact(_connector_attestation(endpoint="gmail:b@example.com")),
    ]
    pool = AsyncMock()
    resolver_result = {
        ("email", "friend@example.com"): ResolvedContact(
            contact_id=None,
            name="Friend",
            roles=[],
            entity_id=entity_id,
        )
    }
    monkeypatch.setattr(
        stale_contacts,
        "resolve_contacts_by_channel_bulk",
        AsyncMock(return_value=resolver_result),
    )
    upsert = AsyncMock(return_value=_evaluation(state=ExpectedSignalState.UNMEASURABLE))
    monkeypatch.setattr(stale_contacts, "upsert_expected_signal", upsert)

    for ordered_rows in (rows, list(reversed(rows))):
        pool.fetch.return_value = ordered_rows
        await stale_contacts.evaluate_stale_contact_signal(
            pool,
            contact_id=uuid4(),
            entity_id=entity_id,
            expected_cadence=timedelta(days=14),
            last_observed_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        assert upsert.await_args.kwargs["producer"] == "unknown"
        assert upsert.await_args.kwargs["producer_endpoint_identity"] is None


async def test_mixed_owner_and_connector_authority_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_id = uuid4()
    _mock_identity_producers(monkeypatch, "connector:gmail")
    pool = AsyncMock()
    pool.fetch.return_value = [
        _fact(stale_contacts.owner_attestation(principal="owner")),
        _fact(_connector_attestation()),
    ]
    monkeypatch.setattr(
        stale_contacts,
        "resolve_contacts_by_channel_bulk",
        AsyncMock(
            return_value={
                ("email", "friend@example.com"): ResolvedContact(
                    contact_id=None,
                    name="Friend",
                    roles=[],
                    entity_id=entity_id,
                )
            }
        ),
    )
    upsert = AsyncMock(return_value=_evaluation(state=ExpectedSignalState.UNMEASURABLE))
    monkeypatch.setattr(stale_contacts, "upsert_expected_signal", upsert)

    await stale_contacts.evaluate_stale_contact_signal(
        pool,
        contact_id=uuid4(),
        entity_id=entity_id,
        expected_cadence=timedelta(days=14),
        last_observed_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert upsert.await_args.kwargs["producer"] == "unknown"


@pytest.mark.parametrize(
    "other_producer",
    ["connector:telegram_user_client", "connector:whatsapp_user_client"],
)
async def test_latest_gmail_cannot_hide_other_active_mapped_identity(
    monkeypatch: pytest.MonkeyPatch,
    other_producer: str,
) -> None:
    entity_id = uuid4()
    pool = AsyncMock()
    pool.fetch.return_value = [_fact(_connector_attestation())]
    _mock_identity_producers(monkeypatch, "connector:gmail", other_producer)
    monkeypatch.setattr(
        stale_contacts,
        "resolve_contacts_by_channel_bulk",
        AsyncMock(
            return_value={
                ("email", "friend@example.com"): ResolvedContact(
                    contact_id=None,
                    name="Friend",
                    roles=[],
                    entity_id=entity_id,
                )
            }
        ),
    )
    upsert = AsyncMock(return_value=_evaluation(state=ExpectedSignalState.UNMEASURABLE))
    monkeypatch.setattr(stale_contacts, "upsert_expected_signal", upsert)

    signal = await stale_contacts.evaluate_stale_contact_signal(
        pool,
        contact_id=uuid4(),
        entity_id=entity_id,
        expected_cadence=timedelta(days=14),
        last_observed_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert signal.is_overdue is False
    assert upsert.await_args.kwargs["producer"] == "unknown"
    assert upsert.await_args.kwargs["producer_endpoint_identity"] is None


async def test_unreadable_complete_identity_inventory_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_id = uuid4()
    pool = AsyncMock()
    pool.fetch.return_value = [_fact(_connector_attestation())]
    monkeypatch.setattr(
        stale_contacts,
        "_active_identity_producers",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        stale_contacts,
        "resolve_contacts_by_channel_bulk",
        AsyncMock(
            return_value={
                ("email", "friend@example.com"): ResolvedContact(
                    contact_id=None,
                    name="Friend",
                    roles=[],
                    entity_id=entity_id,
                )
            }
        ),
    )
    upsert = AsyncMock(return_value=_evaluation(state=ExpectedSignalState.UNMEASURABLE))
    monkeypatch.setattr(stale_contacts, "upsert_expected_signal", upsert)

    await stale_contacts.evaluate_stale_contact_signal(
        pool,
        contact_id=uuid4(),
        entity_id=entity_id,
        expected_cadence=timedelta(days=14),
        last_observed_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert upsert.await_args.kwargs["producer"] == "unknown"


async def test_unreadable_provenance_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = AsyncMock()
    pool.fetch.side_effect = RuntimeError("facts unavailable")
    upsert = AsyncMock(return_value=_evaluation(state=ExpectedSignalState.UNMEASURABLE))
    monkeypatch.setattr(stale_contacts, "upsert_expected_signal", upsert)

    await stale_contacts.evaluate_stale_contact_signal(
        pool,
        contact_id=uuid4(),
        entity_id=uuid4(),
        expected_cadence=timedelta(days=14),
        last_observed_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert upsert.await_args.kwargs["producer"] == "unknown"


@pytest.mark.parametrize(
    "attestation",
    [
        None,
        {"producer": "connector:discord"},
        {
            "producer": "connector:gmail",
            "source_channel": "email",
            "source_identity": "friend@example.com",
            "writer": "interaction_sync",
        },
    ],
)
async def test_missing_unsupported_or_endpointless_provenance_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
    attestation: object,
) -> None:
    pool = AsyncMock()
    pool.fetch.return_value = (
        [_fact(attestation)] if attestation is not None else [{"metadata": {}}]
    )
    upsert = AsyncMock(return_value=_evaluation(state=ExpectedSignalState.UNMEASURABLE))
    monkeypatch.setattr(stale_contacts, "upsert_expected_signal", upsert)

    await stale_contacts.evaluate_stale_contact_signal(
        pool,
        contact_id=uuid4(),
        entity_id=uuid4(),
        expected_cadence=timedelta(days=14),
        last_observed_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert upsert.await_args.kwargs["producer"] == "unknown"


async def test_server_attested_owner_observation_is_measurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = AsyncMock()
    _mock_identity_producers(monkeypatch)
    pool.fetch.return_value = [_fact(stale_contacts.owner_attestation(principal="owner"))]
    upsert = AsyncMock(return_value=_evaluation())
    monkeypatch.setattr(stale_contacts, "upsert_expected_signal", upsert)

    await stale_contacts.evaluate_stale_contact_signal(
        pool,
        contact_id=uuid4(),
        entity_id=uuid4(),
        expected_cadence=timedelta(days=14),
        last_observed_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert upsert.await_args.kwargs["producer"] == "owner"
    assert upsert.await_args.kwargs["producer_endpoint_identity"] is None


async def test_public_interaction_metadata_cannot_assert_reserved_source() -> None:
    from butlers.tools.relationship.interactions import interaction_log

    with pytest.raises(ValueError, match="reserved for server writers"):
        await interaction_log(
            AsyncMock(),
            uuid4(),
            "email",
            metadata={"expected_signal_source": _connector_attestation()},
        )

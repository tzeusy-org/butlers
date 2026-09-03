"""Migrated-PostgreSQL exact-endpoint matrix for Relationship stale contacts."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from butlers.core.expected_signals import ExpectedSignalState
from butlers.identity import ResolvedContact
from butlers.tools.relationship import stale_contacts

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _apply_migrations(pool) -> None:
    root = Path(__file__).resolve().parents[2]
    for filename in (
        "core_210_expected_signals.py",
        "core_211_expected_signal_endpoint_identity.py",
    ):
        path = root / "alembic/versions/core" / filename
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        statements: list[str] = []
        mocked_op = MagicMock()
        mocked_op.execute.side_effect = statements.append
        with patch.object(module, "op", mocked_op):
            module.upgrade()
        for statement in statements:
            await pool.execute(statement)


@pytest.mark.parametrize(
    ("source_channel", "producer", "endpoint", "source_identity"),
    [
        ("email", "connector:gmail", "gmail:user:dead", "friend@example.invalid"),
        (
            "telegram_user_client",
            "connector:telegram_user_client",
            "telegram:user:dead",
            "12345",
        ),
        (
            "whatsapp_user_client",
            "connector:whatsapp_user_client",
            "whatsapp:user:dead",
            "6591234567@s.whatsapp.net",
        ),
    ],
)
@pytest.mark.parametrize("reverse_liveness", [False, True])
async def test_dead_attested_endpoint_is_unmeasurable_with_healthy_sibling(
    provisioned_postgres_pool,
    monkeypatch: pytest.MonkeyPatch,
    source_channel: str,
    producer: str,
    endpoint: str,
    source_identity: str,
    reverse_liveness: bool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await _apply_migrations(pool)
        await pool.execute(
            """
            CREATE TABLE public.facts (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_id uuid NOT NULL,
                predicate text NOT NULL,
                scope text NOT NULL,
                validity text NOT NULL,
                valid_at timestamptz NOT NULL,
                metadata jsonb NOT NULL
            );
            CREATE SCHEMA relationship;
            CREATE TABLE relationship.entity_facts (
                subject uuid NOT NULL,
                predicate text NOT NULL,
                object text NOT NULL,
                object_kind text NOT NULL,
                validity text NOT NULL
            );
            CREATE TABLE public._relationship_test_liveness (
                connector_type text NOT NULL,
                endpoint_identity text NOT NULL,
                state text NOT NULL,
                last_heartbeat_at timestamptz
            );
            CREATE VIEW public.v_qa_connector_state AS
            SELECT connector_type, endpoint_identity, state, last_heartbeat_at
            FROM public._relationship_test_liveness
            """
        )
        entity_id = uuid4()
        contact_id = uuid4()
        observed_at = datetime.now(UTC) - timedelta(days=30)
        await pool.execute(
            """
            INSERT INTO public.facts (
                entity_id, predicate, scope, validity, valid_at, metadata
            ) VALUES ($1, 'interaction_message', 'relationship', 'active', $2, $3)
            """,
            entity_id,
            observed_at,
            {
                "expected_signal_source": {
                    "producer": producer,
                    "source_channel": source_channel,
                    "source_endpoint_identity": endpoint,
                    "source_identity": source_identity,
                    "writer": "interaction_sync",
                }
            },
        )
        identity_predicate = "has-email" if source_channel == "email" else "has-handle"
        identity_value = (
            f"telegram:{source_identity}"
            if source_channel == "telegram_user_client"
            else source_identity
        )
        await pool.execute(
            "INSERT INTO relationship.entity_facts VALUES ($1, $2, $3, 'literal', 'active')",
            entity_id,
            identity_predicate,
            identity_value,
        )
        connector_type = producer.removeprefix("connector:")
        rows = [
            (connector_type, endpoint, "offline", datetime.now(UTC)),
            (
                connector_type,
                endpoint.replace("dead", "healthy"),
                "healthy",
                datetime.now(UTC),
            ),
        ]
        if reverse_liveness:
            rows.reverse()
        await pool.executemany(
            "INSERT INTO public._relationship_test_liveness VALUES ($1, $2, $3, $4)", rows
        )
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

        signal = await stale_contacts.evaluate_stale_contact_signal(
            pool,
            contact_id=contact_id,
            entity_id=entity_id,
            expected_cadence=timedelta(days=14),
            last_observed_at=observed_at,
        )

        assert signal.evaluation.state is ExpectedSignalState.UNMEASURABLE
        assert signal.evaluation.unmeasurable_reason == "producer_stale_or_offline"
        assert signal.evaluation.producer_endpoint_identity == endpoint
        assert signal.is_overdue is False
        persisted = await pool.fetchrow(
            "SELECT measurability, producer_endpoint_identity FROM public.expected_signals "
            "WHERE signal_key = $1",
            f"relationship:stale-contact:{contact_id}",
        )
        assert persisted["measurability"] == "unmeasurable"
        assert persisted["producer_endpoint_identity"] == endpoint


@pytest.mark.parametrize("latest_source", ["gmail", "telegram", "whatsapp"])
@pytest.mark.parametrize("reverse_identities", [False, True])
async def test_all_active_mapped_identities_make_mixed_contact_unmeasurable(
    provisioned_postgres_pool,
    monkeypatch: pytest.MonkeyPatch,
    latest_source: str,
    reverse_identities: bool,
) -> None:
    sources = {
        "gmail": (
            "email",
            "connector:gmail",
            "gmail:user:owner",
            "friend@example.invalid",
        ),
        "telegram": (
            "telegram_user_client",
            "connector:telegram_user_client",
            "telegram:user:owner",
            "12345",
        ),
        "whatsapp": (
            "whatsapp_user_client",
            "connector:whatsapp_user_client",
            "whatsapp:user:owner",
            "6591234567@s.whatsapp.net",
        ),
    }
    async with provisioned_postgres_pool() as pool:
        await _apply_migrations(pool)
        await pool.execute(
            """
            CREATE TABLE public.facts (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                entity_id uuid NOT NULL,
                predicate text NOT NULL,
                scope text NOT NULL,
                validity text NOT NULL,
                valid_at timestamptz NOT NULL,
                metadata jsonb NOT NULL
            );
            CREATE SCHEMA relationship;
            CREATE TABLE relationship.entity_facts (
                subject uuid NOT NULL,
                predicate text NOT NULL,
                object text NOT NULL,
                object_kind text NOT NULL,
                validity text NOT NULL
            );
            CREATE TABLE public._relationship_test_liveness (
                connector_type text NOT NULL,
                endpoint_identity text NOT NULL,
                state text NOT NULL,
                last_heartbeat_at timestamptz
            );
            CREATE VIEW public.v_qa_connector_state AS
            SELECT connector_type, endpoint_identity, state, last_heartbeat_at
            FROM public._relationship_test_liveness
            """
        )
        entity_id = uuid4()
        contact_id = uuid4()
        observed_at = datetime.now(UTC) - timedelta(days=30)
        channel, producer, endpoint, source_identity = sources[latest_source]
        await pool.execute(
            """
            INSERT INTO public.facts (
                entity_id, predicate, scope, validity, valid_at, metadata
            ) VALUES ($1, 'interaction_message', 'relationship', 'active', $2, $3)
            """,
            entity_id,
            observed_at,
            {
                "expected_signal_source": {
                    "producer": producer,
                    "source_channel": channel,
                    "source_endpoint_identity": endpoint,
                    "source_identity": source_identity,
                    "writer": "interaction_sync",
                }
            },
        )
        identities = [
            ("has-email", "friend@example.invalid"),
            ("has-handle", "telegram:12345"),
            ("has-handle", "6591234567@s.whatsapp.net"),
        ]
        if reverse_identities:
            identities.reverse()
        await pool.executemany(
            "INSERT INTO relationship.entity_facts VALUES ($1, $2, $3, 'literal', 'active')",
            [(entity_id, predicate, value) for predicate, value in identities],
        )
        monkeypatch.setattr(
            stale_contacts,
            "resolve_contacts_by_channel_bulk",
            AsyncMock(
                return_value={
                    (channel, source_identity): ResolvedContact(
                        contact_id=None,
                        name="Friend",
                        roles=[],
                        entity_id=entity_id,
                    )
                }
            ),
        )

        signal = await stale_contacts.evaluate_stale_contact_signal(
            pool,
            contact_id=contact_id,
            entity_id=entity_id,
            expected_cadence=timedelta(days=14),
            last_observed_at=observed_at,
        )

        assert signal.evaluation.state is ExpectedSignalState.UNMEASURABLE
        assert signal.evaluation.unmeasurable_reason == "producer_unknown"
        assert signal.evaluation.producer_endpoint_identity is None
        assert signal.is_overdue is False

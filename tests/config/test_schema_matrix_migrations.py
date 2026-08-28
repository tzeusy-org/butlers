"""Schema matrix verification for one-db migration runs."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from butlers.config import ButlerConfig, load_config
from butlers.migrations import ROSTER_DIR, has_butler_chain, run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

# Skip all tests if Docker is not available.
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.nightly,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

CORE_TABLES = {
    "state",
    "scheduled_tasks",
    "sessions",
    "route_inbox",
    "butler_secrets",
    "calendar_sources",
    "calendar_events",
    "calendar_event_entities",
    "calendar_event_instances",
    "calendar_sync_cursors",
    "calendar_action_log",
    "delivery_preferences",
    "deferred_notifications",
}

RETIRED_MESSENGER_TRACKING_TABLES = {
    "delivery_requests",
    "delivery_attempts",
    "delivery_receipts",
    "delivery_dead_letter",
}

CHAIN_TABLES: dict[str, set[str]] = {
    "core": CORE_TABLES,
    "finance": {"accounts", "bills", "subscriptions", "transactions"},
    "general": {"collections", "collection_items"},
    "health": {
        "conditions",
        # measurements created by health_001, dropped by health_002
        "medication_doses",
        "medications",
        "research",
        "symptoms",
    },
    # msg_003 retires the unwired delivery-tracking tables. Messenger now has
    # only the shared core tables; delivery outcomes stay in live adapter and
    # Switchboard/approval paths rather than a private tracking schema.
    "messenger": set(),
    "relationship": {
        "addresses",
        # contact_info moved to shared public schema (core_115 dropped per-schema)
        "contact_labels",
        # contacts moved to shared schema by core_007 migration
        # gifts, interactions, loans, notes, activity_feed dropped by rel_009
        "group_members",
        "groups",
        "important_dates",
        "labels",
        "life_event_categories",
        "life_event_types",
        "life_events",
        # quick_facts dropped by rel_025 (bu-6d5v2); ORG/TITLE now in public.contacts
        "relationship_types",
        "relationships",
        # reminders renamed to _reminders_backup by rel_007, then dropped by rel_020
        "tasks",
    },
    "switchboard": {
        "butler_registry",
        "butler_registry_eligibility_log",
        "connector_heartbeat_log",
        "connector_registry",
        # dashboard_audit_log dropped by sw_026 (legacy audit table fully
        # backfilled into public.audit_log by core_124; bu-o699b)
        "dead_letter_queue",
        "extraction_log",
        "extraction_queue",
        # fanout_execution_log dropped by sw_014 (verified-dead feature table)
        "message_inbox",
        "notifications",
        "operator_audit_log",
        "routing_log",
    },
    "approvals": {"approval_events", "approval_rules", "pending_actions"},
    # contacts_source_accounts dropped by contacts_002 (verified-dead feature table)
    "contacts": {"contacts_source_links", "contacts_sync_state"},
    "mailbox": {"mailbox"},
    "education": {
        "mind_maps",
        "mind_map_nodes",
        "mind_map_edges",
        "quiz_responses",
        "analytics_snapshots",
    },
    # entities/contact_info are core identity tables in PUBLIC (core_002), not
    # per-schema memory-chain tables, so they are not asserted here.
    "memory": {
        "episodes",
        "episode_tombstones",
        "facts",
        "rules",
        "memory_links",
        "memory_events",
    },
    "travel": {"trips", "legs", "accommodations", "reservations", "documents"},
    "home": {"ha_entity_snapshot", "ha_command_log", "maintenance_items"},
    # lifestyle has a butler chain but no domain tables in v1 (schema-only).
    "lifestyle": set(),
    "chronicler": {
        "source_adapter_state",
        "projection_checkpoints",
        "point_events",
        "episodes",
        "episode_event_links",
        "episode_entities",
        "overrides",
        "idempotency_keys",
        "tier2_cache",
    },
}


def _load_one_db_roster_configs() -> list[ButlerConfig]:
    configs: list[ButlerConfig] = []
    for entry in sorted(Path(ROSTER_DIR).iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / "butler.toml").exists():
            continue
        cfg = load_config(entry)
        if cfg.db_name == "butlers":
            assert cfg.db_schema is not None, f"{cfg.name}: expected schema in one-db config"
            configs.append(cfg)
    assert configs, "Expected at least one one-db roster config"
    return configs


def _enabled_module_chains(config: ButlerConfig) -> tuple[str, ...]:
    chains: list[str] = []
    for module_name in sorted(config.modules.keys()):
        if module_name in CHAIN_TABLES:
            chains.append(module_name)
    return tuple(chains)


def _module_target_schema(config: ButlerConfig, module_name: str) -> str:
    """Schema a module's migration chain targets for this butler.

    A module MAY declare a private schema via ``memory_schema`` in its
    ``[modules.<name>]`` block (memory module, bu-93y4rt / bu-w6jca): chronicler
    routes its memory chain to ``chronicler_mem`` so the memory ``episodes``
    table does not collide with the domain ``chronicler.episodes``. Every other
    module lands in the butler's own schema. This mirrors ``lifecycle.py`` step
    8, which reads the same validated config field.
    """
    raw = config.modules.get(module_name) or {}
    override = raw.get("memory_schema")
    return override or (config.db_schema or "")


def _expected_schema_matrix(
    configs: list[ButlerConfig],
) -> tuple[dict[str, set[str]], dict[str, tuple[str, ...]]]:
    expected_by_schema: dict[str, set[str]] = {}
    chain_by_schema: dict[str, list[str]] = {}

    def _add(schema: str, chain: str) -> None:
        chain_tables = CHAIN_TABLES.get(chain)
        assert chain_tables is not None, (
            f"Missing CHAIN_TABLES entry for chain={chain!r} schema={schema!r}"
        )
        bucket = expected_by_schema.setdefault(schema, {"alembic_version"})
        bucket.update(chain_tables)
        chain_by_schema.setdefault(schema, []).append(chain)

    for config in configs:
        schema = config.db_schema
        assert schema is not None
        _add(schema, "core")
        if has_butler_chain(config.name):
            _add(schema, config.name)
        # Module chains may target a private override schema (e.g. chronicler_mem).
        for module_chain in _enabled_module_chains(config):
            _add(_module_target_schema(config, module_chain), module_chain)

    return expected_by_schema, {s: tuple(c) for s, c in chain_by_schema.items()}


def _fetch_tables_by_schema(db_url: str, schemas: set[str]) -> dict[str, set[str]]:
    if not schemas:
        return {}

    sql_schemas = ", ".join(f"'{schema}'" for schema in sorted(schemas))
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_schema IN ({sql_schemas})
                      AND table_type = 'BASE TABLE'
                    """
                )
            )
            by_schema: dict[str, set[str]] = {schema: set() for schema in schemas}
            for table_schema, table_name in rows:
                by_schema[str(table_schema)].add(str(table_name))
            return by_schema
    finally:
        engine.dispose()


def test_one_db_schema_table_matrix_for_core_and_enabled_modules(postgres_container):
    """Enabled module + core table sets should exist in every configured one-db schema."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    configs = _load_one_db_roster_configs()

    for config in configs:
        schema = config.db_schema
        assert schema is not None
        asyncio.run(run_migrations(db_url, chain="core", schema=schema))
        if has_butler_chain(config.name):
            asyncio.run(run_migrations(db_url, chain=config.name, schema=schema))
        for module_chain in _enabled_module_chains(config):
            # Route each module chain to its target schema (private override or
            # the butler's own schema) — mirrors lifecycle.py step 8.
            asyncio.run(
                run_migrations(
                    db_url,
                    chain=module_chain,
                    schema=_module_target_schema(config, module_chain),
                )
            )

    expected_by_schema, chain_by_schema = _expected_schema_matrix(configs)
    actual_by_schema = _fetch_tables_by_schema(db_url, set(expected_by_schema.keys()))

    diagnostics: list[str] = []
    for schema in sorted(expected_by_schema):
        expected_tables = expected_by_schema[schema]
        actual_tables = actual_by_schema.get(schema, set())
        missing_tables = sorted(expected_tables - actual_tables)
        if not missing_tables:
            continue

        diagnostics.append(
            f"schema={schema} chains={','.join(chain_by_schema[schema])} "
            f"missing={missing_tables} present={sorted(actual_tables)}"
        )

    assert not diagnostics, (
        "Schema/table migration matrix verification failed. "
        "Each schema must contain all expected core + enabled module tables.\n"
        + "\n".join(diagnostics)
    )

    messenger_tables = actual_by_schema.get("messenger", set())
    retired_messenger_tables = RETIRED_MESSENGER_TRACKING_TABLES & messenger_tables
    assert retired_messenger_tables == set(), (
        "Messenger migration chain must retire unwired delivery tracking tables; "
        f"found={sorted(retired_messenger_tables)}"
    )

    # Chronicler memory isolation (bu-93y4rt / bu-w6jca): the chronicler routes
    # its memory chain to chronicler_mem so the memory `episodes` table never
    # collides with the domain `chronicler.episodes`. Prove the two coexist and
    # that memory-only tables never leak into the chronicler domain schema.
    chronicler_tables = actual_by_schema.get("chronicler", set())
    chronicler_mem_tables = actual_by_schema.get("chronicler_mem", set())
    assert "episodes" in chronicler_tables, "domain chronicler.episodes must exist"
    assert "episodes" in chronicler_mem_tables, "memory chronicler_mem.episodes must exist"
    memory_only = {"facts", "rules", "memory_links", "memory_events"}
    leaked = memory_only & chronicler_tables
    assert leaked == set(), (
        f"memory-only tables leaked into the chronicler domain schema: {sorted(leaked)}"
    )
    assert memory_only <= chronicler_mem_tables, (
        f"memory tables missing from chronicler_mem: {sorted(memory_only - chronicler_mem_tables)}"
    )

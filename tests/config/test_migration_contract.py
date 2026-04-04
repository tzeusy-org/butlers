"""Canonical migration chain-integrity tests covering all migration chains.

Verifies for every migration in every chain:
1. The migration file exists on disk.
2. Revision metadata matches expected values (revision, down_revision, branch_labels, depends_on).
3. upgrade() and downgrade() are callable.

These are pure-unit tests — no Docker / PostgreSQL required.
SQL-string content tests have been removed; schema outcomes are verified by the
Docker integration tests in test_migrations.py and test_schema_matrix_migrations.py.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field

import pytest

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class MigrationSpec:
    chain: str
    filename: str
    revision: str
    down_revision: str | None
    branch_labels: tuple[str, ...] | None
    depends_on: str | tuple[str, ...] | None = field(default=None)

    @property
    def id(self) -> str:
        return f"{self.chain}/{self.filename}"


_MIGRATION_SPECS = [
    # -------------------------------------------------------------------------
    # Core consolidated chain (alembic/versions/core/)
    # -------------------------------------------------------------------------
    MigrationSpec("core", "core_001_foundation.py", "core_001", None, ("core",)),
    MigrationSpec("core", "core_002_identity.py", "core_002", "core_001", None),
    MigrationSpec("core", "core_003_calendar.py", "core_003", "core_002", None),
    MigrationSpec("core", "core_004_model_and_tokens.py", "core_004", "core_003", None),
    MigrationSpec("core", "core_005_self_healing.py", "core_005", "core_004", None),
    MigrationSpec("core", "core_006_dashboard.py", "core_006", "core_005", None),
    MigrationSpec("core", "core_007_connectors.py", "core_007", "core_006", None),
    MigrationSpec("core", "core_008_external_accounts.py", "core_008", "core_007", None),
    MigrationSpec("core", "core_009_memory_catalog.py", "core_009", "core_008", None),
    MigrationSpec("core", "core_010_insight_tables.py", "core_010", "core_009", None),
    MigrationSpec("core", "core_011_steam_play_history_fix.py", "core_011", "core_010", None),
    MigrationSpec("core", "core_012_temporal_intelligence.py", "core_012", "core_011", None),
    MigrationSpec("core", "core_013_event_chains.py", "core_013", "core_012", None),
    MigrationSpec("core", "core_041_seasonal_periods.py", "core_041", "core_013", None),
    MigrationSpec("core", "core_042_user_context.py", "core_042", "core_041", None),
    MigrationSpec("core", "core_043_deadline_columns.py", "core_043", "core_042", None),
    MigrationSpec("core", "core_044_event_chains_status.py", "core_044", "core_043", None),
    MigrationSpec("core", "core_045_steam_cursor_cleanup.py", "core_045", "core_044", None),
    MigrationSpec(
        "core", "core_046_migrate_user_context_to_public.py", "core_046", "core_045", None
    ),
    MigrationSpec("core", "core_047_rename_shared_indexes.py", "core_047", "core_046", None),
    MigrationSpec("core", "core_048_round_robin_counters.py", "core_048", "core_047", None),
    MigrationSpec("core", "core_049_ingestion_replay_pending.py", "core_049", "core_048", None),
    MigrationSpec("core", "core_050_schedule_token_budget.py", "core_050", "core_049", None),
    MigrationSpec("core", "core_051_qa_patrols.py", "core_051", "core_050", None),
    MigrationSpec("core", "core_052_qa_findings.py", "core_052", "core_051", None),
    MigrationSpec("core", "core_053_qa_dismissals.py", "core_053", "core_052", None),
    MigrationSpec(
        "core", "core_054_healing_attempts_qa_patrol_id.py", "core_054", "core_053", None
    ),
    MigrationSpec("core", "core_055_v_qa_recent_failures.py", "core_055", "core_054", None),
    # -------------------------------------------------------------------------
    # Switchboard roster chain (roster/switchboard/migrations/)
    # -------------------------------------------------------------------------
    MigrationSpec("switchboard", "001_switchboard_messaging.py", "sw_001", None, ("switchboard",)),
    MigrationSpec("switchboard", "002_switchboard_operations.py", "sw_002", "sw_001", None),
    MigrationSpec("switchboard", "003_switchboard_routing.py", "sw_003", "sw_002", None),
    MigrationSpec("switchboard", "004_switchboard_email.py", "sw_004", "sw_003", None),
    MigrationSpec("switchboard", "005_switchboard_agent_type.py", "sw_005", "sw_004", None),
    # -------------------------------------------------------------------------
    # Finance roster chain (roster/finance/migrations/)
    # -------------------------------------------------------------------------
    MigrationSpec("finance", "001_finance_tables.py", "finance_001", None, ("finance",)),
    MigrationSpec(
        "finance",
        "002_merchant_mappings_trigram_index.py",
        "finance_002",
        "finance_001",
        None,
    ),
    MigrationSpec(
        "finance",
        "003_merchant_mappings_schema_correction.py",
        "finance_003",
        "finance_002",
        None,
    ),
    MigrationSpec(
        "finance",
        "004_transactions_dedup_constraint.py",
        "finance_004",
        "finance_003",
        None,
    ),
    MigrationSpec("finance", "005_add_csv_dedup_index.py", "finance_005", "finance_004", None),
    MigrationSpec("finance", "006_intelligence_tables.py", "finance_006", "finance_005", None),
    # -------------------------------------------------------------------------
    # Single-file roster chains
    # -------------------------------------------------------------------------
    MigrationSpec("education", "001_education_tables.py", "education_001", None, ("education",)),
    MigrationSpec("general", "001_general_tables.py", "gen_001", None, ("general",)),
    MigrationSpec("health", "001_health_tables.py", "health_001", None, ("health",)),
    MigrationSpec("home", "001_home_tables.py", "home_001", None, ("home",)),
    MigrationSpec("lifestyle", "001_lifestyle_tables.py", "lifestyle_001", None, ("lifestyle",)),
    MigrationSpec("messenger", "001_messenger_tables.py", "msg_001", None, ("messenger",)),
    MigrationSpec("travel", "001_travel_tables.py", "travel_001", None, ("travel",)),
    # -------------------------------------------------------------------------
    # Relationship roster chain (roster/relationship/migrations/)
    # -------------------------------------------------------------------------
    MigrationSpec(
        "relationship",
        "001_relationship_tables.py",
        "rel_001",
        None,
        ("relationship",),
    ),
    MigrationSpec(
        "relationship",
        "002_align_contacts_schema.py",
        "rel_002",
        "rel_001",
        None,
    ),
    MigrationSpec(
        "relationship",
        "003_consolidate_contacts_to_public.py",
        "rel_003",
        "rel_002",
        None,
    ),
    MigrationSpec(
        "relationship",
        "004_contact_detail_indexes.py",
        "rel_004",
        "rel_003",
        None,
    ),
    MigrationSpec("relationship", "005_addresses_table.py", "rel_005", "rel_004", None),
    # -------------------------------------------------------------------------
    # Module chains (src/butlers/modules/<name>/migrations/)
    # -------------------------------------------------------------------------
    MigrationSpec("approvals", "001_approvals_tables.py", "approvals_001", None, ("approvals",)),
    MigrationSpec("contacts", "001_contacts_sync.py", "contacts_001", None, ("contacts",)),
    MigrationSpec(
        "google_drive",
        "001_google_drive_butler_folders.py",
        "google_drive_001",
        None,
        ("google_drive",),
    ),
    MigrationSpec("mailbox", "001_create_mailbox_table.py", "mailbox_001", None, ("mailbox",)),
    MigrationSpec("memory", "001_memory_schema.py", "mem_001", None, ("memory",)),
    MigrationSpec("memory", "002_seed_predicates.py", "mem_002", "mem_001", None),
    MigrationSpec("whatsapp", "001_whatsapp_sessions.py", "whatsapp_001", None, ("whatsapp",)),
]


def _load_migration_module(chain: str, filename: str):
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir(chain)
    assert chain_dir is not None, f"Chain {chain!r} should be resolvable"
    path = chain_dir / filename
    assert path.exists(), f"Migration file {chain}/{filename} should exist"

    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mspec", _MIGRATION_SPECS, ids=lambda m: m.id)
def test_migration_chain_integrity(mspec: MigrationSpec) -> None:
    """Verify file exists, revision metadata matches, and up/downgrade are callable.

    This is the single authoritative chain-integrity test per migration.
    SQL-string content tests have been removed; schema outcomes are verified by
    Docker integration tests in test_migrations.py and test_schema_matrix_migrations.py.
    """
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir(mspec.chain)
    assert chain_dir is not None, f"Chain {mspec.chain!r} should exist"
    assert (chain_dir / mspec.filename).exists(), (
        f"Migration file {mspec.chain}/{mspec.filename} not found"
    )

    module = _load_migration_module(mspec.chain, mspec.filename)

    assert getattr(module, "revision", None) == mspec.revision, f"{mspec.id}: revision mismatch"
    assert getattr(module, "down_revision", None) == mspec.down_revision, (
        f"{mspec.id}: down_revision mismatch"
    )
    assert getattr(module, "branch_labels", None) == mspec.branch_labels, (
        f"{mspec.id}: branch_labels mismatch"
    )
    assert getattr(module, "depends_on", None) == mspec.depends_on, (
        f"{mspec.id}: depends_on mismatch"
    )
    assert hasattr(module, "upgrade") and callable(module.upgrade), (
        f"{mspec.id}: upgrade() missing or not callable"
    )
    assert hasattr(module, "downgrade") and callable(module.downgrade), (
        f"{mspec.id}: downgrade() missing or not callable"
    )

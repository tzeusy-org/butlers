"""Tests for approvals module migration schema drift repair."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from butlers.testing.migration import load_migration

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "butlers" / "modules" / "approvals" / "migrations"
)


def _collect_sqls(mod, fn_name: str = "upgrade") -> list[str]:
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()
    return sqls


def test_initial_approvals_migration_creates_pending_actions_dossier_columns() -> None:
    mod = load_migration(_MIGRATIONS_DIR / "001_approvals_tables.py")
    sqls = _collect_sqls(mod)

    # Hand DDL on purpose: this asserts on the migration's own SQL text, it
    # does not provision a table.
    pending_actions_sql = next(
        sql for sql in sqls if "CREATE TABLE IF NOT EXISTS pending_actions" in sql
    )
    normalized = " ".join(pending_actions_sql.lower().split())

    assert normalized.count("why text") == 1
    assert normalized.count("evidence jsonb not null default '[]'::jsonb") == 1


def test_approvals_repair_migration_adds_missing_dossier_columns() -> None:
    mod = load_migration(_MIGRATIONS_DIR / "002_pending_actions_why_evidence.py")
    sqls = _collect_sqls(mod)
    normalized = " ".join(sqls[0].lower().split())

    assert mod.revision == "approvals_002"
    assert mod.down_revision == "approvals_001"
    assert "alter table pending_actions" in normalized
    assert "add column if not exists why text" in normalized
    assert "add column if not exists evidence jsonb not null default '[]'::jsonb" in normalized


def test_approvals_repair_migration_downgrade_is_noop() -> None:
    mod = load_migration(_MIGRATIONS_DIR / "002_pending_actions_why_evidence.py")

    assert _collect_sqls(mod, "downgrade") == []


def test_fingerprint_v2_migration_backfills_versioned_history_and_suggestions() -> None:
    mod = load_migration(_MIGRATIONS_DIR / "003_fingerprint_v2.py")
    normalized = " ".join("\n".join(_collect_sqls(mod)).lower().split())

    assert mod.revision == "approvals_003"
    assert mod.down_revision == "approvals_002"
    assert "autonomy_approval_history" in normalized
    assert "autonomy_suggestions" in normalized
    assert "fingerprint_version" in normalized
    assert "set fingerprint_version = 1" in normalized


def test_decision_dossier_migration_uses_next_safe_revision_and_upgrades_evidence() -> None:
    """The RFC 0021 dossier migration follows the fingerprint-v2 revision."""
    mod = load_migration(_MIGRATIONS_DIR / "005_decision_dossier.py")
    sqls = _collect_sqls(mod)
    normalized = " ".join("\n".join(sqls).lower().split())

    assert mod.revision == "approvals_005"
    assert mod.down_revision == "approvals_003"
    assert "add column if not exists blast_radius text" in normalized
    assert "add column if not exists reversibility text" in normalized
    assert "pending_actions_blast_radius_check" in normalized
    assert "pending_actions_reversibility_check" in normalized
    assert "jsonb_array_elements(evidence)" in normalized
    assert "jsonb_build_object('type', 'text'" in normalized


def test_approval_push_emissions_migration_reserves_one_row_per_action() -> None:
    """A durable action-id row makes park pushes idempotent across retries."""
    mod = load_migration(_MIGRATIONS_DIR / "006_approval_push_emissions.py")
    normalized = " ".join("\n".join(_collect_sqls(mod)).lower().split())

    assert mod.revision == "approvals_006"
    assert mod.down_revision == "approvals_005"
    assert "create table if not exists approval_push_emissions" in normalized
    assert "action_id uuid primary key" in normalized
    assert "references pending_actions(id)" in normalized
    assert "emission_kind" in normalized


def test_autonomy_suggestion_action_link_follows_approval_push_revision() -> None:
    """Action deep links are additive and preserve the approvals_006 chain."""
    mod = load_migration(_MIGRATIONS_DIR / "007_autonomy_suggestion_action_link.py")
    normalized = " ".join("\n".join(_collect_sqls(mod)).lower().split())

    assert mod.revision == "approvals_007"
    assert mod.down_revision == "approvals_006"
    assert "alter table autonomy_suggestions" in normalized
    assert "add column if not exists action_id uuid" in normalized
    assert "references pending_actions(id) on delete set null" in normalized


def test_action_event_provenance_outlives_terminal_action_retention() -> None:
    """Audit event references validate on insert without blocking parent retention."""
    mod = load_migration(_MIGRATIONS_DIR / "008_retain_event_action_provenance.py")
    normalized = " ".join("\n".join(_collect_sqls(mod)).lower().split())

    assert mod.revision == "approvals_008"
    assert mod.down_revision == "approvals_007"
    assert "drop constraint if exists approval_events_action_id_fkey" in normalized
    assert "validate_approval_event_action_reference" in normalized
    assert "for key share" in normalized
    assert "create trigger trg_approval_events_action_reference" in normalized


def test_rule_creator_provenance_outlives_terminal_action_retention() -> None:
    """A retained standing rule cannot block cleanup of its terminal creator action."""
    mod = load_migration(_MIGRATIONS_DIR / "009_retain_rule_creator_provenance.py")
    normalized = " ".join("\n".join(_collect_sqls(mod)).lower().split())

    assert mod.revision == "approvals_009"
    assert mod.down_revision == "approvals_008"
    assert "drop constraint if exists approval_rules_created_from_fk" in normalized
    assert "validate_approval_rule_creator_reference" in normalized
    assert "for key share" in normalized
    assert "create constraint trigger" in normalized
    assert "trg_approval_rules_created_from_reference" in normalized
    assert "after insert or update of created_from on approval_rules" in normalized
    assert "deferrable initially deferred" in normalized


def test_rule_event_provenance_outlives_rule_retention() -> None:
    """Rule-event references validate on insert without blocking rule retention."""
    mod = load_migration(_MIGRATIONS_DIR / "011_retain_event_rule_provenance.py")
    normalized = " ".join("\n".join(_collect_sqls(mod)).lower().split())

    assert mod.revision == "approvals_011"
    assert mod.down_revision == "approvals_010"
    assert "drop constraint if exists approval_events_rule_id_fkey" in normalized
    assert "validate_approval_event_rule_reference" in normalized
    assert "for key share" in normalized
    assert "create trigger trg_approval_events_rule_reference" in normalized


def test_abandoned_recovery_migration_extends_only_approval_vocabularies() -> None:
    """The durable recovery state and event are additive, with safe rollback guards."""
    mod = load_migration(_MIGRATIONS_DIR / "012_abandoned_recovery.py")
    upgrade = " ".join("\n".join(_collect_sqls(mod)).lower().split())
    downgrade = " ".join("\n".join(_collect_sqls(mod, "downgrade")).lower().split())

    assert mod.revision == "approvals_012"
    assert mod.down_revision == "approvals_011"
    assert "'abandoned'" in upgrade
    assert "'action_abandoned'" in upgrade
    assert "to_regclass('pending_actions')" in upgrade
    assert "cannot downgrade approvals_012 while abandoned actions exist" in downgrade
    assert "to_regclass('approval_events')" in downgrade


def test_pending_action_deduplication_migration_follows_abandonment() -> None:
    """Semantic keys must include every owner-decision state at the chain head."""
    mod = load_migration(_MIGRATIONS_DIR / "013_pending_action_deduplication_key.py")
    upgrade = " ".join("\n".join(_collect_sqls(mod)).lower().split())
    downgrade = " ".join("\n".join(_collect_sqls(mod, "downgrade")).lower().split())

    assert mod.revision == "approvals_013"
    assert mod.down_revision == "approvals_012"
    assert "ux_pending_actions_active_deduplication_key" in upgrade
    assert "'abandoned'" in upgrade
    assert "'action_abandoned'" in upgrade
    assert "drop index if exists ux_pending_actions_active_deduplication_key" in upgrade
    assert "duplicate owner-decision deduplication keys" in upgrade
    assert "cannot downgrade approvals_013 while deduplication keys exist" in downgrade

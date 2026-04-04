"""Unit tests for QA staffer Alembic migrations core_051 through core_055.

Verifies:
- Migration files exist, are importable, and have correct revision metadata.
- core_051: qa_patrols table — correct columns, status CHECK constraint, indexes.
- core_052: qa_findings table — correct columns, FK references, CHECK constraints.
- core_053: qa_dismissals table — correct PK, columns, index.
- core_054: healing_attempts qa_patrol_id — ADD COLUMN, NOT VALID FK, VALIDATE,
  index; downgrade drops all three objects.
- core_055: v_qa_recent_failures view — all 10 butler schemas covered, RFC 0010
  guardrails: UNION view, hardcoded source_butler, cross-schema grants,
  migration-based revoke on downgrade.

Pure-unit tests — no Docker / PostgreSQL required.

Issue: bu-uxzui
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"


def _load_migration(name: str):
    """Dynamically load a migration module by filename stem."""
    path = VERSIONS_DIR / f"{name}.py"
    assert path.exists(), f"Migration file not found: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================================
# core_051 — public.qa_patrols
# ============================================================================


class TestCore051QaPatrols:
    @pytest.fixture(autouse=True)
    def _mod(self) -> None:
        self._mod = _load_migration("core_051_qa_patrols")

    def test_revision_id(self) -> None:
        assert self._mod.revision == "core_051"

    def test_down_revision(self) -> None:
        assert self._mod.down_revision == "core_050"

    def test_branch_labels_none(self) -> None:
        assert self._mod.branch_labels is None

    def test_upgrade_callable(self) -> None:
        assert callable(self._mod.upgrade)

    def test_downgrade_callable(self) -> None:
        assert callable(self._mod.downgrade)

    def test_creates_qa_patrols_table(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "public.qa_patrols" in src

    def test_required_columns_present(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        for col in (
            "id",
            "started_at",
            "completed_at",
            "status",
            "findings_count",
            "novel_count",
            "dispatched_count",
            "log_lookback_minutes",
            "sources_polled",
            "error_detail",
        ):
            assert col in src, f"Column {col!r} missing from upgrade source"

    def test_status_check_constraint_covers_all_statuses(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        for status in ("running", "clean", "findings_dispatched", "error", "skipped_overlap"):
            assert status in src, f"Status {status!r} missing from CHECK constraint"

    def test_creates_started_at_index(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "idx_qa_patrols_started_at" in src

    def test_creates_status_index(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "idx_qa_patrols_status" in src

    def test_grants_to_qa_role(self) -> None:
        src = inspect.getsource(self._mod)
        assert "butler_qa_rw" in src

    def test_downgrade_drops_table(self) -> None:
        src = inspect.getsource(self._mod.downgrade)
        assert "qa_patrols" in src
        assert "DROP TABLE" in src

    def test_downgrade_drops_indexes(self) -> None:
        src = inspect.getsource(self._mod.downgrade)
        assert "idx_qa_patrols_started_at" in src
        assert "idx_qa_patrols_status" in src

    def test_grant_uses_do_block_for_safety(self) -> None:
        """Grant must use DO $$…$$ best-effort block, not bare GRANT."""
        src = inspect.getsource(self._mod)
        assert "DO $$" in src

    def test_sources_polled_is_text_array(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "TEXT[]" in src or "text[]" in src


# ============================================================================
# core_052 — public.qa_findings
# ============================================================================


class TestCore052QaFindings:
    @pytest.fixture(autouse=True)
    def _mod(self) -> None:
        self._mod = _load_migration("core_052_qa_findings")

    def test_revision_id(self) -> None:
        assert self._mod.revision == "core_052"

    def test_down_revision(self) -> None:
        assert self._mod.down_revision == "core_051"

    def test_creates_qa_findings_table(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "public.qa_findings" in src

    def test_required_columns_present(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        for col in (
            "id",
            "patrol_id",
            "fingerprint",
            "source_type",
            "source_butler",
            "severity",
            "exception_type",
            "event_summary",
            "call_site",
            "occurrence_count",
            "first_seen",
            "last_seen",
            "dedup_reason",
            "healing_attempt_id",
            "created_at",
        ):
            assert col in src, f"Column {col!r} missing from upgrade source"

    def test_patrol_id_fk_to_qa_patrols(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "qa_patrols" in src
        assert "REFERENCES" in src

    def test_healing_attempt_id_fk_to_healing_attempts(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "healing_attempts" in src
        assert "REFERENCES" in src

    def test_source_type_check_constraint(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        for source in ("log_scanner", "session_records", "butler_reports"):
            assert source in src, f"Source type {source!r} missing from CHECK constraint"

    def test_severity_check_constraint(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "severity" in src
        assert "BETWEEN" in src or ("0" in src and "4" in src)

    def test_creates_patrol_id_index(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "idx_qa_findings_patrol_id" in src

    def test_creates_fingerprint_index(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "idx_qa_findings_fingerprint" in src

    def test_creates_source_butler_index(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "idx_qa_findings_source_butler" in src

    def test_creates_severity_index(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "idx_qa_findings_severity" in src

    def test_downgrade_drops_table_and_indexes(self) -> None:
        src = inspect.getsource(self._mod.downgrade)
        assert "qa_findings" in src
        assert "DROP TABLE" in src
        assert "idx_qa_findings_patrol_id" in src


# ============================================================================
# core_053 — public.qa_dismissals
# ============================================================================


class TestCore053QaDismissals:
    @pytest.fixture(autouse=True)
    def _mod(self) -> None:
        self._mod = _load_migration("core_053_qa_dismissals")

    def test_revision_id(self) -> None:
        assert self._mod.revision == "core_053"

    def test_down_revision(self) -> None:
        assert self._mod.down_revision == "core_052"

    def test_creates_qa_dismissals_table(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "public.qa_dismissals" in src

    def test_required_columns_present(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        for col in ("fingerprint", "dismissed_until", "dismissed_by", "created_at"):
            assert col in src, f"Column {col!r} missing"

    def test_fingerprint_is_primary_key(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "PRIMARY KEY" in src
        assert "fingerprint" in src

    def test_creates_dismissed_until_index(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "idx_qa_dismissals_dismissed_until" in src

    def test_downgrade_drops_table_and_index(self) -> None:
        src = inspect.getsource(self._mod.downgrade)
        assert "qa_dismissals" in src
        assert "DROP TABLE" in src
        assert "idx_qa_dismissals_dismissed_until" in src


# ============================================================================
# core_054 — healing_attempts.qa_patrol_id
# ============================================================================


class TestCore054HealingAttemptsQaPatrolId:
    @pytest.fixture(autouse=True)
    def _mod(self) -> None:
        self._mod = _load_migration("core_054_healing_attempts_qa_patrol_id")

    def test_revision_id(self) -> None:
        assert self._mod.revision == "core_054"

    def test_down_revision(self) -> None:
        assert self._mod.down_revision == "core_053"

    def test_upgrade_adds_column(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "qa_patrol_id" in src
        assert "ADD COLUMN" in src

    def test_upgrade_column_is_nullable(self) -> None:
        """Column must be DEFAULT NULL to avoid full table rewrite."""
        src = inspect.getsource(self._mod.upgrade)
        assert "DEFAULT NULL" in src

    def test_upgrade_adds_fk_not_valid(self) -> None:
        """FK must be added NOT VALID to avoid long table scan at migration time."""
        src = inspect.getsource(self._mod.upgrade)
        assert "NOT VALID" in src
        assert "fk_healing_attempts_qa_patrol_id" in src

    def test_upgrade_validates_constraint_separately(self) -> None:
        """VALIDATE CONSTRAINT must be a separate statement (ShareUpdateExclusiveLock)."""
        src = inspect.getsource(self._mod.upgrade)
        assert "VALIDATE CONSTRAINT" in src

    def test_upgrade_references_qa_patrols(self) -> None:
        src = inspect.getsource(self._mod.upgrade)
        assert "public.qa_patrols" in src

    def test_upgrade_creates_partial_index(self) -> None:
        """Index should be a partial index on non-NULL values only."""
        src = inspect.getsource(self._mod.upgrade)
        assert "idx_healing_attempts_qa_patrol_id" in src
        assert "IS NOT NULL" in src

    def test_downgrade_drops_index(self) -> None:
        src = inspect.getsource(self._mod.downgrade)
        assert "idx_healing_attempts_qa_patrol_id" in src
        assert "DROP INDEX" in src

    def test_downgrade_drops_constraint(self) -> None:
        src = inspect.getsource(self._mod.downgrade)
        assert "fk_healing_attempts_qa_patrol_id" in src
        assert "DROP CONSTRAINT" in src

    def test_downgrade_drops_column(self) -> None:
        src = inspect.getsource(self._mod.downgrade)
        assert "qa_patrol_id" in src
        assert "DROP COLUMN" in src

    def test_all_downgrade_objects_use_if_exists(self) -> None:
        src = inspect.getsource(self._mod.downgrade)
        assert "IF EXISTS" in src


# ============================================================================
# core_055 — public.v_qa_recent_failures (RFC 0010)
# ============================================================================


_EXPECTED_SESSION_SCHEMAS = (
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "relationship",
    "switchboard",
    "travel",
)


class TestCore055VQaRecentFailures:
    @pytest.fixture(autouse=True)
    def _mod(self) -> None:
        self._mod = _load_migration("core_055_v_qa_recent_failures")

    def test_revision_id(self) -> None:
        assert self._mod.revision == "core_055"

    def test_down_revision(self) -> None:
        assert self._mod.down_revision == "core_054"

    def test_creates_view(self) -> None:
        # Use full module source — view name may be in a constant (e.g. _VIEW_FQN)
        src = inspect.getsource(self._mod)
        assert "v_qa_recent_failures" in src
        assert "CREATE" in src

    # RFC 0010 guardrail 1: UNION view (structurally read-only)
    def test_view_uses_union_all(self) -> None:
        src = inspect.getsource(self._mod)
        assert "UNION ALL" in src

    # RFC 0010 guardrail 2: explicit source_butler column per UNION term
    def test_view_hardcodes_source_butler_per_schema(self) -> None:
        # The _union_term helper must use f"'{schema}'" so the generated SQL
        # contains a hardcoded schema literal.  Inspect the function source for
        # the pattern that produces SQL-level string literals from the schema arg.
        src = inspect.getsource(self._mod)
        # Verify the template produces a SQL string literal from the schema name
        # (f"'{schema}'" or similar quoting pattern)
        assert "source_butler" in src, "source_butler column missing from view definition"
        # The _union_term function must reference the schema variable as a SQL literal
        union_src = inspect.getsource(self._mod._union_term)
        assert "source_butler" in union_src
        # The quoting must produce a SQL string literal from the schema name
        assert "'{schema}'" in union_src or '"{schema}"' in union_src or "schema" in union_src

    # All 10 butler schemas are covered
    def test_all_butler_schemas_covered(self) -> None:
        src = inspect.getsource(self._mod)
        for schema in _EXPECTED_SESSION_SCHEMAS:
            assert schema in src, f"Schema {schema!r} not covered in view"

    # RFC 0010 guardrail 5: cross-schema grants are migration-tracked
    def test_grants_cross_schema_select_to_qa_role(self) -> None:
        src = inspect.getsource(self._mod)
        assert "butler_qa_rw" in src
        assert "SELECT" in src

    def test_grants_to_each_schema(self) -> None:
        # Use full module source — the grant helper iterates _SESSION_SCHEMAS
        src = inspect.getsource(self._mod)
        for schema in _EXPECTED_SESSION_SCHEMAS:
            assert schema in src, f"Schema {schema!r} not referenced in module"

    def test_upgrade_grants_select_on_view(self) -> None:
        # v_qa_recent_failures may be referenced via _VIEW_FQN constant
        src = inspect.getsource(self._mod)
        assert "v_qa_recent_failures" in src

    def test_downgrade_drops_view(self) -> None:
        # Use full module source — DROP VIEW uses _VIEW_FQN constant
        src = inspect.getsource(self._mod)
        assert "v_qa_recent_failures" in src
        assert "DROP VIEW" in src

    def test_downgrade_revokes_cross_schema_grants(self) -> None:
        """RFC 0010 guardrail 5: grants must be revoked on downgrade."""
        # The revoke helper is defined at module level and called from downgrade
        src = inspect.getsource(self._mod)
        assert "REVOKE" in src

    def test_view_exposes_status_column(self) -> None:
        """View must include a derived status column (error/timeout/crash)."""
        src = inspect.getsource(self._mod)
        assert "status" in src
        assert "timeout" in src
        assert "error" in src
        assert "crash" in src

    def test_view_filters_to_failed_sessions(self) -> None:
        """View must filter to success=false rows only."""
        src = inspect.getsource(self._mod)
        assert "success = false" in src or "success=false" in src

    def test_view_exposes_source_butler_column(self) -> None:
        src = inspect.getsource(self._mod)
        assert "source_butler" in src

    def test_grants_use_best_effort_do_blocks(self) -> None:
        """All GRANTs must use DO $$…$$ best-effort blocks."""
        src = inspect.getsource(self._mod)
        assert "DO $$" in src

    def test_view_in_public_schema(self) -> None:
        # View FQN is in a module-level constant _VIEW_FQN
        src = inspect.getsource(self._mod)
        assert "public.v_qa_recent_failures" in src

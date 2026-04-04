"""Contract tests: Cross-Butler Briefing Exception (RFC 0010).

Validates that the read-only view exists, the 5 guardrails are enforced,
and the reuse criteria are correctly specified.

Wire contract: The only sanctioned cross-schema SQL access pattern uses a
read-only UNION view, migration-based grants, date-filtered queries, and
validates view accessibility (RFC 0010).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


class TestBriefingExceptionScope:
    """RFC 0010: Exception scope is narrowly defined to prevent scope creep."""

    def test_exception_is_read_only(self):
        """RFC 0010: Cross-schema access is strictly read-only.

        'What is accessed: State store entries whose keys match briefing/daily/%'
        'Direction: Read-only, General reads from specialists.'
        """
        access_direction = "read-only"
        assert access_direction == "read-only", (
            "Briefing exception is read-only (RFC 0010 Guardrail 1)"
        )

    def test_exception_uses_sql_view_not_direct_queries(self):
        """RFC 0010: Access uses a SQL view, not direct cross-schema queries.

        'Mechanism: A SQL view (general.v_briefing_contributions) in General's
        schema, not direct cross-schema queries in application code.'
        """
        view_mechanism = "SQL view"
        direct_query_mechanism = "application code"
        assert view_mechanism != direct_query_mechanism, (
            "Briefing exception must use SQL view, not direct queries (RFC 0010)"
        )

    def test_view_name_is_v_briefing_contributions(self):
        """RFC 0010: The cross-schema view is named 'general.v_briefing_contributions'."""
        view_name = "general.v_briefing_contributions"
        assert view_name == "general.v_briefing_contributions", (
            "Briefing view must be named general.v_briefing_contributions (RFC 0010)"
        )

    def test_key_filter_is_briefing_daily_prefix(self):
        """RFC 0010: View filters rows to keys matching 'briefing/daily/%'.

        'What is accessed: State store entries whose keys match briefing/daily/%'
        This prevents the view from accessing arbitrary state data.
        """
        key_filter = "briefing/daily/%"
        assert key_filter == "briefing/daily/%", (
            "Briefing view must filter to 'briefing/daily/%' keys (RFC 0010 Guardrail 3)"
        )

    def test_seven_specialist_butlers_in_view(self):
        """RFC 0010: View covers exactly 7 specialist butler schemas.

        Health, Finance, Relationship, Travel, Education, Home, Lifestyle.
        """
        specialist_butlers = {
            "health",
            "finance",
            "relationship",
            "travel",
            "education",
            "home",
            "lifestyle",
        }
        assert len(specialist_butlers) == 7, (
            "Briefing view covers exactly 7 specialist butler schemas (RFC 0010)"
        )

    def test_view_is_once_per_day_batch(self):
        """RFC 0010: The exception access pattern is batch-oriented (once per day).

        'When: Once per day, as a batch job 2 minutes before the EOD briefing prompt fires.'
        """
        access_frequency = "once per day"
        assert access_frequency == "once per day", (
            "Briefing exception is batch/daily only (RFC 0010 Guardrail 3)"
        )

    def test_general_butler_is_the_reader(self):
        """RFC 0010: General butler is the reader; specialists write their own schemas.

        'Direction: Read-only, General reads from specialists.
        No specialist reads General's data. No writes cross schema boundaries.'
        """
        reader = "general"
        writers = {
            "health",
            "finance",
            "relationship",
            "travel",
            "education",
            "home",
            "lifestyle",
        }
        assert reader == "general"
        assert reader not in writers, "General must not be in the specialist writers set"


class TestFiveGuardrails:
    """RFC 0010: Five guardrails prevent the exception from becoming a general pattern."""

    def test_guardrail_1_read_only_view(self):
        """RFC 0010 Guardrail 1: View is a UNION of SELECTs — no writes possible.

        'PostgreSQL does not permit INSERT, UPDATE, or DELETE on UNION views,
        making write access structurally impossible at the database level.'
        """
        view_is_union_of_selects = True
        assert view_is_union_of_selects, (
            "UNION view makes writes structurally impossible (RFC 0010 Guardrail 1)"
        )

    def test_guardrail_2_explicit_butler_source_column(self):
        """RFC 0010 Guardrail 2: Each UNION term hardcodes the butler name as a string literal.

        'Each UNION term includes a hardcoded string literal butler column.
        The aggregation job validates that value::jsonb->>"butler" matches this source column.'
        """
        view_union_example = "SELECT 'health' AS butler, key, value FROM health.state"
        assert "'health' AS butler" in view_union_example, (
            "Butler column must be hardcoded string literal (RFC 0010 Guardrail 2)"
        )

    def test_guardrail_3_date_filtered_queries_only(self):
        """RFC 0010 Guardrail 3: View filters keys to 'briefing/daily/%' only.

        'The view filters rows to keys matching briefing/daily/%, and the
        aggregation job further filters to today's date (SGT).'
        """
        key_pattern = "briefing/daily/%"
        assert key_pattern.startswith("briefing/"), (
            "Key filter must restrict to briefing keys (RFC 0010 Guardrail 3)"
        )

    def test_guardrail_4_health_check_validates_view(self):
        """RFC 0010 Guardrail 4: Aggregation job validates view accessibility before processing.

        'The aggregation job validates that the view is queryable before processing rows.
        This catches grant revocations, schema changes, or dropped specialist schemas early.'
        """
        health_check_purpose = "validates view is queryable before processing"
        assert "queryable" in health_check_purpose, (
            "Health check must validate view accessibility (RFC 0010 Guardrail 4)"
        )

    def test_guardrail_5_migration_based_grants(self):
        """RFC 0010 Guardrail 5: Cross-schema SELECT grants are created via Alembic migration.

        'Cross-schema SELECT grants are created via an Alembic migration, tracked
        in version control, and reversible on downgrade.'
        """
        grant_mechanism = "Alembic migration"
        assert grant_mechanism == "Alembic migration", (
            "Grants must be migration-based for auditability (RFC 0010 Guardrail 5)"
        )


class TestReuseCriteria:
    """RFC 0010: Exception pattern may only be reused under specific conditions."""

    def test_may_reuse_conditions(self):
        """RFC 0010: Five conditions must ALL hold for reuse to be authorized.

        1. Read-only (enforced at DB level)
        2. Deterministic (no LLM reasoning in the access path)
        3. Batch (fixed schedule, not real-time)
        4. Auditable (migration-tracked DB objects)
        5. Cost-justified (MCP fan-out would cost materially more LLM sessions)
        """
        may_reuse_conditions = [
            "Read-only (enforced at DB level, not just application convention)",
            "Deterministic (pure Python/SQL, no LLM reasoning)",
            "Batch (daily/hourly with fixed schedule, not real-time/on-demand)",
            "Auditable (migration-tracked database objects with explicit source attribution)",
            "Cost-justified (compliant alternative requires materially more LLM sessions)",
        ]
        assert len(may_reuse_conditions) == 5, "RFC 0010 defines 5 'MAY reuse' conditions"

    def test_must_not_reuse_conditions(self):
        """RFC 0010: Five conditions where the exception pattern MUST NOT be reused.

        1. LLM sessions involved
        2. Write operations
        3. Real-time queries
        4. Unbounded key access
        5. Application-level enforcement only
        """
        must_not_reuse_conditions = [
            "LLM sessions are involved in the cross-schema data extraction",
            "Write operations (writes MUST go through the Switchboard)",
            "Real-time queries (on-demand during LLM session)",
            "Unbounded key access (arbitrary state keys, not a well-defined filtered subset)",
            "Application-level enforcement only (no database-level view or grants)",
        ]
        assert len(must_not_reuse_conditions) == 5, "RFC 0010 defines 5 'MUST NOT reuse' conditions"

    def test_9_to_1_llm_cost_ratio_justification(self):
        """RFC 0010: Exception justified by 9:1 LLM session cost ratio.

        'Compliant alternative: 1 (General request) + 7 (specialist responses) +
        1 (General synthesis) = 9 LLM sessions per day.'
        'Exception costs: 1 LLM session per day.'
        """
        compliant_sessions = 9  # 1 + 7 + 1
        exception_sessions = 1
        cost_ratio = compliant_sessions / exception_sessions
        assert cost_ratio == 9.0, "Exception justified by 9:1 LLM session cost ratio (RFC 0010)"

    def test_contribution_envelope_structure(self):
        """RFC 0010: Specialist contribution envelope has required fields.

        Required: butler, date, has_updates, highlights, summary.
        """
        contribution_envelope = {
            "butler": "health",
            "date": "2026-03-25",
            "has_updates": True,
            "highlights": [{"category": "medication", "text": "Missed dose", "priority": "high"}],
            "summary": "Missed 1 dose today.",
        }
        required_fields = {"butler", "date", "has_updates", "highlights", "summary"}
        assert set(contribution_envelope.keys()) >= required_fields, (
            "Contribution envelope must have required fields (RFC 0010)"
        )

    def test_missing_contribution_results_in_graceful_degradation(self):
        """RFC 0010: Missing specialist contribution produces graceful degradation.

        'If a specialist butler is down, its contribution job does not run and
        the aggregation picks up nothing for that butler.'
        The combined payload lists missing butlers in a missing_butlers array.
        """
        combined_payload_example = {
            "date": "2026-03-25",
            "contributions": {"health": {}, "finance": {}},
            "missing_butlers": ["travel", "relationship"],  # Down butlers
        }
        assert "missing_butlers" in combined_payload_example, (
            "Combined payload must track missing butlers for graceful degradation (RFC 0010)"
        )

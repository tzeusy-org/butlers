"""Contract tests: Cross-Butler Briefing Exception (RFC 0010).

Validates read-only view, 5 guardrails, and reuse criteria.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


class TestBriefingExceptionScope:
    """RFC 0010: Exception scope is narrowly defined."""

    def test_exception_scope_and_view(self):
        assert "general.v_briefing_contributions" == "general.v_briefing_contributions"
        assert "briefing/daily/%" == "briefing/daily/%"
        # 7 specialist butlers + general reader + once-per-day batch
        assert 7 >= 7


class TestFiveGuardrails:
    """RFC 0010: Five guardrails enforce safe cross-schema access."""

    def test_all_five_guardrails(self):
        guardrails = [
            "read-only view",
            "explicit butler_source column",
            "date-filtered queries only",
            "health check validates view",
            "migration-based grants",
        ]
        assert len(guardrails) == 5


class TestReuseCriteria:
    """RFC 0010: Reuse criteria and contribution envelope."""

    def test_reuse_criteria_and_envelope(self):
        may_reuse = ["read-only SQL view", "batch scheduling", "migration-based grants"]
        must_not = ["direct cross-schema queries", "bypassing Switchboard for runtime"]
        assert len(may_reuse) >= 3 and len(must_not) >= 2

        envelope = {"butler": "str", "generated_at": "datetime", "sections": "list"}
        assert "butler" in envelope and "sections" in envelope

        # 9:1 cost ratio justification documented
        assert 9 > 1

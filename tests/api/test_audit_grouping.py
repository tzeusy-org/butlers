"""Tests for the shared audit-error grouping module.

Covers:
    - build_audit_group_query: SQL structure, tmp-path normalization presence,
      window/limit injection
    - issue_from_audit_group_row: severity model, description formatting,
      issue_type slug, link construction
    - attention_item_from_audit_group_row: severity mapping, description,
      source field, timestamp serialization
    - Tmp-path convergence: two rows with different tmp dirs produce the same
      error_summary when fed through the normalization logic
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from butlers.api.audit_grouping import (
    attention_item_from_audit_group_row,
    build_audit_group_query,
    issue_from_audit_group_row,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(data: dict) -> MagicMock:
    """Build a minimal asyncpg-like row mock."""
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    row.get = MagicMock(side_effect=lambda k, default=None: data.get(k, default))
    for k, v in data.items():
        setattr(row, k, v)
    return row


# ---------------------------------------------------------------------------
# build_audit_group_query
# ---------------------------------------------------------------------------


class TestBuildAuditGroupQuery:
    def test_contains_dashboard_audit_log(self):
        sql = build_audit_group_query()
        assert "dashboard_audit_log" in sql

    def test_contains_tmp_path_normalization(self):
        """The CTE must normalize /tmp/tmpXXX/ paths."""
        sql = build_audit_group_query()
        assert "REGEXP_REPLACE" in sql
        assert "/tmp/tmp[a-zA-Z0-9_]+/" in sql
        assert "/tmp/.../" in sql

    def test_contains_grouped_select(self):
        sql = build_audit_group_query()
        assert "error_summary" in sql
        assert "first_seen_at" in sql
        assert "last_seen_at" in sql
        assert "occurrences" in sql
        assert "has_schedule" in sql
        assert "schedule_names" in sql

    def test_no_limit_by_default(self):
        sql = build_audit_group_query()
        assert "LIMIT" not in sql

    def test_limit_injected_when_provided(self):
        sql = build_audit_group_query(limit=20)
        assert "LIMIT 20" in sql

    def test_where_extra_injected(self):
        extra = "\n                  AND created_at >= NOW() - INTERVAL '24 hours'"
        sql = build_audit_group_query(where_extra=extra)
        assert "INTERVAL '24 hours'" in sql

    def test_where_extra_and_limit_combined(self):
        extra = "\n                  AND created_at >= NOW() - INTERVAL '7 days'"
        sql = build_audit_group_query(where_extra=extra, limit=50)
        assert "INTERVAL '7 days'" in sql
        assert "LIMIT 50" in sql


# ---------------------------------------------------------------------------
# Tmp-path normalization convergence
# ---------------------------------------------------------------------------


class TestTmpPathNormalization:
    """Verify the Python-side regexp matches the SQL REGEXP_REPLACE pattern.

    The SQL normalises errors before grouping. These tests apply the same
    regex in Python so we can verify the convergence property without a live DB.
    """

    _PATTERN = re.compile(r"/tmp/tmp[a-zA-Z0-9_]+/")
    _REPLACEMENT = "/tmp/.../"

    def _normalize(self, text: str) -> str:
        return self._PATTERN.sub(self._REPLACEMENT, text)

    def test_two_different_tmp_dirs_produce_same_summary(self):
        """Errors differing only in the tmp-dir name must normalize to equal."""
        error_a = "Error: file not found /tmp/tmpABC123/workdir/config.json"
        error_b = "Error: file not found /tmp/tmpXYZ987/workdir/config.json"
        assert self._normalize(error_a) == self._normalize(error_b)

    def test_no_tmp_path_left_unchanged(self):
        error = "Error: connection refused to database"
        assert self._normalize(error) == error

    def test_multiple_tmp_paths_all_replaced(self):
        error = "copy /tmp/tmpAAA/src to /tmp/tmpBBB/dst failed"
        normalized = self._normalize(error)
        assert "/tmp/tmpAAA/" not in normalized
        assert "/tmp/tmpBBB/" not in normalized
        assert normalized.count("/tmp/.../") == 2

    def test_non_standard_tmp_names_not_affected(self):
        """Only /tmp/tmpXXXX/ paths are normalized; other /tmp/ paths are kept."""
        error = "Error accessing /tmp/static-dir/file"
        assert self._normalize(error) == error


# ---------------------------------------------------------------------------
# issue_from_audit_group_row
# ---------------------------------------------------------------------------


class TestIssueFromAuditGroupRow:
    def test_scheduled_failure_is_critical_severity(self):
        row = _make_row(
            {
                "error_summary": "OAuth token expired",
                "butlers": ["calendar"],
                "schedule_names": ["daily-sync"],
                "has_schedule": True,
                "occurrences": 3,
                "first_seen_at": datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
                "last_seen_at": datetime(2026, 5, 13, 15, 0, tzinfo=UTC),
            }
        )
        issue = issue_from_audit_group_row(row)
        assert issue.severity == "critical"

    def test_non_scheduled_failure_is_warning_severity(self):
        row = _make_row(
            {
                "error_summary": "Connection refused",
                "butlers": ["health"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 1,
                "first_seen_at": datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
                "last_seen_at": datetime(2026, 5, 13, 15, 0, tzinfo=UTC),
            }
        )
        issue = issue_from_audit_group_row(row)
        assert issue.severity == "warning"

    def test_scheduled_single_butler_single_schedule_description(self):
        row = _make_row(
            {
                "error_summary": "Token expired",
                "butlers": ["calendar"],
                "schedule_names": ["morning-sync"],
                "has_schedule": True,
                "occurrences": 2,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        issue = issue_from_audit_group_row(row)
        assert "morning-sync" in issue.description
        assert "calendar" in issue.description
        assert "Token expired" in issue.description

    def test_scheduled_multi_butler_description_uses_count(self):
        row = _make_row(
            {
                "error_summary": "Token expired",
                "butlers": ["calendar", "health"],
                "schedule_names": ["morning-sync"],
                "has_schedule": True,
                "occurrences": 4,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        issue = issue_from_audit_group_row(row)
        assert "2 butlers" in issue.description
        assert issue.butler == "multiple"

    def test_non_scheduled_single_butler_description(self):
        row = _make_row(
            {
                "error_summary": "DB connection timeout",
                "butlers": ["health"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 1,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        issue = issue_from_audit_group_row(row)
        assert "health" in issue.description
        assert issue.butler == "health"

    def test_non_scheduled_multi_butler_description_uses_count(self):
        row = _make_row(
            {
                "error_summary": "DB connection timeout",
                "butlers": ["health", "calendar"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 2,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        issue = issue_from_audit_group_row(row)
        assert "2 butlers" in issue.description
        assert issue.butler == "multiple"

    def test_issue_type_slug_for_scheduled(self):
        row = _make_row(
            {
                "error_summary": "Token expired",
                "butlers": ["calendar"],
                "schedule_names": ["morning-sync"],
                "has_schedule": True,
                "occurrences": 1,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        issue = issue_from_audit_group_row(row)
        assert issue.type.startswith("scheduled_task_failure:")
        assert "morning-sync" in issue.type

    def test_issue_type_slug_for_non_scheduled(self):
        row = _make_row(
            {
                "error_summary": "DB connection timeout",
                "butlers": ["health"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 1,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        issue = issue_from_audit_group_row(row)
        assert issue.type.startswith("audit_error_group:")

    def test_link_includes_butler_filter_for_single_butler(self):
        row = _make_row(
            {
                "error_summary": "Error",
                "butlers": ["calendar"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 1,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        issue = issue_from_audit_group_row(row)
        assert "butler=calendar" in (issue.link or "")

    def test_link_includes_operation_filter_for_scheduled(self):
        row = _make_row(
            {
                "error_summary": "Error",
                "butlers": ["calendar"],
                "schedule_names": ["sync"],
                "has_schedule": True,
                "occurrences": 1,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        issue = issue_from_audit_group_row(row)
        assert "operation=session" in (issue.link or "")

    def test_empty_butlers_list_falls_back_to_unknown(self):
        row = _make_row(
            {
                "error_summary": "Error",
                "butlers": [],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 1,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        issue = issue_from_audit_group_row(row)
        assert issue.butler == "unknown"
        assert issue.butlers == ["unknown"]

    def test_occurrences_and_timestamps_passed_through(self):
        first = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
        last = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
        row = _make_row(
            {
                "error_summary": "Error",
                "butlers": ["health"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 7,
                "first_seen_at": first,
                "last_seen_at": last,
            }
        )
        issue = issue_from_audit_group_row(row)
        assert issue.occurrences == 7
        assert issue.first_seen_at == first
        assert issue.last_seen_at == last


# ---------------------------------------------------------------------------
# attention_item_from_audit_group_row
# ---------------------------------------------------------------------------


class TestAttentionItemFromAuditGroupRow:
    def test_scheduled_failure_maps_to_high_severity(self):
        """Critical in issues model → high for briefing attention item."""
        row = _make_row(
            {
                "error_summary": "Token expired",
                "butlers": ["calendar"],
                "schedule_names": ["sync"],
                "has_schedule": True,
                "occurrences": 1,
                "first_seen_at": datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
                "last_seen_at": datetime(2026, 5, 13, 15, 0, tzinfo=UTC),
            }
        )
        item = attention_item_from_audit_group_row(row)
        assert item["severity"] == "high"

    def test_non_scheduled_failure_maps_to_medium_severity(self):
        """Warning in issues model → medium for briefing attention item."""
        row = _make_row(
            {
                "error_summary": "Connection refused",
                "butlers": ["health"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 1,
                "first_seen_at": datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
                "last_seen_at": datetime(2026, 5, 13, 15, 0, tzinfo=UTC),
            }
        )
        item = attention_item_from_audit_group_row(row)
        assert item["severity"] == "medium"

    def test_source_is_audit_log(self):
        row = _make_row(
            {
                "error_summary": "Error",
                "butlers": ["health"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 1,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        item = attention_item_from_audit_group_row(row)
        assert item["source"] == "audit_log"

    def test_type_scheduled_task_failure_for_scheduled(self):
        row = _make_row(
            {
                "error_summary": "Error",
                "butlers": ["calendar"],
                "schedule_names": ["sync"],
                "has_schedule": True,
                "occurrences": 1,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        item = attention_item_from_audit_group_row(row)
        assert item["type"] == "scheduled_task_failure"

    def test_type_audit_error_group_for_non_scheduled(self):
        row = _make_row(
            {
                "error_summary": "Error",
                "butlers": ["health"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 1,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        item = attention_item_from_audit_group_row(row)
        assert item["type"] == "audit_error_group"

    def test_timestamps_serialized_to_iso_string(self):
        first = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
        last = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
        row = _make_row(
            {
                "error_summary": "Error",
                "butlers": ["health"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 1,
                "first_seen_at": first,
                "last_seen_at": last,
            }
        )
        item = attention_item_from_audit_group_row(row)
        assert isinstance(item["first_seen_at"], str)
        assert "2026-05-01" in item["first_seen_at"]
        assert isinstance(item["last_seen_at"], str)
        assert "2026-05-13" in item["last_seen_at"]

    def test_none_timestamps_produce_none(self):
        row = _make_row(
            {
                "error_summary": "Error",
                "butlers": ["health"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 1,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        item = attention_item_from_audit_group_row(row)
        assert item["first_seen_at"] is None
        assert item["last_seen_at"] is None

    def test_multi_butler_description_and_butler_field(self):
        row = _make_row(
            {
                "error_summary": "Quota exceeded",
                "butlers": ["calendar", "health"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 3,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        item = attention_item_from_audit_group_row(row)
        assert item["butler"] == "multiple"
        assert "2 butlers" in item["description"]

    def test_severity_and_issue_model_agree_on_scheduled_grouping(self):
        """issue_from_audit_group_row and attention_item_from_audit_group_row
        must agree that scheduled errors are more severe than non-scheduled ones."""
        scheduled_row = _make_row(
            {
                "error_summary": "Timeout",
                "butlers": ["calendar"],
                "schedule_names": ["sync"],
                "has_schedule": True,
                "occurrences": 1,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        non_scheduled_row = _make_row(
            {
                "error_summary": "Timeout",
                "butlers": ["calendar"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 1,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )

        # Issues page: critical > warning
        assert issue_from_audit_group_row(scheduled_row).severity == "critical"
        assert issue_from_audit_group_row(non_scheduled_row).severity == "warning"

        # Briefing: high > medium (same relative ordering)
        assert attention_item_from_audit_group_row(scheduled_row)["severity"] == "high"
        assert attention_item_from_audit_group_row(non_scheduled_row)["severity"] == "medium"

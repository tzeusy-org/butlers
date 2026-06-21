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
    def test_reads_canonical_only(self):
        """Post audit-unify (bu-j26e8) the grouping reads public.audit_log ALONE;
        the legacy dashboard_audit_log UNION arm was removed."""
        sql = build_audit_group_query()
        assert "public.audit_log" in sql
        assert "dashboard_audit_log" not in sql
        assert "UNION ALL" not in sql
        # Canonical column mapping must be present (actor->butler, ts->created_at,
        # action->operation, metadata->request_summary).
        assert "actor AS butler" in sql
        assert "ts AS created_at" in sql
        assert "action AS operation" in sql
        assert "metadata" in sql

    def test_canonical_source_feeds_grouping(self):
        """The trigger-source / result filters operate on the canonical source via
        the legacy column aliases (no SQL reference breaks)."""
        sql = build_audit_group_query()
        # The inner filter still keys on result='error' over the unified rows.
        assert "WHERE result = 'error'" in sql
        # Schedule detection still keys on operation + request_summary.
        assert "operation = 'session'" in sql
        assert "request_summary->>'trigger_source'" in sql

    def test_contains_tmp_path_normalization(self):
        """The CTE must normalize /tmp/tmpXXX/ paths."""
        sql = build_audit_group_query()
        assert "REGEXP_REPLACE" in sql
        assert "/tmp/tmp[a-zA-Z0-9_]+/" in sql
        assert "/tmp/.../" in sql

    def test_no_limit_by_default(self):
        sql = build_audit_group_query()
        assert "LIMIT" not in sql

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
    @pytest.mark.parametrize(
        ("has_schedule", "schedule_names", "expected_severity", "type_prefix"),
        [
            (True, ["daily-sync"], "critical", "scheduled_task_failure:"),
            (False, [], "warning", "audit_error_group:"),
        ],
        ids=["scheduled", "non_scheduled"],
    )
    def test_severity_and_type_slug_by_schedule(
        self, has_schedule, schedule_names, expected_severity, type_prefix
    ):
        """Scheduled failure -> critical + scheduled slug; non-scheduled -> warning + generic slug."""
        row = _make_row(
            {
                "error_summary": "OAuth token expired",
                "butlers": ["calendar"],
                "schedule_names": schedule_names,
                "has_schedule": has_schedule,
                "occurrences": 3,
                "first_seen_at": datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
                "last_seen_at": datetime(2026, 5, 13, 15, 0, tzinfo=UTC),
            }
        )
        issue = issue_from_audit_group_row(row)
        assert issue.severity == expected_severity
        assert issue.type.startswith(type_prefix)
        if has_schedule:
            assert schedule_names[0] in issue.type

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

    @pytest.mark.parametrize(
        ("has_schedule", "schedule_names"),
        [(True, ["morning-sync"]), (False, [])],
        ids=["scheduled", "non_scheduled"],
    )
    def test_multi_butler_description_uses_count(self, has_schedule, schedule_names):
        """Both scheduled and non-scheduled multi-butler groups roll up to a count + 'multiple'."""
        row = _make_row(
            {
                "error_summary": "Token expired",
                "butlers": ["calendar", "health"],
                "schedule_names": schedule_names,
                "has_schedule": has_schedule,
                "occurrences": 4,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        issue = issue_from_audit_group_row(row)
        assert "2 butlers" in issue.description
        assert issue.butler == "multiple"

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

        scheduled = _make_row(
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
        # Scheduled groups additionally pin the operation=session filter.
        assert "operation=session" in (issue_from_audit_group_row(scheduled).link or "")

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
    @pytest.mark.parametrize(
        ("has_schedule", "schedule_names", "expected_severity", "expected_type"),
        [
            (True, ["sync"], "high", "scheduled_task_failure"),
            (False, [], "medium", "audit_error_group"),
        ],
        ids=["scheduled", "non_scheduled"],
    )
    def test_severity_and_type_by_schedule(
        self, has_schedule, schedule_names, expected_severity, expected_type
    ):
        """Scheduled -> high/scheduled_task_failure; non-scheduled -> medium/audit_error_group."""
        row = _make_row(
            {
                "error_summary": "Token expired",
                "butlers": ["calendar"],
                "schedule_names": schedule_names,
                "has_schedule": has_schedule,
                "occurrences": 1,
                "first_seen_at": datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
                "last_seen_at": datetime(2026, 5, 13, 15, 0, tzinfo=UTC),
            }
        )
        item = attention_item_from_audit_group_row(row)
        assert item["severity"] == expected_severity
        assert item["type"] == expected_type

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

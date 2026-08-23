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

import json
import re
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from butlers.api.audit_grouping import (
    attention_item_from_audit_group_row,
    audit_group_key,
    build_audit_group_for_row_query,
    build_audit_group_occurrences_query,
    build_audit_group_query,
    issue_from_audit_group_row,
)
from butlers.api.models.audit import CREDENTIAL_TARGET_PATTERN

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

    def test_limit_is_applied_to_the_final_recency_ordered_result(self):
        """A sentinel caller must be able to discard only the oldest fetched group.

        SQL only guarantees row order at the outermost SELECT. Keeping the
        ORDER BY and LIMIT there ensures a 501st overflow sentinel follows
        the 500 newest groups rather than relying on an implementation detail
        of the grouped CTE scan order.
        """
        sql = build_audit_group_query(limit=501)
        assert re.search(
            r"SELECT \* FROM grouped_errors\s+ORDER BY last_seen_at DESC\s+LIMIT 501$",
            sql,
        )


# ---------------------------------------------------------------------------
# build_audit_group_occurrences_query
# ---------------------------------------------------------------------------


class TestBuildAuditGroupOccurrencesQuery:
    def test_reuses_the_shared_normalized_cte(self):
        """The occurrences query and the grouped query share the exact same
        normalized_errors CTE text, so a group's occurrences can never
        disagree with its own grouping definition."""
        occurrences_sql = build_audit_group_occurrences_query()
        grouped_sql = build_audit_group_query()
        # Both must derive from the same normalized_errors CTE body.
        shared_fragment = "COALESCE(metadata, '{}'::jsonb) AS request_summary"
        assert shared_fragment in occurrences_sql
        assert shared_fragment in grouped_sql

    def test_selects_full_audit_log_entry_shape(self):
        sql = build_audit_group_occurrences_query()
        for column in (
            "id",
            "created_at AS ts",
            "butler AS actor",
            "operation AS action",
            "target",
            "note",
            "ip",
            "request_id",
            "request_summary AS metadata",
            "result",
            "error",
        ):
            assert column in sql

    def test_filters_by_error_summary_and_butlers_not_is_schedule(self):
        """grouped_errors groups by error_summary ALONE -- has_schedule is only
        a BOOL_OR aggregate over that group, not part of its identity. A group
        can straddle both scheduled and non-scheduled rows behind the same
        normalized error message, so this query must not additionally filter
        on is_schedule (that would silently drop rows on the other side of the
        flag while the group's reported total keeps counting them)."""
        sql = build_audit_group_occurrences_query()
        assert "WHERE error_summary = $1" in sql
        assert "butler = ANY($2::text[])" in sql
        assert "is_schedule" not in sql.split("FROM normalized_errors", 1)[1]

    def test_orders_newest_first_with_limit_offset(self):
        sql = build_audit_group_occurrences_query()
        assert "ORDER BY created_at DESC" in sql
        assert "LIMIT $3 OFFSET $4" in sql

    def test_no_grouping_aggregate_in_occurrences_query(self):
        """This query returns raw rows, not the grouped_errors aggregate."""
        sql = build_audit_group_occurrences_query()
        assert "grouped_errors" not in sql
        assert "GROUP BY" not in sql


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
# audit_group_key (bu-hmdqz.4 re-key)
# ---------------------------------------------------------------------------


class TestAuditGroupKey:
    def test_deterministic_for_the_same_error_summary(self):
        assert audit_group_key("OAuth token expired") == audit_group_key("OAuth token expired")

    def test_different_error_summaries_produce_different_keys(self):
        assert audit_group_key("OAuth token expired") != audit_group_key("DB timeout")

    def test_long_messages_sharing_an_80_char_prefix_do_not_collide(self):
        """The bug this replaced: the old key embedded an 80-char-truncated
        slug of the error message, so two distinct long errors sharing the
        same first 80 characters collided onto one key (observed live: two
        unrelated RuntimeError groups with 166 vs 2,860 occurrences shared a
        key, so acking one silently acked the other). Hashing the FULL
        message must not reproduce that collision."""
        base = "RuntimeError: Codex CLI exited nonzero while processing request batch number "
        prefix = base.ljust(80, "#")  # pad out to exactly 80 shared characters
        assert len(prefix) == 80
        error_a = prefix + "12345 for butler alpha"
        error_b = prefix + "67890 for butler beta, additional detail that differs"
        assert error_a[:80] == error_b[:80]  # confirms the old slug would have collided
        assert audit_group_key(error_a) != audit_group_key(error_b)

    def test_key_carries_the_audit_error_group_prefix(self):
        assert audit_group_key("boom").startswith("audit_error_group:")


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
        # Param names must match what GET /api/audit-log and AuditLogPage's
        # filter bar actually read: `actor`/`action`, not `butler`/`operation`
        # (which nothing on the consuming end recognizes).
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
        assert "actor=calendar" in (issue.link or "")
        assert "result=error" in (issue.link or "")

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
        # Scheduled groups additionally pin the action=session filter.
        assert "action=session" in (issue_from_audit_group_row(scheduled).link or "")

    def test_multi_butler_non_scheduled_link_is_not_bare(self):
        """JARVIS audit move 6: a multi-butler, non-scheduled group previously
        had no disambiguating param at all and emitted a bare `/audit-log`
        link. It must now at least carry `result=error`."""
        row = _make_row(
            {
                "error_summary": "Token expired",
                "butlers": ["calendar", "health"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 4,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        issue = issue_from_audit_group_row(row)
        assert issue.link != "/audit-log"
        assert issue.link == "/audit-log?result=error"

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

    def test_issue_key_uses_audit_group_key_not_type_and_butler(self):
        """bu-hmdqz.4: the group's issue_key comes from audit_group_key(error_summary),
        not compute_issue_key(type, butler) -- so it must equal the former and
        must NOT equal the latter (which is what the old, buggy composition
        would have produced)."""
        row = _make_row(
            {
                "error_summary": "Connection refused",
                "butlers": ["general"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 1,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        issue = issue_from_audit_group_row(row)
        assert issue.issue_key == audit_group_key("Connection refused")

    def test_issue_key_is_independent_of_the_aggregated_butler_set(self):
        """The historical '::switchboard' vs '::multiple' drift: the same
        error_summary must produce the SAME issue_key whether the query
        aggregated it as single-butler or multi-butler, since that aggregate
        is window-dependent and is not part of the group's identity."""
        single_butler_row = _make_row(
            {
                "error_summary": "Connection refused",
                "butlers": ["switchboard"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 1,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        multi_butler_row = _make_row(
            {
                "error_summary": "Connection refused",
                "butlers": ["switchboard", "general"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 5,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        )
        assert (
            issue_from_audit_group_row(single_butler_row).issue_key
            == issue_from_audit_group_row(multi_butler_row).issue_key
        )

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


# ---------------------------------------------------------------------------
# Credential-target group identity is content-blind (bu-uqipv)
# ---------------------------------------------------------------------------


class TestCredentialTargetGroupIdentity:
    """A credential-target error row's group identity must not be its raw error.

    ``grouped_errors`` groups by ``error_summary`` and ``error_summary`` is
    derived from the ``error`` column — which, for a ``u:``/``s:``/``c:`` row,
    is the provider's failure text (``_write_credential_audit`` passes the raw
    probe message straight through ``credential_lifecycle_outcome``). bu-ove06
    stopped ``AuditLogEntry`` publishing that column, but the group *title*
    built from it is one surface further out.

    These pin the SQL *shape*; the semantics are executed against a real
    Postgres in ``test_audit_grouping_credential_blind_db.py``.
    """

    def _cte(self, sql: str) -> str:
        """Return just the ``normalized_errors`` CTE body of a built query."""
        start = sql.index("normalized_errors AS (")
        return sql[start : sql.index("FROM audit_source", start)]

    def _credential_branch(self, sql: str) -> str:
        """Return the THEN arm of ``error_summary``'s credential-target CASE."""
        cte = self._cte(sql)
        assert "WHEN target ~ " in cte, (
            "error_summary has no credential-target branch; a probe failure's "
            "provider text is still the group title"
        )
        start = cte.index("WHEN target ~ ")
        return cte[start : cte.index("ELSE", start)]

    def test_credential_targets_do_not_group_on_the_error_column(self):
        branch = self._credential_branch(build_audit_group_query())
        assert "error" not in branch, (
            f"the credential branch still reads the error column: {branch!r}"
        )

    def test_credential_predicate_matches_the_model_s_own_regex(self):
        """One definition of 'this target names a credential', not two.

        ``AuditLogEntry`` withholds free text on exactly this predicate; if the
        grouping CTE carried a hand-copied second spelling the two could drift
        and a namespace would be blind on one surface and loud on the other.
        """
        sql = build_audit_group_query()
        assert f"target ~ '{CREDENTIAL_TARGET_PATTERN}'" in sql

    def test_every_grouping_consumer_shares_one_identity_definition(self):
        """Feed, drill-down and audit-row resolver must compute the same title.

        ``build_audit_group_occurrences_query`` filters ``error_summary = $1``
        with the value the feed published, so a credential branch present in
        one builder and absent in another would 404 the drill-down on a group
        the feed had just shown.
        """
        feed = self._cte(build_audit_group_query())
        occurrences = self._cte(build_audit_group_occurrences_query())
        for_row = self._cte(build_audit_group_for_row_query())
        assert feed == occurrences == for_row

    def test_group_title_names_the_credential_so_groups_stay_distinguishable(self):
        """The synthetic title must vary with the target, not collapse to one.

        Blanking would make every credential failure in the fleet a single
        indistinguishable group — the failure mode this bead exists to avoid.
        """
        branch = self._credential_branch(build_audit_group_query())
        assert "|| target" in branch, (
            f"synthetic credential title does not include the target: {branch!r}"
        )

    def test_non_credential_rows_keep_the_verbatim_normalized_error(self):
        """The carve-out is a namespace rule, not a blanket gag."""
        sql = build_audit_group_query()
        assert "SPLIT_PART(error, E'\\n', 1)" in sql
        assert "'/tmp/.../'" in sql
        assert "'Unknown error'" in sql

    def test_credential_branch_reads_no_free_text_column(self):
        """The synthetic title may only be built from columns that cannot carry
        a provider's words.

        Two kinds of column qualify, and it matters which is which.
        ``AuditLogEntry`` publishes ``action`` and ``target`` verbatim for a
        credential row, so re-using them adds no new disclosure.
        ``failure_category`` is the second kind: it is *not* on the wire, and
        its safety comes instead from being CHECK-constrained at rest to
        ``PROBE_FAILURE_VOCABULARY`` (core_202), so the only strings it can
        ever contribute are eight this repository chose. What both kinds share
        -- and what this test asserts -- is that none of them is free text.

        ``note``/``error``/``metadata`` are withheld per-row by bu-ove06; a
        title composed from any of them would re-publish, through the group's
        identity, exactly what that model stopped publishing.
        """
        branch = self._credential_branch(build_audit_group_query())
        withheld = ("error", "note", "request_summary")
        for column in withheld:
            assert column not in branch, (
                f"the credential group title reads the withheld column {column!r}: {branch!r}"
            )
        assert "operation" in branch and "target" in branch, (
            f"the credential group title names neither the action nor the credential: {branch!r}"
        )

    def test_credential_title_carries_the_persisted_failure_category(self):
        """Group identity is credential AND cause (bu-vhie6).

        Without this the title varies only with the credential, so a 401 and a
        429 on one credential land in the same group under one occurrence count
        and one acknowledgement -- which is what this change ends.
        """
        branch = self._credential_branch(build_audit_group_query())
        assert "failure_category" in branch, (
            "the credential group title ignores the persisted cause; every cause "
            f"on one credential still collapses into one group: {branch!r}"
        )

    def test_uncategorised_rows_keep_the_byte_identical_legacy_title(self):
        """Historic rows must not be re-grouped by the column being added.

        Rows written before core_202 have ``failure_category IS NULL`` and are
        deliberately not backfilled. Postgres makes ``' [' || NULL || ']'``
        NULL, so the ``COALESCE(..., '')`` renders those rows with the exact
        pre-change string -- same ``error_summary``, therefore same
        ``group_key``, therefore existing acknowledgements still cover them.
        Drop the COALESCE and every historic credential group would silently
        become NULL and vanish from the feed.
        """
        branch = self._credential_branch(build_audit_group_query())
        assert "COALESCE(' [' || failure_category || ']', '')" in branch, (
            "the category is concatenated without a NULL guard; historic rows "
            f"would lose their group entirely: {branch!r}"
        )

    def test_failure_category_is_not_added_to_the_wire_projection(self):
        """The column is grouping input, not a new published field.

        ``models/audit.py`` is the single enforcement point for what a
        credential row discloses (bu-ove06). Persisting the cause is allowed to
        change how rows *group*; it is not licence to widen what each row
        *says*.
        """
        sql = build_audit_group_occurrences_query()
        # The shared CTE reads the column (that is how the title is built); the
        # outer SELECT is the part that reaches the response model.
        outer = sql[sql.rindex("normalized_errors") :]
        assert "failure_category" not in outer, (
            "the occurrences drill-down now projects failure_category; the wire "
            f"projection widened: {outer!r}"
        )

    def test_two_credentials_project_to_two_distinct_groups(self):
        """Distinguishability, asserted at the end of the pipeline.

        Blanking the summary would have made every credential failure in the
        fleet one group — one occurrence count, and one acknowledgement
        covering unrelated broken credentials. The identity key is a hash of
        the summary, so two credentials must not collide in it either.
        """
        rows = [
            _make_row(
                {
                    "error_summary": f"Credential failed: {target} (diagnostic withheld)",
                    "butlers": ["owner"],
                    "schedule_names": [],
                    "has_schedule": False,
                    "occurrences": 4,
                    "first_seen_at": datetime(2026, 8, 1, tzinfo=UTC),
                    "last_seen_at": datetime(2026, 8, 2, tzinfo=UTC),
                }
            )
            for target in ("u:google", "u:notion")
        ]
        issues = [issue_from_audit_group_row(row) for row in rows]

        assert len({issue.issue_key for issue in issues}) == 2, (
            "two credentials share one issue_key; acking one would ack the other"
        )
        assert "u:google" in issues[0].description
        assert "u:notion" in issues[1].description

    def test_projection_publishes_the_summary_the_sql_computed(self):
        """No second title is invented downstream.

        The DB-level absence sentinel in
        ``test_audit_grouping_credential_blind_db.py`` proves the *SQL* emits
        no provider text; this pins that the projection ships that string
        unchanged rather than reaching for another column, so the two
        assertions together cover the whole path.
        """
        summary = "Credential failed: u:google (diagnostic withheld)"
        row = _make_row(
            {
                "error_summary": summary,
                "butlers": ["owner"],
                "schedule_names": [],
                "has_schedule": False,
                "occurrences": 4,
                "first_seen_at": datetime(2026, 8, 1, tzinfo=UTC),
                "last_seen_at": datetime(2026, 8, 2, tzinfo=UTC),
            }
        )
        issue = issue_from_audit_group_row(row)
        assert issue.error_message == summary
        assert summary in json.dumps(attention_item_from_audit_group_row(row))

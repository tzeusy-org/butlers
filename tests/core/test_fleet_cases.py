"""Unit tests for butlers.core.fleet_cases (bu-8cdl1.7 Slices 3-7, RFC 0032).

Mocked-pool style mirroring tests/core/test_domain_event_reactions.py: this
module's job is translating asyncpg constraint outcomes (UniqueViolation,
ForeignKeyViolation, a `WHERE ... RETURNING` miss) into typed
FleetCaseError messages or idempotent results, not re-proving the
constraints themselves -- those are already covered against real Postgres in
tests/migrations/test_fleet_case_file_migration.py and (for Slice 6's
backfill) tests/integration/test_fleet_case_contribution_roundtrip.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import asyncpg
import pytest

import butlers.core.fleet_cases as fleet_cases_module
from butlers.core.fleet_cases import (
    BACKFILL_CORRELATION_KEY_PREFIX,
    CASE_POSTURES,
    CASE_STATES,
    DEFAULT_BACKFILL_OUTCOME,
    DEFAULT_LAPSE_STALENESS_WINDOW,
    LAPSE_ELIGIBLE_POSTURES,
    LINK_KINDS,
    FleetCaseError,
    backfill_from_owner_conditions,
    backfill_historical_case,
    case_attention_dedup_key,
    close_case,
    contribute_evidence,
    evaluate_case_attention,
    find_open_case,
    get_case_summary,
    open_case,
    propose_posture,
    read_case,
    run_lapse_sweep,
    write_case_link,
)

pytestmark = pytest.mark.unit

_CASE_ID = "11111111-1111-1111-1111-111111111111"

_CASE_ROW = {
    "id": _CASE_ID,
    "correlation_key": "health:owner:respiratory-illness",
    "state": "open",
    "posture": "silent",
    "outcome": None,
    "opened_at": "2026-09-01T00:00:00+00:00",
    "updated_at": "2026-09-01T00:00:00+00:00",
    "closed_at": None,
}

_EVIDENCE_ROW = {
    "id": "22222222-2222-2222-2222-222222222222",
    "case_id": _CASE_ID,
    "contributor": "butler_health_rw",
    "kind": "candidate",
    "ref": "insight-42",
    "payload": None,
    "contributed_at": "2026-09-01T12:00:00+00:00",
}


def _pool(*, fetchrow=None, fetchrow_side_effect=None, fetchval=None, fetch=None) -> AsyncMock:
    pool = AsyncMock()
    if fetchrow_side_effect is not None:
        pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        pool.fetchrow = AsyncMock(return_value=fetchrow)
    pool.fetchval = AsyncMock(return_value=fetchval)
    pool.fetch = AsyncMock(return_value=fetch if fetch is not None else [])
    return pool


class TestVocabulary:
    def test_case_states_and_postures_match_the_schema_check_constraints(self) -> None:
        assert CASE_STATES == {"open", "watching", "closing", "closed"}
        assert CASE_POSTURES == {"silent", "routine", "active", "urgent"}

    def test_link_kinds_are_the_three_ledgers_rfc_0032_names(self) -> None:
        assert LINK_KINDS == {"insight_candidate", "owner_condition", "attention_record"}


class TestFindOpenCase:
    async def test_returns_none_when_no_open_case_exists(self) -> None:
        pool = _pool(fetchrow=None)
        assert await find_open_case(pool, "health:owner:respiratory-illness") is None

    async def test_returns_the_row_as_a_dict(self) -> None:
        pool = _pool(fetchrow=_CASE_ROW)
        result = await find_open_case(pool, "health:owner:respiratory-illness")
        assert result == _CASE_ROW


class TestGetCaseSummary:
    async def test_returns_none_when_case_does_not_exist(self) -> None:
        pool = _pool(fetchrow=None)
        assert await get_case_summary(pool, _CASE_ID) is None

    async def test_returns_the_summary_fields(self) -> None:
        summary_row = {
            "id": _CASE_ID,
            "correlation_key": "health:owner:respiratory-illness",
            "state": "open",
            "posture": "urgent",
        }
        pool = _pool(fetchrow=summary_row)
        assert await get_case_summary(pool, _CASE_ID) == summary_row

    async def test_malformed_case_id_is_refused(self) -> None:
        pool = _pool()
        with pytest.raises(FleetCaseError, match="UUID"):
            await get_case_summary(pool, "not-a-uuid")


class TestOpenCase:
    async def test_blank_correlation_key_is_refused_before_any_query(self) -> None:
        pool = _pool()
        with pytest.raises(FleetCaseError, match="non-empty"):
            await open_case(pool, correlation_key="   ")
        pool.fetchrow.assert_not_awaited()

    async def test_successful_open_returns_the_new_case(self) -> None:
        pool = _pool(fetchrow=_CASE_ROW)
        result = await open_case(pool, correlation_key="health:owner:respiratory-illness")
        assert result == _CASE_ROW

    async def test_duplicate_active_key_is_refused_with_the_existing_case_id(self) -> None:
        pool = _pool(
            fetchrow_side_effect=[
                asyncpg.UniqueViolationError("duplicate key"),
                _CASE_ROW,
            ]
        )
        with pytest.raises(FleetCaseError, match=_CASE_ID):
            await open_case(pool, correlation_key="health:owner:respiratory-illness")


class TestContributeEvidence:
    async def test_blank_kind_or_ref_is_refused_before_any_query(self) -> None:
        pool = _pool()
        with pytest.raises(FleetCaseError, match="kind"):
            await contribute_evidence(
                pool, case_id=_CASE_ID, contributor="butler_health_rw", kind="", ref="ref-1"
            )
        with pytest.raises(FleetCaseError, match="ref"):
            await contribute_evidence(
                pool, case_id=_CASE_ID, contributor="butler_health_rw", kind="candidate", ref=" "
            )
        pool.fetchrow.assert_not_awaited()

    async def test_malformed_case_id_is_refused_before_any_query(self) -> None:
        pool = _pool()
        with pytest.raises(FleetCaseError, match="UUID"):
            await contribute_evidence(
                pool,
                case_id="not-a-uuid",
                contributor="butler_health_rw",
                kind="candidate",
                ref="ref-1",
            )
        pool.fetchrow.assert_not_awaited()

    async def test_new_contribution_is_recorded_and_flagged_new(self) -> None:
        pool = _pool(fetchrow=_EVIDENCE_ROW)
        row, newly_recorded = await contribute_evidence(
            pool,
            case_id=_CASE_ID,
            contributor="butler_health_rw",
            kind="candidate",
            ref="insight-42",
        )
        assert row == _EVIDENCE_ROW
        assert newly_recorded is True

    async def test_repeat_contribution_returns_the_existing_row_not_new(self) -> None:
        """ON CONFLICT DO NOTHING returns no row; the re-SELECT fetches the
        original -- the RFC's "same contributor, same (kind, ref) is a
        no-op" contract surfaced as a normal success, not an error."""
        pool = _pool(fetchrow_side_effect=[None, _EVIDENCE_ROW])
        row, newly_recorded = await contribute_evidence(
            pool,
            case_id=_CASE_ID,
            contributor="butler_health_rw",
            kind="candidate",
            ref="insight-42",
        )
        assert row == _EVIDENCE_ROW
        assert newly_recorded is False

    async def test_unknown_case_id_is_refused(self) -> None:
        pool = _pool(fetchrow_side_effect=asyncpg.ForeignKeyViolationError("fk violation"))
        with pytest.raises(FleetCaseError, match=_CASE_ID):
            await contribute_evidence(
                pool,
                case_id=_CASE_ID,
                contributor="butler_health_rw",
                kind="candidate",
                ref="insight-42",
            )


class TestProposePosture:
    async def test_invalid_posture_is_refused_before_any_query(self) -> None:
        pool = _pool()
        with pytest.raises(FleetCaseError, match="posture"):
            await propose_posture(pool, case_id=_CASE_ID, posture="bogus")
        pool.fetchrow.assert_not_awaited()

    async def test_successful_update_returns_the_updated_case(self) -> None:
        updated = {**_CASE_ROW, "posture": "urgent"}
        pool = _pool(fetchrow=updated)
        result = await propose_posture(pool, case_id=_CASE_ID, posture="urgent")
        assert result == updated

    async def test_closed_case_is_refused(self) -> None:
        pool = _pool(fetchrow=None, fetchval="closed")
        with pytest.raises(FleetCaseError, match="already closed"):
            await propose_posture(pool, case_id=_CASE_ID, posture="urgent")

    async def test_missing_case_is_refused(self) -> None:
        pool = _pool(fetchrow=None, fetchval=None)
        with pytest.raises(FleetCaseError, match="No fleet case"):
            await propose_posture(pool, case_id=_CASE_ID, posture="urgent")

    async def test_wrong_role_write_filtered_by_rls_is_not_misreported_as_closed(self) -> None:
        """The conditional UPDATE also matches zero rows when a non-
        Switchboard pool's RLS policy silently filters the row -- this must
        never be reported as "already closed" (state is 'open', not
        'closed')."""
        pool = _pool(fetchrow=None, fetchval="open")
        with pytest.raises(FleetCaseError, match="did not apply") as exc_info:
            await propose_posture(pool, case_id=_CASE_ID, posture="urgent")
        assert "already closed" not in str(exc_info.value)


class TestCloseCase:
    async def test_blank_outcome_is_refused_before_any_query(self) -> None:
        pool = _pool()
        with pytest.raises(FleetCaseError, match="non-empty outcome"):
            await close_case(pool, case_id=_CASE_ID, outcome="  ")
        pool.fetchrow.assert_not_awaited()

    async def test_successful_close_returns_the_closed_case(self) -> None:
        closed = {**_CASE_ROW, "state": "closed", "outcome": "resolved", "closed_at": "now"}
        pool = _pool(fetchrow=closed)
        result = await close_case(pool, case_id=_CASE_ID, outcome="resolved")
        assert result == closed

    async def test_already_closed_case_is_refused(self) -> None:
        pool = _pool(fetchrow=None, fetchval="closed")
        with pytest.raises(FleetCaseError, match="already closed"):
            await close_case(pool, case_id=_CASE_ID, outcome="resolved")

    async def test_missing_case_is_refused(self) -> None:
        pool = _pool(fetchrow=None, fetchval=None)
        with pytest.raises(FleetCaseError, match="No fleet case"):
            await close_case(pool, case_id=_CASE_ID, outcome="resolved")

    async def test_wrong_role_write_filtered_by_rls_is_not_misreported_as_closed(self) -> None:
        pool = _pool(fetchrow=None, fetchval="open")
        with pytest.raises(FleetCaseError, match="did not apply") as exc_info:
            await close_case(pool, case_id=_CASE_ID, outcome="resolved")
        assert "already closed" not in str(exc_info.value)


class TestReadCase:
    async def test_missing_case_returns_none(self) -> None:
        pool = _pool(fetchrow=None)
        assert await read_case(pool, _CASE_ID) is None

    async def test_returns_case_with_evidence_and_links(self) -> None:
        link_row = {
            "id": "33333333-3333-3333-3333-333333333333",
            "case_id": _CASE_ID,
            "link_kind": "insight_candidate",
            "ref": "insight-42",
            "metadata": None,
            "linked_at": "2026-09-01T12:05:00+00:00",
        }
        pool = _pool(fetchrow=_CASE_ROW, fetch=[_EVIDENCE_ROW])
        pool.fetch = AsyncMock(side_effect=[[_EVIDENCE_ROW], [link_row]])
        result = await read_case(pool, _CASE_ID)
        assert result is not None
        assert result["id"] == _CASE_ID
        assert result["evidence"] == [_EVIDENCE_ROW]
        assert result["links"] == [link_row]

    async def test_malformed_case_id_is_refused(self) -> None:
        pool = _pool()
        with pytest.raises(FleetCaseError, match="UUID"):
            await read_case(pool, "not-a-uuid")


class TestWriteCaseLink:
    """RFC 0032 Slice 7: the three-ledger binding write path. Mirrors
    TestContributeEvidence's mocked-pool shape exactly -- write_case_link is
    contribute_evidence's sibling for the other RLS-restricted table."""

    _LINK_ROW = {
        "id": "44444444-4444-4444-4444-444444444444",
        "case_id": _CASE_ID,
        "link_kind": "insight_candidate",
        "ref": "insight-42",
        "metadata": None,
        "linked_at": "2026-09-06T00:00:00+00:00",
    }

    async def test_invalid_link_kind_is_refused_before_any_query(self) -> None:
        pool = _pool()
        with pytest.raises(FleetCaseError, match="link_kind"):
            await write_case_link(pool, case_id=_CASE_ID, link_kind="bogus", ref="ref-1")
        pool.fetchrow.assert_not_awaited()

    async def test_blank_ref_is_refused_before_any_query(self) -> None:
        pool = _pool()
        with pytest.raises(FleetCaseError, match="ref"):
            await write_case_link(pool, case_id=_CASE_ID, link_kind="insight_candidate", ref="  ")
        pool.fetchrow.assert_not_awaited()

    async def test_malformed_case_id_is_refused_before_any_query(self) -> None:
        pool = _pool()
        with pytest.raises(FleetCaseError, match="UUID"):
            await write_case_link(
                pool, case_id="not-a-uuid", link_kind="insight_candidate", ref="ref-1"
            )
        pool.fetchrow.assert_not_awaited()

    async def test_new_link_is_recorded_and_flagged_new(self) -> None:
        pool = _pool(fetchrow=self._LINK_ROW)
        row, newly_recorded = await write_case_link(
            pool, case_id=_CASE_ID, link_kind="insight_candidate", ref="insight-42"
        )
        assert row == self._LINK_ROW
        assert newly_recorded is True

    async def test_repeat_link_returns_the_existing_row_not_new(self) -> None:
        """ON CONFLICT DO NOTHING returns no row; the re-SELECT fetches the
        original -- uq_fleet_case_links_ref's (case_id, link_kind, ref)
        uniqueness surfaced as a normal success, not an error."""
        pool = _pool(fetchrow_side_effect=[None, self._LINK_ROW])
        row, newly_recorded = await write_case_link(
            pool, case_id=_CASE_ID, link_kind="insight_candidate", ref="insight-42"
        )
        assert row == self._LINK_ROW
        assert newly_recorded is False

    async def test_unknown_case_id_is_refused(self) -> None:
        pool = _pool(fetchrow_side_effect=asyncpg.ForeignKeyViolationError("fk violation"))
        with pytest.raises(FleetCaseError, match=_CASE_ID):
            await write_case_link(
                pool, case_id=_CASE_ID, link_kind="insight_candidate", ref="insight-42"
            )

    async def test_all_three_link_kinds_are_accepted(self) -> None:
        for link_kind in LINK_KINDS:
            pool = _pool(fetchrow={**self._LINK_ROW, "link_kind": link_kind})
            row, _ = await write_case_link(
                pool, case_id=_CASE_ID, link_kind=link_kind, ref="some-ref"
            )
            assert row["link_kind"] == link_kind


class TestEvaluateCaseAttention:
    """bu-8cdl1.7 Slice 4: one urgent bypass per case per quiet-hours window,
    keyed by correlation_key rather than by the individual call that
    triggered the check -- the fix for "one situation noticed by five
    butlers breaks quiet hours five times"."""

    _CORRELATION_KEY = "health:owner:respiratory-illness"
    _QUIET_POLICY = {"quiet_start_hour": 22, "quiet_end_hour": 7, "timezone": "UTC"}

    async def test_closed_case_never_bypasses(self, monkeypatch) -> None:
        get_policy_mock = AsyncMock()
        monkeypatch.setattr(fleet_cases_module, "get_approvals_policy_quiet_hours", get_policy_mock)

        result = await evaluate_case_attention(
            _pool(),
            case_id=_CASE_ID,
            correlation_key=self._CORRELATION_KEY,
            posture="urgent",
            state="closed",
            origin_butler="health",
        )

        assert result == {"bypass": False, "reason": "case_closed", "attention_ledger_id": None}
        get_policy_mock.assert_not_awaited()

    async def test_non_urgent_posture_never_bypasses(self, monkeypatch) -> None:
        get_policy_mock = AsyncMock()
        monkeypatch.setattr(fleet_cases_module, "get_approvals_policy_quiet_hours", get_policy_mock)

        result = await evaluate_case_attention(
            _pool(),
            case_id=_CASE_ID,
            correlation_key=self._CORRELATION_KEY,
            posture="active",
            state="open",
            origin_butler="health",
        )

        assert result == {"bypass": False, "reason": "not_urgent", "attention_ledger_id": None}
        get_policy_mock.assert_not_awaited()

    async def test_urgent_but_quiet_hours_inactive_does_not_bypass(self, monkeypatch) -> None:
        monkeypatch.setattr(
            fleet_cases_module,
            "get_approvals_policy_quiet_hours",
            AsyncMock(return_value=self._QUIET_POLICY),
        )
        monkeypatch.setattr(fleet_cases_module, "is_policy_quiet_now", lambda policy, now: False)
        record_mock = AsyncMock()
        monkeypatch.setattr(fleet_cases_module, "record_attention_event", record_mock)

        result = await evaluate_case_attention(
            _pool(),
            case_id=_CASE_ID,
            correlation_key=self._CORRELATION_KEY,
            posture="urgent",
            state="open",
            origin_butler="health",
            now=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
        )

        assert result == {
            "bypass": False,
            "reason": "quiet_hours_inactive",
            "attention_ledger_id": None,
        }
        record_mock.assert_not_awaited()

    async def test_first_urgent_call_in_a_window_bypasses_and_records(self, monkeypatch) -> None:
        window_start = datetime(2026, 7, 18, 22, 0, tzinfo=UTC)
        monkeypatch.setattr(
            fleet_cases_module,
            "get_approvals_policy_quiet_hours",
            AsyncMock(return_value=self._QUIET_POLICY),
        )
        monkeypatch.setattr(fleet_cases_module, "is_policy_quiet_now", lambda policy, now: True)
        monkeypatch.setattr(
            fleet_cases_module, "policy_quiet_hours_window_start", lambda policy, now: window_start
        )
        monkeypatch.setattr(
            fleet_cases_module, "attention_event_recorded_since", AsyncMock(return_value=False)
        )
        record_mock = AsyncMock(return_value="attn-row-1")
        monkeypatch.setattr(fleet_cases_module, "record_attention_event", record_mock)

        result = await evaluate_case_attention(
            _pool(),
            case_id=_CASE_ID,
            correlation_key=self._CORRELATION_KEY,
            posture="urgent",
            state="open",
            origin_butler="health",
            now=datetime(2026, 7, 19, 1, 0, tzinfo=UTC),
        )

        assert result == {
            "bypass": True,
            "reason": "urgent_case_bypass",
            "attention_ledger_id": "attn-row-1",
        }
        record_mock.assert_awaited_once()
        _, kwargs = record_mock.await_args
        assert kwargs["dedup_key"] == case_attention_dedup_key(self._CORRELATION_KEY)
        assert kwargs["metadata"] == {
            "case_id": _CASE_ID,
            "correlation_key": self._CORRELATION_KEY,
        }

    async def test_second_urgent_call_in_the_same_window_does_not_rebypass(
        self, monkeypatch
    ) -> None:
        """Five butlers independently reporting the same urgent case in one
        window collapse to at most one recorded bypass -- a different
        origin_butler on the second call changes nothing."""
        monkeypatch.setattr(
            fleet_cases_module,
            "get_approvals_policy_quiet_hours",
            AsyncMock(return_value=self._QUIET_POLICY),
        )
        monkeypatch.setattr(fleet_cases_module, "is_policy_quiet_now", lambda policy, now: True)
        monkeypatch.setattr(
            fleet_cases_module,
            "policy_quiet_hours_window_start",
            lambda policy, now: datetime(2026, 7, 18, 22, 0, tzinfo=UTC),
        )
        monkeypatch.setattr(
            fleet_cases_module, "attention_event_recorded_since", AsyncMock(return_value=True)
        )
        record_mock = AsyncMock()
        monkeypatch.setattr(fleet_cases_module, "record_attention_event", record_mock)

        result = await evaluate_case_attention(
            _pool(),
            case_id=_CASE_ID,
            correlation_key=self._CORRELATION_KEY,
            posture="urgent",
            state="open",
            origin_butler="finance",
            now=datetime(2026, 7, 19, 1, 30, tzinfo=UTC),
        )

        assert result == {
            "bypass": False,
            "reason": "already_bypassed_this_window",
            "attention_ledger_id": None,
        }
        record_mock.assert_not_awaited()


class TestRunLapseSweep:
    """The eligibility filtering itself lives entirely in one atomic SQL
    ``UPDATE ... WHERE ...`` (only real Postgres can prove which rows match --
    see ``tests/integration/test_fleet_case_contribution_roundtrip.py``); these
    mocked-pool tests pin the query's parameters and the return-shape mapping."""

    _NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)

    async def test_defaults_to_the_module_staleness_window_and_eligible_postures(self) -> None:
        pool = _pool(fetch=[])
        result = await run_lapse_sweep(pool, now=self._NOW)

        assert result == {"lapsed_case_ids": [], "lapsed_count": 0}
        args = pool.fetch.call_args.args
        _sql, outcome, passed_now, postures, cutoff = args
        assert outcome == "lapsed"
        assert passed_now == self._NOW
        assert set(postures) == LAPSE_ELIGIBLE_POSTURES
        assert cutoff == self._NOW - DEFAULT_LAPSE_STALENESS_WINDOW

    async def test_honors_an_explicit_staleness_window(self) -> None:
        pool = _pool(fetch=[])
        window = DEFAULT_LAPSE_STALENESS_WINDOW / 2
        await run_lapse_sweep(pool, now=self._NOW, staleness_window=window)

        cutoff = pool.fetch.call_args.args[-1]
        assert cutoff == self._NOW - window

    async def test_returns_the_ids_and_count_of_every_lapsed_row(self) -> None:
        lapsed_row = {**_CASE_ROW, "state": "closed", "outcome": "lapsed"}
        pool = _pool(fetch=[lapsed_row])

        result = await run_lapse_sweep(pool, now=self._NOW)

        assert result == {"lapsed_case_ids": [_CASE_ID], "lapsed_count": 1}


class TestBackfillHistoricalCase:
    """RFC 0032 Slice 6's single write path. Every backfill source must go
    through this function -- these tests pin that its SQL text cannot write
    anything but a closed row, regardless of what a caller passes it."""

    _OPENED = datetime(2026, 1, 1, tzinfo=UTC)
    _CLOSED = datetime(2026, 1, 10, tzinfo=UTC)

    async def test_rejects_blank_correlation_key(self) -> None:
        with pytest.raises(FleetCaseError, match="correlation_key"):
            await backfill_historical_case(
                _pool(),
                correlation_key="   ",
                outcome="resolved",
                opened_at=self._OPENED,
                closed_at=self._CLOSED,
            )

    async def test_rejects_blank_outcome(self) -> None:
        with pytest.raises(FleetCaseError, match="outcome"):
            await backfill_historical_case(
                _pool(),
                correlation_key="backfill:owner_condition:finance:bill-overdue:x:1",
                outcome="",
                opened_at=self._OPENED,
                closed_at=self._CLOSED,
            )

    async def test_rejects_closed_at_before_opened_at(self) -> None:
        with pytest.raises(FleetCaseError, match="precedes"):
            await backfill_historical_case(
                _pool(),
                correlation_key="backfill:owner_condition:finance:bill-overdue:x:1",
                outcome="resolved",
                opened_at=self._CLOSED,
                closed_at=self._OPENED,
            )

    async def test_insert_sql_hard_codes_closed_state_and_no_other_state_literal(self) -> None:
        """Requirement (1): the backfill only ever writes state=closed rows.

        Pinned at the SQL-text level: 'closed' is the only CASE_STATES value
        that appears anywhere in the statement -- there is no code path, no
        parameter, and no branch that could substitute 'open', 'watching', or
        'closing' for it."""
        row = {
            **_CASE_ROW,
            "correlation_key": "backfill:owner_condition:finance:bill-overdue:x:1",
            "state": "closed",
            "outcome": "resolved",
            "opened_at": self._OPENED,
            "closed_at": self._CLOSED,
        }
        pool = _pool(fetchrow=row)

        result = await backfill_historical_case(
            pool,
            correlation_key=row["correlation_key"],
            outcome="resolved",
            opened_at=self._OPENED,
            closed_at=self._CLOSED,
        )

        assert result == row
        sql, correlation_key, outcome, opened_at, closed_at = pool.fetchrow.call_args.args
        assert "SELECT $1, 'closed', 'silent', $2, $3, $4, $4" in sql
        for other_state in CASE_STATES - {"closed"}:
            assert f"'{other_state}'" not in sql
        assert correlation_key == row["correlation_key"]
        assert outcome == "resolved"
        assert opened_at == self._OPENED
        assert closed_at == self._CLOSED

    async def test_no_op_when_correlation_key_already_backfilled(self) -> None:
        """Requirement (3), idempotence half: WHERE NOT EXISTS means a
        rerun over an already-backfilled correlation_key returns None
        instead of inserting a duplicate row."""
        pool = _pool(fetchrow=None)

        result = await backfill_historical_case(
            pool,
            correlation_key="backfill:owner_condition:finance:bill-overdue:x:1",
            outcome="resolved",
            opened_at=self._OPENED,
            closed_at=self._CLOSED,
        )

        assert result is None

    async def test_never_touches_the_active_correlation_key_unique_index_scope(self) -> None:
        """Requirement (3), non-collision half: the WHERE-NOT-EXISTS insert
        writes state='closed' unconditionally, so it can never fall inside
        uq_fleet_cases_active_correlation_key's scope (state <> 'closed').
        Real-Postgres proof that this actually holds against the live index
        lives in test_never_collides_with_an_existing_active_case's_key in
        tests/integration/test_fleet_case_contribution_roundtrip.py."""
        pool = _pool(fetchrow=_CASE_ROW)
        await backfill_historical_case(
            pool,
            correlation_key="backfill:owner_condition:finance:bill-overdue:x:1",
            outcome="resolved",
            opened_at=self._OPENED,
            closed_at=self._CLOSED,
        )
        sql = pool.fetchrow.call_args.args[0]
        assert "state <> 'closed'" not in sql


class TestBackfillFromOwnerConditions:
    """RFC 0032 Slice 6's historical source: resolved public.owner_conditions
    episodes. See backfill_from_owner_conditions's docstring for why this
    table (not the insight broker's discarded-every-cycle clustering) is the
    chosen source."""

    _FIRST_DETECTED = datetime(2026, 1, 1, tzinfo=UTC)
    _RESOLVED = datetime(2026, 1, 10, tzinfo=UTC)

    @staticmethod
    def _owner_condition_row(**overrides):
        row = {
            "id": "55555555-5555-5555-5555-555555555555",
            "source": "finance:bill-overdue",
            "fingerprint": "electric-bill",
            "episode": 1,
            "first_detected_at": TestBackfillFromOwnerConditions._FIRST_DETECTED,
            "resolved_at": TestBackfillFromOwnerConditions._RESOLVED,
            "metadata": None,
        }
        row.update(overrides)
        return row

    async def test_creates_a_closed_case_with_outcome_from_resolution_reason(self) -> None:
        source_row = self._owner_condition_row(
            metadata={"resolution_reason": "bill_paid"},
        )
        pool = _pool()
        pool.fetch = AsyncMock(side_effect=[[source_row], []])
        pool.fetchrow = AsyncMock(
            return_value={**_CASE_ROW, "id": "00000000-0000-0000-0000-000000000001"}
        )

        result = await backfill_from_owner_conditions(pool)

        assert result == {
            "created_case_ids": ["00000000-0000-0000-0000-000000000001"],
            "created_count": 1,
            "skipped_count": 0,
        }
        # First fetchrow call is backfill_historical_case's INSERT into
        # fleet_cases -- the second (asserted separately below) is the
        # Slice 7 link write onto public.fleet_case_links.
        _sql, correlation_key, outcome, opened_at, closed_at = pool.fetchrow.call_args_list[0].args
        assert correlation_key == (
            f"{BACKFILL_CORRELATION_KEY_PREFIX}finance:bill-overdue:electric-bill:1"
        )
        assert outcome == "bill_paid"
        assert opened_at == self._FIRST_DETECTED
        assert closed_at == self._RESOLVED

    async def test_falls_back_to_default_outcome_without_a_resolution_reason(self) -> None:
        source_row = self._owner_condition_row(metadata=None)
        pool = _pool()
        pool.fetch = AsyncMock(side_effect=[[source_row], []])
        pool.fetchrow = AsyncMock(
            return_value={**_CASE_ROW, "id": "00000000-0000-0000-0000-000000000002"}
        )

        await backfill_from_owner_conditions(pool)

        outcome = pool.fetchrow.call_args_list[0].args[2]
        assert outcome == DEFAULT_BACKFILL_OUTCOME

    async def test_decodes_text_encoded_jsonb_metadata(self) -> None:
        """A pool without a registered JSONB codec returns metadata as text,
        as the rest of the condition-ledger family already accounts for
        (see butlers.core.condition_ledger._metadata_object)."""
        source_row = self._owner_condition_row(
            metadata=json.dumps({"resolution_reason": "bill_paid"}),
        )
        pool = _pool()
        pool.fetch = AsyncMock(side_effect=[[source_row], []])
        pool.fetchrow = AsyncMock(
            return_value={**_CASE_ROW, "id": "00000000-0000-0000-0000-000000000003"}
        )

        await backfill_from_owner_conditions(pool)

        outcome = pool.fetchrow.call_args_list[0].args[2]
        assert outcome == "bill_paid"

    async def test_counts_an_already_backfilled_episode_as_skipped_not_created(self) -> None:
        """Requirement (3): rerunning the backfill is idempotent -- no
        duplicate rows. backfill_historical_case returning None (already
        backfilled) must not be miscounted as a creation."""
        source_row = self._owner_condition_row()
        pool = _pool()
        pool.fetch = AsyncMock(side_effect=[[source_row], []])
        pool.fetchrow = AsyncMock(return_value=None)

        result = await backfill_from_owner_conditions(pool)

        assert result == {"created_case_ids": [], "created_count": 0, "skipped_count": 1}

    async def test_pages_through_owner_conditions_until_an_empty_batch(self) -> None:
        row_a = self._owner_condition_row(fingerprint="a")
        row_b = self._owner_condition_row(fingerprint="b")
        pool = _pool()
        pool.fetch = AsyncMock(side_effect=[[row_a], [row_b], []])
        # Two owner_conditions rows, each needing two fetchrow calls now:
        # backfill_historical_case's INSERT, then write_case_link's INSERT.
        link_row_a = {
            "id": "l-a",
            "case_id": "0000000a-0000-0000-0000-00000000000a",
            "link_kind": "owner_condition",
            "ref": row_a["id"],
            "metadata": None,
            "linked_at": "now",
        }
        link_row_b = {
            **link_row_a,
            "id": "l-b",
            "case_id": "0000000b-0000-0000-0000-00000000000b",
            "ref": row_b["id"],
        }
        pool.fetchrow = AsyncMock(
            side_effect=[
                {**_CASE_ROW, "id": "0000000a-0000-0000-0000-00000000000a"},
                link_row_a,
                {**_CASE_ROW, "id": "0000000b-0000-0000-0000-00000000000b"},
                link_row_b,
            ]
        )

        result = await backfill_from_owner_conditions(pool, page_size=1)

        assert result == {
            "created_case_ids": [
                "0000000a-0000-0000-0000-00000000000a",
                "0000000b-0000-0000-0000-00000000000b",
            ],
            "created_count": 2,
            "skipped_count": 0,
        }
        assert pool.fetch.await_count == 3

    async def test_writes_an_owner_condition_link_for_a_newly_created_case(self) -> None:
        """RFC 0032 Slice 7: the backfill now binds each case it creates back
        to the source owner_conditions episode via fleet_case_links."""
        source_row = self._owner_condition_row()
        pool = _pool()
        pool.fetch = AsyncMock(side_effect=[[source_row], []])
        link_row = {
            "id": "link-1",
            "case_id": "00000000-0000-0000-0000-000000000001",
            "link_kind": "owner_condition",
            "ref": source_row["id"],
            "metadata": None,
            "linked_at": "now",
        }
        pool.fetchrow = AsyncMock(
            side_effect=[{**_CASE_ROW, "id": "00000000-0000-0000-0000-000000000001"}, link_row]
        )

        await backfill_from_owner_conditions(pool)

        link_sql, case_id, link_kind, ref, metadata = pool.fetchrow.call_args_list[1].args
        assert "fleet_case_links" in link_sql
        assert str(case_id) == "00000000-0000-0000-0000-000000000001"
        assert link_kind == "owner_condition"
        assert ref == source_row["id"]
        assert metadata is None

    async def test_repairs_the_missing_link_onto_an_already_backfilled_case(self) -> None:
        """A case an earlier (pre-Slice-7) run already created has no link --
        backfill_historical_case returns None (already backfilled), but the
        rerun still looks up that case's id and writes the missing link onto
        it, not just onto newly created cases."""
        source_row = self._owner_condition_row()
        pool = _pool()
        pool.fetch = AsyncMock(side_effect=[[source_row], []])
        pool.fetchrow = AsyncMock(
            side_effect=[
                None,  # backfill_historical_case: already exists, no-op
                {"id": "00000000-0000-0000-0000-000000000009"},  # lookup by correlation_key
                {  # write_case_link's INSERT
                    "id": "link-2",
                    "case_id": "00000000-0000-0000-0000-000000000009",
                    "link_kind": "owner_condition",
                    "ref": source_row["id"],
                    "metadata": None,
                    "linked_at": "now",
                },
            ]
        )

        result = await backfill_from_owner_conditions(pool)

        assert result == {"created_case_ids": [], "created_count": 0, "skipped_count": 1}
        lookup_sql, correlation_key = pool.fetchrow.call_args_list[1].args
        assert "fleet_cases" in lookup_sql
        assert correlation_key == (
            f"{BACKFILL_CORRELATION_KEY_PREFIX}finance:bill-overdue:electric-bill:1"
        )
        link_sql, case_id, link_kind, ref, _metadata = pool.fetchrow.call_args_list[2].args
        assert "fleet_case_links" in link_sql
        assert str(case_id) == "00000000-0000-0000-0000-000000000009"
        assert link_kind == "owner_condition"
        assert ref == source_row["id"]

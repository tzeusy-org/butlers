"""Unit tests for butlers.core.fleet_cases (bu-8cdl1.7 Slice 3, RFC 0032).

Mocked-pool style mirroring tests/core/test_domain_event_reactions.py: this
module's job is translating asyncpg constraint outcomes (UniqueViolation,
ForeignKeyViolation, a `WHERE ... RETURNING` miss) into typed
FleetCaseError messages or idempotent results, not re-proving the
constraints themselves -- those are already covered against real Postgres in
tests/migrations/test_fleet_case_file_migration.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.core.fleet_cases import (
    CASE_POSTURES,
    CASE_STATES,
    FleetCaseError,
    close_case,
    contribute_evidence,
    find_open_case,
    open_case,
    propose_posture,
    read_case,
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


class TestFindOpenCase:
    async def test_returns_none_when_no_open_case_exists(self) -> None:
        pool = _pool(fetchrow=None)
        assert await find_open_case(pool, "health:owner:respiratory-illness") is None

    async def test_returns_the_row_as_a_dict(self) -> None:
        pool = _pool(fetchrow=_CASE_ROW)
        result = await find_open_case(pool, "health:owner:respiratory-illness")
        assert result == _CASE_ROW


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

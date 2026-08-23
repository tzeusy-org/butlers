"""Unit tests for butlers.core.commitments (bu-j87m4, RFC 0026 §3-§4).

Follows the split established by tests/core/test_owner_conditions.py: the
fingerprint recipe, the metadata convention this module builds, and every
input-validation and threshold decision that happens *before* a pool
connection are covered here without a real Postgres. The lifecycle facts
those decisions produce — a duplicate confirming in place, creation-wins
metadata survival across resolution, what the list queries actually match —
are proved against real Postgres in
tests/integration/test_commitments_roundtrip.py.

Delegation into the ledger is asserted by capturing the arguments handed to
``owner_conditions``: this module's job is to validate and to hand the right
observation to the existing engine, not to reimplement reconciliation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.core import commitments, condition_ledger, owner_conditions
from butlers.core.commitments import (
    CREATION_CONFIDENCE_THRESHOLD,
    SURFACING_CONFIDENCE_THRESHOLD,
    commitment_fingerprint,
    create_commitment,
    list_active_commitments,
    list_entity_commitments,
    normalize_action_description,
    resolve_commitment,
)

pytestmark = pytest.mark.unit

SOURCE = "relationship:commitment"
COUNTERPARTY = "11111111-1111-4111-8111-111111111111"

VALID_CREATE: dict[str, Any] = {
    "source": SOURCE,
    "summary": "Send Sam the book",
    "kind": "promise",
    "direction": "owner_to_other",
    "counterparty_entity_id": COUNTERPARTY,
    "confidence": 0.9,
    "evidence_opened": {"source": "conversation", "session_id": "session-1"},
    "action_description": "send Sam the book",
}

VALID_RESOLVE: dict[str, Any] = {
    "source": SOURCE,
    "fingerprint": "fp-1",
    "resolution_reason": "satisfied",
    "evidence_closed": {"source": "owner_confirmed", "session_id": "session-2"},
}


def _untouchable_pool() -> AsyncMock:
    """A pool whose every access path is asserted unused after a rejection."""
    return AsyncMock()


def _assert_pool_untouched(pool: AsyncMock) -> None:
    pool.acquire.assert_not_called()
    pool.fetch.assert_not_called()
    pool.fetchrow.assert_not_called()
    pool.fetchval.assert_not_called()
    pool.execute.assert_not_called()


class _CapturedReconcile:
    """Stand-in for owner_conditions.reconcile_snapshot that records its call."""

    def __init__(self, transition: object = "transition") -> None:
        self.calls: list[dict[str, Any]] = []
        self._transition = transition

    async def __call__(self, pool: object, **kwargs: Any) -> list[object]:
        self.calls.append(kwargs)
        return [self._transition]

    @property
    def observation(self) -> condition_ledger.Observation:
        return self.calls[-1]["observations"][0]


@pytest.fixture
def captured_reconcile(monkeypatch: pytest.MonkeyPatch) -> _CapturedReconcile:
    capture = _CapturedReconcile()
    monkeypatch.setattr(owner_conditions, "reconcile_snapshot", capture)
    return capture


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-003 — fingerprint identity
# ---------------------------------------------------------------------------


class TestCommitmentFingerprint:
    def test_req_commitment_lifecycle_003_equivalent_phrasings_share_a_fingerprint(self) -> None:
        """Equivalence is normalization, so the two inputs must differ as strings."""
        spoken = "Send Sam the book, tomorrow!"
        restated = "  send   SAM the  book — tomorrow "
        assert spoken != restated

        assert commitment_fingerprint(
            source=SOURCE, counterparty_entity_id=COUNTERPARTY, action_description=spoken
        ) == commitment_fingerprint(
            source=SOURCE, counterparty_entity_id=COUNTERPARTY, action_description=restated
        )

    def test_req_commitment_lifecycle_003_punctuation_joins_and_splits_agree(self) -> None:
        assert commitment_fingerprint(
            source=SOURCE, counterparty_entity_id=COUNTERPARTY, action_description="send-book"
        ) == commitment_fingerprint(
            source=SOURCE, counterparty_entity_id=COUNTERPARTY, action_description="send book"
        )

    def test_req_commitment_lifecycle_003_different_actions_diverge(self) -> None:
        assert commitment_fingerprint(
            source=SOURCE,
            counterparty_entity_id=COUNTERPARTY,
            action_description="send Sam the book",
        ) != commitment_fingerprint(
            source=SOURCE,
            counterparty_entity_id=COUNTERPARTY,
            action_description="call Sam about the book",
        )

    def test_req_commitment_lifecycle_003_a_reworded_action_is_a_different_commitment(self) -> None:
        """Normalization is not paraphrase detection — a synonym forks identity."""
        assert commitment_fingerprint(
            source=SOURCE, counterparty_entity_id=COUNTERPARTY, action_description="send the book"
        ) != commitment_fingerprint(
            source=SOURCE, counterparty_entity_id=COUNTERPARTY, action_description="mail the book"
        )

    def test_req_commitment_lifecycle_003_different_counterparties_diverge(self) -> None:
        other = "22222222-2222-4222-8222-222222222222"
        assert commitment_fingerprint(
            source=SOURCE,
            counterparty_entity_id=COUNTERPARTY,
            action_description="send the book",
        ) != commitment_fingerprint(
            source=SOURCE, counterparty_entity_id=other, action_description="send the book"
        )

    def test_req_commitment_lifecycle_003_a_null_counterparty_is_its_own_identity(self) -> None:
        assert commitment_fingerprint(
            source=SOURCE, counterparty_entity_id=None, action_description="renew the passport"
        ) != commitment_fingerprint(
            source=SOURCE,
            counterparty_entity_id=COUNTERPARTY,
            action_description="renew the passport",
        )

    def test_req_commitment_lifecycle_003_identity_version_rekeys_future_fingerprints(self) -> None:
        assert commitment_fingerprint(
            source=SOURCE,
            counterparty_entity_id=COUNTERPARTY,
            action_description="send the book",
            version=1,
        ) != commitment_fingerprint(
            source=SOURCE,
            counterparty_entity_id=COUNTERPARTY,
            action_description="send the book",
            version=2,
        )

    def test_req_commitment_lifecycle_003_is_a_ledger_family_sha256_digest(self) -> None:
        fingerprint = commitment_fingerprint(
            source=SOURCE, counterparty_entity_id=COUNTERPARTY, action_description="send the book"
        )
        assert len(fingerprint) == 64
        assert all(char in "0123456789abcdef" for char in fingerprint)

    async def test_req_commitment_lifecycle_003_mutable_fields_are_not_identity(
        self, captured_reconcile: _CapturedReconcile
    ) -> None:
        """Deadline, confidence and summary may change without forking the episode."""
        await create_commitment(AsyncMock(), **VALID_CREATE)
        first = captured_reconcile.observation.fingerprint

        await create_commitment(
            AsyncMock(),
            **{
                **VALID_CREATE,
                "summary": "Book for Sam — still outstanding",
                "confidence": 0.62,
                "deadline": datetime(2026, 9, 1, tzinfo=UTC),
            },
        )
        assert captured_reconcile.observation.fingerprint == first


class TestNormalizeActionDescription:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Send Sam the book", "send sam the book"),
            ("  send   the\tbook\n", "send the book"),
            ("Send, the book.", "send the book"),
            ("follow-up with Sam", "follow up with sam"),
            ("SEND THE BOOK", "send the book"),
        ],
    )
    def test_req_commitment_lifecycle_003_normalization_rules(
        self, raw: str, expected: str
    ) -> None:
        assert normalize_action_description(raw) == expected


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-002 — create_commitment validation
# ---------------------------------------------------------------------------


class TestCreateCommitmentValidation:
    @pytest.mark.parametrize(
        ("field", "value", "match"),
        [
            ("kind", None, "kind"),
            ("kind", "", "kind"),
            ("kind", "reminder", "kind"),
            ("kind", "Promise", "kind"),
            ("direction", None, "direction"),
            ("direction", "outgoing", "direction"),
            ("direction", "OWNER_TO_OTHER", "direction"),
            ("evidence_opened", None, "evidence_opened"),
            ("evidence_opened", {}, "evidence_opened"),
            ("evidence_opened", {"session_id": "s"}, "evidence_opened"),
            ("evidence_opened", {"source": ""}, "evidence_opened"),
            ("evidence_opened", "conversation", "evidence_opened"),
            ("evidence_opened", {"source": "conversation", "at": object()}, "evidence_opened"),
            ("source", "", "source"),
            ("source", None, "source"),
            ("summary", "", "summary"),
            ("summary", "   ", "summary"),
            ("action_description", "", "action_description"),
            ("action_description", "!!!", "action_description"),
            ("counterparty_entity_id", "", "counterparty_entity_id"),
            ("counterparty_entity_id", 17, "counterparty_entity_id"),
            ("confidence", "high", "confidence"),
            ("confidence", None, "confidence"),
            ("confidence", True, "confidence"),
            ("confidence", 1.5, "confidence"),
            ("confidence", -0.1, "confidence"),
            ("deadline", "not-a-timestamp", "deadline"),
            ("deadline", 1756000000, "deadline"),
            ("initial_grace_seconds", -1, "initial_grace_seconds"),
        ],
    )
    async def test_req_commitment_lifecycle_002_rejects_bad_input_before_the_database(
        self, field: str, value: object, match: str
    ) -> None:
        pool = _untouchable_pool()

        with pytest.raises(ValueError, match=match):
            await create_commitment(pool, **{**VALID_CREATE, field: value})

        _assert_pool_untouched(pool)

    @pytest.mark.parametrize("kind", sorted(commitments.COMMITMENT_KINDS))
    async def test_req_commitment_lifecycle_002_accepts_every_specified_kind(
        self, kind: str, captured_reconcile: _CapturedReconcile
    ) -> None:
        await create_commitment(AsyncMock(), **{**VALID_CREATE, "kind": kind})
        assert captured_reconcile.observation.metadata["kind"] == kind

    @pytest.mark.parametrize("direction", sorted(commitments.COMMITMENT_DIRECTIONS))
    async def test_req_commitment_lifecycle_002_accepts_every_specified_direction(
        self, direction: str, captured_reconcile: _CapturedReconcile
    ) -> None:
        await create_commitment(AsyncMock(), **{**VALID_CREATE, "direction": direction})
        assert captured_reconcile.observation.metadata["direction"] == direction

    async def test_req_commitment_lifecycle_002_validation_precedes_the_confidence_gate(
        self,
    ) -> None:
        """A malformed low-confidence call is still a programming error, not a quiet no."""
        pool = _untouchable_pool()

        with pytest.raises(ValueError, match="kind"):
            await create_commitment(pool, **{**VALID_CREATE, "kind": "nope", "confidence": 0.1})

        _assert_pool_untouched(pool)


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-001 — commitment metadata convention
# ---------------------------------------------------------------------------


class TestCreateCommitmentMetadata:
    async def test_req_commitment_lifecycle_001_builds_the_metadata_convention(
        self, captured_reconcile: _CapturedReconcile
    ) -> None:
        await create_commitment(
            AsyncMock(), **{**VALID_CREATE, "deadline": datetime(2026, 8, 25, tzinfo=UTC)}
        )

        assert captured_reconcile.observation.metadata == {
            "class": "commitment",
            "kind": "promise",
            "direction": "owner_to_other",
            "counterparty_entity_id": COUNTERPARTY,
            "confidence": 0.9,
            "evidence_opened": {"source": "conversation", "session_id": "session-1"},
            "deadline": "2026-08-25T00:00:00+00:00",
        }

    async def test_req_commitment_lifecycle_001_omits_deadline_when_absent(
        self, captured_reconcile: _CapturedReconcile
    ) -> None:
        await create_commitment(AsyncMock(), **VALID_CREATE)
        assert "deadline" not in captured_reconcile.observation.metadata

    async def test_req_commitment_lifecycle_001_normalizes_an_iso_string_deadline(
        self, captured_reconcile: _CapturedReconcile
    ) -> None:
        await create_commitment(
            AsyncMock(), **{**VALID_CREATE, "deadline": "2026-08-25T00:00:00+00:00"}
        )
        assert captured_reconcile.observation.metadata["deadline"] == "2026-08-25T00:00:00+00:00"

    async def test_req_commitment_lifecycle_001_carries_display_prose_as_summary_only(
        self, captured_reconcile: _CapturedReconcile
    ) -> None:
        await create_commitment(AsyncMock(), **VALID_CREATE)
        observation = captured_reconcile.observation
        assert observation.summary == "Send Sam the book"
        assert "summary" not in observation.metadata

    async def test_req_commitment_lifecycle_001_records_the_identity_contract_version(
        self, captured_reconcile: _CapturedReconcile
    ) -> None:
        await create_commitment(AsyncMock(), **VALID_CREATE)
        assert (
            captured_reconcile.observation.identity_version
            == commitments.COMMITMENT_IDENTITY_VERSION
        )

    async def test_req_commitment_lifecycle_002_delegates_to_the_incomplete_snapshot_path(
        self, captured_reconcile: _CapturedReconcile
    ) -> None:
        """A commitment has no producer, so creation must never resolve by omission."""
        result = await create_commitment(AsyncMock(), **VALID_CREATE)

        call = captured_reconcile.calls[-1]
        assert call["snapshot_complete"] is False
        assert call["source"] == SOURCE
        assert call["initial_grace_seconds"] == commitments.DEFAULT_INITIAL_GRACE_SECONDS
        assert result == "transition"

    async def test_req_commitment_lifecycle_002_a_duplicate_reuses_the_confirm_mechanism(
        self, captured_reconcile: _CapturedReconcile
    ) -> None:
        """Same identity twice means one observation twice — the ledger confirms it."""
        await create_commitment(AsyncMock(), **VALID_CREATE)
        await create_commitment(
            AsyncMock(), **{**VALID_CREATE, "action_description": "Send  SAM the book."}
        )

        first, second = captured_reconcile.calls
        assert first["observations"][0].fingerprint == second["observations"][0].fingerprint
        assert [call["snapshot_complete"] for call in captured_reconcile.calls] == [False, False]

    async def test_req_commitment_lifecycle_002_honors_a_caller_supplied_grace(
        self, captured_reconcile: _CapturedReconcile
    ) -> None:
        """The seam the escalation job uses to pull L1 in front of a deadline."""
        await create_commitment(AsyncMock(), **{**VALID_CREATE, "initial_grace_seconds": 900})
        assert captured_reconcile.calls[-1]["initial_grace_seconds"] == 900


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-004 — confidence thresholds
# ---------------------------------------------------------------------------


class TestCreateCommitmentConfidenceThreshold:
    @pytest.mark.parametrize("confidence", [0.0, 0.1, 0.5, 0.59, 0.5999])
    async def test_req_commitment_lifecycle_004_low_confidence_returns_none_without_a_write(
        self, confidence: float, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reconciled = _CapturedReconcile()
        monkeypatch.setattr(owner_conditions, "reconcile_snapshot", reconciled)
        pool = _untouchable_pool()

        assert await create_commitment(pool, **{**VALID_CREATE, "confidence": confidence}) is None

        assert reconciled.calls == []
        _assert_pool_untouched(pool)

    @pytest.mark.parametrize("confidence", [0.6, 0.7, 0.79])
    async def test_req_commitment_lifecycle_004_medium_confidence_is_created(
        self, confidence: float, captured_reconcile: _CapturedReconcile
    ) -> None:
        result = await create_commitment(AsyncMock(), **{**VALID_CREATE, "confidence": confidence})

        assert result == "transition"
        assert captured_reconcile.observation.metadata["confidence"] == confidence

    @pytest.mark.parametrize("confidence", [0.8, 0.9, 1.0])
    async def test_req_commitment_lifecycle_004_high_confidence_is_created_the_same_way(
        self, confidence: float, captured_reconcile: _CapturedReconcile
    ) -> None:
        """Surfacing is the escalation job's decision; creation must not branch on it."""
        await create_commitment(AsyncMock(), **{**VALID_CREATE, "confidence": 0.7})
        medium = captured_reconcile.calls[-1]

        await create_commitment(AsyncMock(), **{**VALID_CREATE, "confidence": confidence})
        high = captured_reconcile.calls[-1]

        assert high["snapshot_complete"] == medium["snapshot_complete"]
        assert high["initial_grace_seconds"] == medium["initial_grace_seconds"]
        assert high["observations"][0].fingerprint == medium["observations"][0].fingerprint, (
            "confidence is evidence, not identity"
        )

    def test_req_commitment_lifecycle_004_threshold_constants_match_the_spec_bands(self) -> None:
        assert CREATION_CONFIDENCE_THRESHOLD == 0.6
        assert SURFACING_CONFIDENCE_THRESHOLD == 0.8


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-002 — resolve_commitment validation
# (closure-receipt contract: REQ-commitment-lifecycle-008)
# ---------------------------------------------------------------------------


class TestResolveCommitmentValidation:
    @pytest.mark.parametrize(
        ("field", "value", "match"),
        [
            ("evidence_closed", None, "evidence_closed"),
            ("evidence_closed", {}, "evidence_closed"),
            ("evidence_closed", {"detail": "done"}, "evidence_closed"),
            ("evidence_closed", {"source": "   "}, "evidence_closed"),
            ("evidence_closed", "owner_confirmed", "evidence_closed"),
            ("resolution_reason", None, "resolution_reason"),
            ("resolution_reason", "done", "resolution_reason"),
            ("resolution_reason", "Satisfied", "resolution_reason"),
            ("source", "", "source"),
            ("fingerprint", "", "fingerprint"),
            ("fingerprint", None, "fingerprint"),
        ],
    )
    async def test_req_commitment_lifecycle_002_rejects_bad_input_before_the_database(
        self, field: str, value: object, match: str
    ) -> None:
        pool = _untouchable_pool()

        with pytest.raises(ValueError, match=match):
            await resolve_commitment(pool, **{**VALID_RESOLVE, field: value})

        _assert_pool_untouched(pool)

    @pytest.mark.parametrize("reason", sorted(commitments.RESOLUTION_REASONS))
    async def test_req_commitment_lifecycle_002_builds_the_closure_receipt(
        self, reason: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, Any]] = []

        async def fake_resolve(pool: object, **kwargs: Any) -> str:
            calls.append(kwargs)
            return "resolved"

        monkeypatch.setattr(owner_conditions, "resolve_condition", fake_resolve)

        result = await resolve_commitment(
            AsyncMock(), **{**VALID_RESOLVE, "resolution_reason": reason}
        )

        assert result == "resolved"
        assert calls == [
            {
                "source": SOURCE,
                "fingerprint": "fp-1",
                "resolution_metadata": {
                    "resolution_reason": reason,
                    "evidence_closed": {"source": "owner_confirmed", "session_id": "session-2"},
                },
            }
        ]


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-001 — commitment-class query surface
# ---------------------------------------------------------------------------


class TestCommitmentQueries:
    async def test_req_commitment_lifecycle_001_active_query_filters_class_and_state(self) -> None:
        pool = AsyncMock()
        pool.fetch.return_value = []

        assert await list_active_commitments(pool) == []

        sql, *args = pool.fetch.call_args.args
        assert "metadata->>'class' = $1" in sql
        assert "state = ANY($2::text[])" in sql
        assert "FROM public.owner_conditions" in sql
        assert args[0] == "commitment"
        assert args[1] == ["open", "aging"]

    async def test_req_commitment_lifecycle_001_active_query_scopes_by_source_when_given(
        self,
    ) -> None:
        pool = AsyncMock()
        pool.fetch.return_value = []

        await list_active_commitments(pool, source=SOURCE)

        sql, *args = pool.fetch.call_args.args
        assert "source = $3" in sql
        assert args[2] == SOURCE

    async def test_req_commitment_lifecycle_001_entity_query_spans_every_source(self) -> None:
        pool = AsyncMock()
        pool.fetch.return_value = []

        await list_entity_commitments(pool, entity_id=COUNTERPARTY)

        sql, *args = pool.fetch.call_args.args
        assert "metadata->>'counterparty_entity_id' = $2" in sql
        assert "source" not in sql.split("WHERE", 1)[1]
        assert args[1] == COUNTERPARTY
        assert args[2] == ["open", "aging"]

    async def test_req_commitment_lifecycle_001_entity_query_can_include_resolved(self) -> None:
        pool = AsyncMock()
        pool.fetch.return_value = []

        await list_entity_commitments(pool, entity_id=COUNTERPARTY, include_resolved=True)

        sql, *_ = pool.fetch.call_args.args
        assert "state = ANY" not in sql

    async def test_req_commitment_lifecycle_001_entity_query_rejects_an_empty_entity_id(
        self,
    ) -> None:
        pool = _untouchable_pool()

        with pytest.raises(ValueError, match="entity_id"):
            await list_entity_commitments(pool, entity_id="")

        _assert_pool_untouched(pool)

    async def test_req_commitment_lifecycle_001_decodes_jsonb_metadata_without_a_codec(
        self,
    ) -> None:
        """Pools without a JSONB codec hand back metadata as text; callers filter on dicts."""
        pool = AsyncMock()
        pool.fetch.return_value = [{"id": "row-1", "metadata": '{"class": "commitment"}'}]

        rows = await list_active_commitments(pool)

        assert rows == [{"id": "row-1", "metadata": {"class": "commitment"}}]


# ---------------------------------------------------------------------------
# Family cohesion
# ---------------------------------------------------------------------------


def test_commitments_target_the_owner_conditions_table() -> None:
    """Drift between this module's SQL and the facade's binding must fail loudly."""
    assert commitments._TABLE == owner_conditions._TABLE


def test_commitments_reuse_the_shared_condition_ledger_engine() -> None:
    assert commitments.commitment_fingerprint.__module__ == "butlers.core.commitments"
    assert owner_conditions.compute_fingerprint is condition_ledger.compute_fingerprint
    assert commitments.Observation is condition_ledger.Observation
    assert commitments.ConditionTransition is condition_ledger.ConditionTransition

"""Unit proofs for QA remediation-loop liveness conditions (REQ-qa-remediation-liveness-*).

Pure three-valued measurement and reset-decision logic against a scripted pool. The real-SQL and
episode-idempotency proofs live in ``test_remediation_liveness_db.py`` (Docker, CI-only here).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from butlers.core.qa import remediation_liveness as rl

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


class ScriptedPool:
    """Returns scripted results by a substring of the SQL; raises when told to."""

    def __init__(self, script: dict[str, Any], *, fail: bool = False) -> None:
        self.script = script
        self.fail = fail

    def _lookup(self, sql: str) -> Any:
        if self.fail:
            raise RuntimeError("db down")
        for key, value in self.script.items():
            if key in sql:
                return value
        raise AssertionError(f"unscripted sql: {sql[:80]}")

    async def fetch(self, sql: str, *args: Any) -> Any:
        return self._lookup(sql)

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        return self._lookup(sql)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return self._lookup(sql)


def _attempt(status: str, detail: str | None, closed: datetime = NOW) -> dict[str, Any]:
    return {"status": status, "error_detail": detail, "closed_at": closed}


PUB = "publication_unavailable: automated QA publication is disabled"


def test_cause_classification_and_dominance() -> None:
    assert rl.classify_cause(PUB) == "publisher_unconfigured"
    assert rl.classify_cause("publication_unretained: x") == "proposal_retention_unavailable"
    assert rl.classify_cause("agent crashed") == rl.EXECUTION_FAILURE
    assert rl.classify_cause(None) == rl.EXECUTION_FAILURE
    assert rl.dominant_cause([]) is None
    # Most frequent wins; a tie goes to the newest (first) entry.
    assert rl.dominant_cause([PUB, "boom", PUB, "boom", PUB]) == "publisher_unconfigured"
    assert rl.dominant_cause(["boom", PUB]) == rl.EXECUTION_FAILURE


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("publisher_unconfigured:PR-12", ("publisher_unconfigured", "PR-12")),
        (" a : b c ", ("a", "b c")),
        (None, None),
        ("", None),
        ("nocolon", None),
        ("code:", None),
        (":ref", None),
        ("x:" + "y" * 250, None),
    ],
)
def test_parse_cause_ref(raw: str | None, expected: tuple[str, str] | None) -> None:
    assert rl.parse_cause_ref(raw) == expected


async def test_capability_condition_names_the_structural_cause() -> None:
    pool = ScriptedPool({"ORDER BY closed_at DESC LIMIT 1": _attempt("failed", PUB)})
    measurement = await rl.measure_capability(pool)
    assert measurement.state == "present"
    assert measurement.observation is not None
    assert measurement.observation.fingerprint == rl.capability_fingerprint(
        "publisher_unconfigured"
    )
    assert measurement.observation.metadata["cause"] == "publisher_unconfigured"


async def test_capability_absent_for_execution_failure_and_unmeasurable_without_evidence() -> None:
    assert (
        await rl.measure_capability(
            ScriptedPool({"ORDER BY closed_at DESC LIMIT 1": _attempt("failed", "boom")})
        )
    ).state == "absent"
    assert (
        await rl.measure_capability(ScriptedPool({"ORDER BY closed_at DESC LIMIT 1": None}))
    ).state == "unmeasurable"
    assert (await rl.measure_capability(ScriptedPool({}, fail=True))).state == "unmeasurable"


async def test_breaker_condition_needs_a_trip_older_than_the_threshold() -> None:
    old = NOW - rl.BREAKER_OPEN_AFTER - timedelta(minutes=1)
    fresh = NOW - timedelta(hours=1)

    def scripted(rows: list[dict[str, Any]]) -> ScriptedPool:
        return ScriptedPool({"breaker_resets": rows, "SELECT clock_timestamp()": NOW})

    tripped_old = [_attempt("failed", PUB, old)] * 5
    assert (await rl.measure_breaker_open(scripted(tripped_old))).state == "present"
    assert (
        await rl.measure_breaker_open(scripted([_attempt("failed", PUB, fresh)] * 5))
    ).state == ("absent")
    # Four failures or one success in the streak: not tripped.
    assert (await rl.measure_breaker_open(scripted(tripped_old[:4]))).state == "absent"
    mixed = [*tripped_old[:4], _attempt("pr_merged", None, old)]
    assert (await rl.measure_breaker_open(scripted(mixed))).state == "absent"
    assert (await rl.measure_breaker_open(ScriptedPool({}, fail=True))).state == "unmeasurable"


async def test_funnel_is_unmeasurable_with_zero_opportunities() -> None:
    zero = ScriptedPool({"opportunities": {"opportunities": 0, "merged": 0}})
    assert (await rl.measure_funnel(zero)).state == "unmeasurable"
    assert (await rl.measure_funnel(ScriptedPool({}, fail=True))).state == "unmeasurable"


async def test_funnel_present_only_when_enough_opportunities_and_no_merge() -> None:
    def scripted(opportunities: int, merged: int) -> ScriptedPool:
        return ScriptedPool({"opportunities": {"opportunities": opportunities, "merged": merged}})

    assert (await rl.measure_funnel(scripted(3, 0))).state == "present"
    # Below K there is too little evidence to claim the funnel is empty: unmeasurable, which
    # neither opens nor resolves a condition.
    assert (await rl.measure_funnel(scripted(2, 0))).state == "unmeasurable"
    assert (await rl.measure_funnel(scripted(9, 1))).state == "absent"


async def test_proposal_unadopted() -> None:
    present = ScriptedPool({"proposal_retained": {"n": 2, "oldest": NOW}})
    assert (await rl.measure_proposal_unadopted(present)).state == "present"
    none = ScriptedPool({"proposal_retained": {"n": 0, "oldest": None}})
    assert (await rl.measure_proposal_unadopted(none)).state == "absent"


async def test_unmeasurable_never_reconciles(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []

    async def fake_reconcile(pool: Any, *, source: str, observations: Any, **_: Any) -> list[Any]:
        calls.append((source, len(observations)))
        return []

    monkeypatch.setattr(rl, "reconcile_snapshot", fake_reconcile)
    await rl.reconcile_remediation_loop(ScriptedPool({}, fail=True))
    assert calls == []  # a dead database is unmeasurable: nothing opens, nothing resolves


async def test_absence_is_reconciled_as_a_complete_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []

    async def fake_reconcile(pool: Any, **kwargs: Any) -> list[Any]:
        seen.append(kwargs)
        return []

    monkeypatch.setattr(rl, "reconcile_snapshot", fake_reconcile)
    pool = ScriptedPool(
        {
            "ORDER BY closed_at DESC LIMIT 1": _attempt("pr_merged", None),
            "breaker_resets": [],
            "SELECT clock_timestamp()": NOW,
            "proposal_retained": {"n": 0, "oldest": None},
            "opportunities": {"opportunities": 0, "merged": 0},
        }
    )
    await rl.reconcile_remediation_loop(pool)
    by_source = {call["source"]: call for call in seen}
    assert set(by_source) == {rl.CAPABILITY_SOURCE, rl.BREAKER_SOURCE, rl.PROPOSAL_SOURCE}
    assert all(
        call["snapshot_complete"] and call["observations"] == [] for call in by_source.values()
    )


async def test_reset_requires_a_cause() -> None:
    pool = ScriptedPool({})
    assert (await rl.evaluate_reset(pool, None, ["boom"])).reason == "cause_ref_required"
    assert (await rl.evaluate_reset(pool, "x", ["boom"])).reason == "cause_ref_required"


async def test_reset_allowed_for_non_structural_cause_with_any_named_cause() -> None:
    decision = await rl.evaluate_reset(ScriptedPool({}), "execution_failure:fix-1", ["boom"] * 5)
    assert decision.allowed and decision.cause_code == "execution_failure"


async def test_reset_refused_when_structural_cause_is_not_named() -> None:
    decision = await rl.evaluate_reset(ScriptedPool({}), "execution_failure:retry", [PUB] * 5)
    assert not decision.allowed
    assert decision.reason == "structural_cause_not_named:publisher_unconfigured"


@pytest.fixture
def publisher_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    from butlers.core.qa import publisher_wiring

    monkeypatch.setattr(publisher_wiring, "production_publisher_wired", lambda: True)


async def test_reset_refused_while_the_named_structural_cause_is_still_present() -> None:
    """No reference buys a reset while the capability is still missing."""
    for ref in ("publisher_unconfigured:PR-1", "publisher_unconfigured:PR-2"):
        decision = await rl.evaluate_reset(ScriptedPool({"breaker_resets": None}), ref, [PUB] * 5)
        assert not decision.allowed
        assert decision.reason == "structural_cause_present:publisher_unconfigured"


async def test_presence_probes_are_closed_and_fail_toward_refusal() -> None:
    assert await rl.structural_cause_present(ScriptedPool({}), "no_such_cause") is None
    readable = ScriptedPool({"qa_publication_exports": 1})
    assert await rl.structural_cause_present(readable, "proposal_retention_unavailable") is False
    broken = ScriptedPool({}, fail=True)
    assert await rl.structural_cause_present(broken, "proposal_retention_unavailable") is True


async def test_reset_allowed_when_structural_cause_is_named_with_a_fresh_reference(
    publisher_wired: None,
) -> None:
    pool = ScriptedPool({"breaker_resets": None})
    decision = await rl.evaluate_reset(pool, "publisher_unconfigured:PR-1", [PUB] * 5)
    assert decision.allowed
    assert (decision.cause_code, decision.cause_ref) == ("publisher_unconfigured", "PR-1")


async def test_reset_refused_when_the_same_claim_was_already_disproved(
    publisher_wired: None,
) -> None:
    pool = ScriptedPool({"breaker_resets": 1})
    decision = await rl.evaluate_reset(pool, "publisher_unconfigured:PR-1", [PUB] * 5)
    assert not decision.allowed
    assert decision.reason == "structural_cause_unchanged:publisher_unconfigured"


async def test_reset_refused_when_prior_evidence_is_unreadable(publisher_wired: None) -> None:
    decision = await rl.evaluate_reset(
        ScriptedPool({}, fail=True), "publisher_unconfigured:PR-1", [PUB] * 5
    )
    assert not decision.allowed and decision.reason == "evidence_unreadable"


class RecordingPool:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *args: Any) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.calls.append((sql, args))


async def test_refusal_is_persisted_as_a_refused_row_with_its_reason() -> None:
    decision = rl.ResetDecision(False, "execution_failure", "retry", "structural_cause_present:x")
    pool = RecordingPool()
    assert await rl.record_reset_refusal(pool, decision, reset_by="dashboard") is True
    sql, args = pool.calls[0]
    assert "'refused'" in sql
    assert "NOT EXISTS" in sql  # identical refusals inside the window are not re-inserted
    assert args == (
        "dashboard",
        "Manual reset refused",
        "execution_failure",
        "retry",
        "structural_cause_present:x",
        rl.REFUSAL_DEDUPE_WINDOW.total_seconds(),
    )


async def test_refusal_persistence_failure_is_reported_not_raised() -> None:
    decision = rl.ResetDecision(False, reason="cause_ref_required")
    assert await rl.record_reset_refusal(RecordingPool(fail=True), decision, reset_by="d") is False


def test_streak_and_admission_share_the_breaker_constants_and_ignore_refusals() -> None:
    from butlers.core.healing.constants import QA_BREAKER_WHERE_SQL

    assert QA_BREAKER_WHERE_SQL in rl.STREAK_SQL
    assert "outcome = 'reset'" in QA_BREAKER_WHERE_SQL
    assert "r.outcome = 'reset'" in rl._PRIOR_STRUCTURAL_SQL

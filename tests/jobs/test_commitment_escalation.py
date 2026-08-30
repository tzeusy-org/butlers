"""Decisions the commitment escalation job makes without a database (bu-n1evl).

RFC 0026 §6-§9 / REQ-commitment-lifecycle-005, -006. Everything here is a
choice the job makes before or independently of any SQL: the escalation-to-
priority mapping, the dedup-key shape the insight broker will accept, the
cadence it borrows from the ledger, and the resolution route it is forbidden
to take. The consequences that only a real ledger can show — the clock
actually advancing, grace actually collapsing, cancellation actually landing
top-level in ``metadata`` — live in
tests/integration/test_commitment_escalation.py.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta

import pytest

from butlers.core.commitments import (
    COMMITMENT_METADATA_CLASS,
    SURFACING_CONFIDENCE_THRESHOLD,
)
from butlers.core.condition_ledger import ESCALATION_ADVANCE
from butlers.jobs import commitment_escalation as job
from butlers.tools.switchboard.insight.broker import _DEDUP_KEY_PATTERN

_NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _module_ast(module: object) -> ast.Module:
    return ast.parse(inspect.getsource(module))


def _referenced_names(module: object) -> set[str]:
    """Return every name the module's *code* mentions, ignoring prose.

    A plain substring search over the source would be satisfied by a docstring
    that merely names the thing it promises never to call, which is exactly
    what this module's docstring does.
    """
    tree = _module_ast(module)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
    return names


def _imported_modules(module: object) -> set[str]:
    """Return every module this module imports, at top level or inside a function."""
    modules: set[str] = set()
    for node in ast.walk(_module_ast(module)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _row(
    *,
    level: str = "L1",
    confidence: float | None = 0.9,
    deadline: str | None = None,
    summary: str = "Send Sam the book",
    first_detected_at: datetime | None = None,
    last_confirmed_at: datetime | None = None,
) -> dict:
    metadata: dict = {
        "class": COMMITMENT_METADATA_CLASS,
        "kind": "promise",
        "direction": "owner_to_other",
        "counterparty_entity_id": "11111111-2222-3333-4444-555555555555",
    }
    if confidence is not None:
        metadata["confidence"] = confidence
    if deadline is not None:
        metadata["deadline"] = deadline
    return {
        "id": 1,
        "source": "relationship:commitment",
        "fingerprint": "a" * 64,
        "state": "aging",
        "first_detected_at": first_detected_at or (_NOW - timedelta(days=11)),
        "last_confirmed_at": last_confirmed_at or (_NOW - timedelta(days=11)),
        "next_reescalate_at": _NOW + timedelta(days=1),
        "escalation_level": level,
        "summary": summary,
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-005 — surfacing shape
# ---------------------------------------------------------------------------


class TestSurfacingShape:
    def test_req_commitment_lifecycle_005_dedup_key_derives_from_the_fingerprint(self) -> None:
        """ "a dedup key derived from the commitment's fingerprint"."""
        row = _row()
        key = job._dedup_key(row["fingerprint"], row["escalation_level"])
        assert row["fingerprint"] in key
        assert key == f"commitment:{'a' * 64}:L1"

    def test_req_commitment_lifecycle_005_dedup_key_is_one_the_broker_accepts(self) -> None:
        """A two-segment ``commitment:{fingerprint}`` is rejected outright.

        The broker requires ``{category}:{entity}:{time-scope}``, so pinning
        the shape against the broker's own pattern is what stops criterion 6
        from being satisfied by a key every proposal would error on.
        """
        for scope in (*job.SURFACING_LEVELS, job.ARCHIVAL_DEDUP_SCOPE):
            assert _DEDUP_KEY_PATTERN.match(job._dedup_key("f" * 64, scope))
        assert not _DEDUP_KEY_PATTERN.match(f"commitment:{'f' * 64}")

    def test_req_commitment_lifecycle_005_archival_scope_never_collides_with_l3(self) -> None:
        """The archival question must not be deduplicated against L3 surfacing."""
        assert job.ARCHIVAL_DEDUP_SCOPE not in job.SURFACING_LEVELS
        assert job._dedup_key("f" * 64, "L3") != job._dedup_key("f" * 64, job.ARCHIVAL_DEDUP_SCOPE)

    def test_req_commitment_lifecycle_005_priority_rises_with_escalation_level(self) -> None:
        """ "the commitment's escalation level mapped to insight priority"."""
        assert sorted(job._PRIORITY_BY_LEVEL) == ["L1", "L2", "L3"]
        assert (
            job._PRIORITY_BY_LEVEL["L1"]
            < job._PRIORITY_BY_LEVEL["L2"]
            < job._PRIORITY_BY_LEVEL["L3"]
        )

    def test_req_commitment_lifecycle_005_priorities_are_in_the_brokers_range(self) -> None:
        """The broker rejects anything outside 1-100 before it inserts."""
        for priority in (*job._PRIORITY_BY_LEVEL.values(), job._ARCHIVAL_PRIORITY):
            assert isinstance(priority, int)
            assert 1 <= priority <= 100

    def test_req_commitment_lifecycle_005_no_level_forces_quiet_hours(self) -> None:
        """A weeks-ignored commitment is the wrong thing to push past a hold.

        Anything at or above the attention ledger's urgent threshold bypasses
        the suppression the delivery cycle applies; a commitment that has
        provably waited days for attention has not earned that.
        """
        from butlers.core.attention_ledger import URGENT_PRIORITY_THRESHOLD

        for priority in (*job._PRIORITY_BY_LEVEL.values(), job._ARCHIVAL_PRIORITY):
            assert priority < URGENT_PRIORITY_THRESHOLD

    def test_req_commitment_lifecycle_005_summary_is_commitment_specific(self) -> None:
        """ "a commitment-specific summary" — the promise, its deadline, its age."""
        row = _row(level="L2", deadline="2026-09-01T09:00:00+00:00")
        message = job._surfacing_message(row, now=_NOW)
        assert "Send Sam the book" in message
        assert "2026-09-01" in message
        assert "L2" in message
        assert "11d" in message

    def test_req_commitment_lifecycle_005_a_summaryless_commitment_still_proposes(self) -> None:
        """The broker rejects an empty message, so a blank summary must not produce one."""
        assert job._surfacing_message(_row(summary="   "), now=_NOW).strip()

    def test_req_commitment_lifecycle_005_candidate_metadata_identifies_the_commitment(
        self,
    ) -> None:
        """A responder needs the ledger identity, not just the prose."""
        metadata = job._candidate_metadata(_row(), proposal="surfacing")
        assert metadata["class"] == COMMITMENT_METADATA_CLASS
        assert metadata["source"] == "relationship:commitment"
        assert metadata["fingerprint"] == "a" * 64
        assert metadata["escalation_level"] == "L1"
        assert metadata["proposal"] == "surfacing"

    def test_req_commitment_lifecycle_005_insight_is_attributed_to_the_filing_butler(self) -> None:
        """Source is ``{origin_butler}:{category}``; the insight belongs to the butler."""
        assert job._origin_butler("relationship:commitment") == "relationship"
        assert job._origin_butler("health:follow-up") == "health"
        assert job._origin_butler("bare") == "bare"
        assert job._origin_butler(":commitment") == "switchboard"


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-005 — cadence borrowed from the ledger
# ---------------------------------------------------------------------------


class TestCadence:
    def test_commitment_job_borrows_the_one_public_ledger_schedule(self) -> None:
        """A copied cadence could agree today and silently diverge tomorrow."""
        from butlers.core import condition_ledger

        assert hasattr(condition_ledger, "ESCALATION_ADVANCE")
        assert not hasattr(condition_ledger, "_ESCALATION_ADVANCE")
        assert job.ESCALATION_ADVANCE is condition_ledger.ESCALATION_ADVANCE

    def test_req_commitment_lifecycle_005_dwell_times_match_the_ledger_schedule(self) -> None:
        """The job's cooldowns are the ledger's schedule, not a second copy of it.

        ``ESCALATION_ADVANCE`` is keyed by the level a condition is AT and
        yields (level it moves to, interval until the FOLLOWING due date), so
        inverting it gives the dwell time of the level being entered. If that
        inversion is ever wrong, a commitment's cooldown and its escalation
        cadence disagree and one level surfaces twice or not at all.
        """
        assert job._DWELL_DAYS_BY_LEVEL == {"L1": 1, "L2": 3, "L3": 7}
        for at_level, (entered, interval) in ESCALATION_ADVANCE.items():
            assert job._DWELL_DAYS_BY_LEVEL[entered] == interval.days, at_level

    def test_req_commitment_lifecycle_005_surfacing_starts_above_the_grace_level(self) -> None:
        """ "commitments at L1 or above" — L0 is grace and is never surfaced."""
        assert "L0" not in job.SURFACING_LEVELS
        assert job.SURFACING_LEVELS == ("L1", "L2", "L3")

    def test_req_commitment_lifecycle_005_every_surfacing_level_has_a_priority(self) -> None:
        assert set(job.SURFACING_LEVELS) == set(job._PRIORITY_BY_LEVEL)
        assert set(job.SURFACING_LEVELS) <= set(job._DWELL_DAYS_BY_LEVEL)

    def test_req_commitment_lifecycle_005_candidates_expire_within_one_tick(self) -> None:
        """A candidate outlives its tick but not the next one, so none accumulate."""
        assert job._CANDIDATE_TTL == timedelta(days=1)


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-004/-005 — the confidence band
# ---------------------------------------------------------------------------


class TestConfidenceBand:
    def test_req_commitment_lifecycle_005_threshold_is_the_shared_one(self) -> None:
        """The job applies the band ``commitments`` exports; it does not restate 0.8."""
        assert SURFACING_CONFIDENCE_THRESHOLD == 0.8

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.9, 0.9),
            (0.8, 0.8),
            (1, 1.0),
            (None, None),
            ("0.9", None),
            (True, None),
        ],
    )
    def test_req_commitment_lifecycle_005_confidence_reads_only_real_numbers(
        self, value: object, expected: float | None
    ) -> None:
        """A malformed confidence reads as absent, which the caller treats as unsurfaceable.

        Read in Python rather than cast in SQL on purpose: a ``::numeric`` cast
        over the result set would abort on one bad row and take every
        well-formed commitment with it.
        """
        assert job._confidence_of(_row(confidence=None) | {"metadata": {"confidence": value}}) == (
            expected
        )


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-005 — deadline reading
# ---------------------------------------------------------------------------


class TestDeadlineReading:
    def test_req_commitment_lifecycle_005_naive_deadline_is_read_as_utc(self) -> None:
        """``next_reescalate_at`` is always aware, so a naive deadline must be too.

        Without this the grace comparison raises ``TypeError`` and the whole
        tick dies on one commitment whose producer stored a naive timestamp.
        """
        parsed = job._deadline_of(_row(deadline="2026-09-01T09:00:00"))
        assert parsed == datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

    def test_req_commitment_lifecycle_005_aware_deadline_keeps_its_offset(self) -> None:
        parsed = job._deadline_of(_row(deadline="2026-09-01T09:00:00+08:00"))
        assert parsed == datetime(2026, 9, 1, 1, 0, tzinfo=UTC)

    @pytest.mark.parametrize("raw", ["", "not-a-date", "2026-13-45"])
    def test_req_commitment_lifecycle_005_unparseable_deadline_is_ignored_not_raised(
        self, raw: str
    ) -> None:
        """One producer's bad timestamp must not stop the sweep."""
        assert job._deadline_of(_row(deadline=raw)) is None

    def test_req_commitment_lifecycle_005_absent_deadline_is_none(self) -> None:
        assert job._deadline_of(_row()) is None


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-006 — the archival question
# ---------------------------------------------------------------------------


class TestArchivalQuestion:
    def test_req_commitment_lifecycle_006_stale_window_is_ninety_days(self) -> None:
        assert job.GC_STALE_DAYS == 90

    def test_req_commitment_lifecycle_006_archival_message_matches_the_spec_wording(self) -> None:
        """ "This commitment has been open for [N] days with no activity. Cancel or keep?"."""
        row = _row(level="L3", last_confirmed_at=_NOW - timedelta(days=97))
        message = job._archival_message(row, now=_NOW)
        assert message.startswith("This commitment has been open for 97 days with no activity.")
        assert "Cancel or keep?" in message
        assert "Send Sam the book" in message

    def test_req_commitment_lifecycle_006_n_counts_silence_not_age(self) -> None:
        """Silence is what triggered the question, so silence is the number shown."""
        row = _row(
            level="L3",
            first_detected_at=_NOW - timedelta(days=400),
            last_confirmed_at=_NOW - timedelta(days=91),
        )
        assert "91 days" in job._archival_message(row, now=_NOW)


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-006 — the resolution route
# ---------------------------------------------------------------------------


class TestResolutionRoute:
    def test_req_commitment_lifecycle_006_cancellation_never_builds_an_observation(self) -> None:
        """The reserved keys must reach the ledger as resolution metadata, not evidence.

        ``resolution_reason`` and ``evidence_closed`` are the ledger's own keys
        (``condition_ledger.RESOLUTION_METADATA_KEYS``): ``reconcile_snapshot``
        raises ``ValueError`` before any pool access if an observation carries
        either, and even if it did not, the creation-wins merge would let a
        producer's value beat the closing evidence. So this job must never
        construct an ``Observation`` at all — the only correct route is
        ``resolve_commitment`` -> ``resolve_condition``'s ``resolution_metadata``.
        """
        from butlers.core.condition_ledger import RESOLUTION_METADATA_KEYS

        assert RESOLUTION_METADATA_KEYS == {"resolution_reason", "evidence_closed"}
        assert not hasattr(job, "Observation")
        referenced = _referenced_names(job)
        assert "Observation" not in referenced
        assert "reconcile_snapshot" not in referenced
        assert "create_commitment" not in referenced
        assert "resolve_commitment" in referenced

    def test_req_commitment_lifecycle_006_cancellation_reason_is_a_ledger_vocabulary_term(
        self,
    ) -> None:
        """``resolve_commitment`` rejects anything outside ``RESOLUTION_REASONS``."""
        from butlers.core.commitments import RESOLUTION_REASONS

        assert "cancelled" in RESOLUTION_REASONS


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-005 — composition with the insight engine
# ---------------------------------------------------------------------------


class TestInsightEngineComposition:
    def test_req_commitment_lifecycle_005_the_real_broker_satisfies_the_protocol(self) -> None:
        """The injected seam must actually accept what the job passes it.

        A ``Protocol`` is not enforced at runtime, so nothing else would catch
        the job passing a keyword the broker does not take — the failure would
        surface as a ``TypeError`` inside ``_propose``'s blanket ``except``,
        counted as a proposal error and logged rather than raised.
        """
        from butlers.tools.switchboard.insight.broker import propose_insight_candidate

        broker = inspect.signature(propose_insight_candidate).parameters
        declared = inspect.signature(job.InsightProposer.__call__).parameters
        for name in declared:
            if name in ("self", "pool"):
                continue
            assert name in broker, f"job declares {name!r}, broker does not accept it"

    def test_req_commitment_lifecycle_005_job_only_reaches_the_engine_through_the_seam(
        self,
    ) -> None:
        """ "No changes to the insight engine itself" — and no back door into it.

        The job must not import the broker; the one entry point it uses is the
        injected proposer, so the composition boundary stays visible in the
        signature.
        """
        imported = _imported_modules(job)
        assert not any("insight" in module for module in imported), imported
        assert imported == {
            "__future__",
            "json",
            "logging",
            "collections.abc",
            "dataclasses",
            "datetime",
            "typing",
            "asyncpg",
            "butlers.core.commitments",
            "butlers.core.condition_ledger",
        }


# ---------------------------------------------------------------------------
# REQ-commitment-lifecycle-005 — the job is actually scheduled
# ---------------------------------------------------------------------------


class TestSchedulerRegistration:
    """ "A scheduled job SHALL check commitment-class ``owner_conditions``".

    A job that is written but not reachable satisfies nothing. The two halves
    of "scheduled" are a ``[[butler.schedule]]`` entry in the roster config and
    a handler under that name in the deterministic registry, and nothing else
    checks that the two agree — a typo in either is a silent no-op at runtime,
    with the scheduler simply never dispatching.
    """

    @staticmethod
    def _schedule_entry() -> dict:
        import tomllib
        from pathlib import Path

        config = tomllib.loads(
            (Path(__file__).resolve().parents[2] / "roster/switchboard/butler.toml").read_text()
        )
        entries = [
            entry
            for entry in config["butler"]["schedule"]
            if entry.get("job_name") == "commitment_escalation"
        ]
        assert len(entries) == 1, "expected exactly one commitment-escalation schedule entry"
        return entries[0]

    def test_req_commitment_lifecycle_005_the_roster_schedules_the_job(self) -> None:
        entry = self._schedule_entry()
        assert entry["dispatch_mode"] == "job"
        assert entry["cron"].split()[2:] == ["*", "*", "*"], "the tick must run daily"

    def test_req_commitment_lifecycle_005_the_scheduled_name_resolves_to_a_handler(self) -> None:
        """The registry is what turns the config's ``job_name`` into a call."""
        from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

        registry = get_deterministic_schedule_job_registry()
        assert self._schedule_entry()["job_name"] in registry["switchboard"]

    def test_req_commitment_lifecycle_006_the_daily_cadence_matches_the_candidate_ttl(self) -> None:
        """Candidates expire in one tick, so a slower cadence would leave gaps.

        ``_CANDIDATE_TTL`` is one day precisely because the job runs daily: a
        candidate this tick proposed is either delivered or replaced by the
        next tick's, never left to accumulate. A weekly cron would leave six
        days with no pending commitment candidate at all.
        """
        assert self._schedule_entry()["cron"].split()[2:] == ["*", "*", "*"]
        assert job._CANDIDATE_TTL == timedelta(days=1)

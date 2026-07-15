"""Day-close gap interview (bu-whhll.12, epic bu-whhll Tier 2).

Closes the inference loop opened by the occupation adapter (bu-whhll.10): after a
day is closed and reconciled, some waking time is still unexplained, or the only
thing covering the owner's workday is a *low-confidence* ``occupation_block``
that the deterministic pipeline was never sure about. This module decides —
deterministically — whether that day is worth **one** owner-facing confirmation
("Yesterday 09:30-19:30 looks like a work day — confirm?") and applies the
one-tap answer as a durable correction.

Why this is its own surface
---------------------------
The once-daily ``chronicler_day_close`` prompt is explicitly forbidden from
sending any extra proactive/correction message (see the ``prompt`` in
``roster/chronicler/butler.toml``: "those are separate, explicitly-opted-in
surfaces, not part of day-close"). The gap interview is exactly one of those
"separate, explicitly-opted-in surfaces": the day-close narration is left
untouched, and this runs as its own opt-in, owner-toggleable, **max one
message per day** surface.

Two halves, cleanly split (mirrors ``rollups.py`` / ``routines.py``)
--------------------------------------------------------------------
- :func:`evaluate_gap_interview` — a **pure** function over in-memory episode
  rows. No I/O, no clock reads, no LLM. It decides whether a day qualifies and
  builds the exact question text. This is the unit-tested core.
- :func:`apply_gap_interview_answer` — the async writer that turns a one-tap
  answer into (1) a ``chronicler.overrides`` row (this is the *first real
  tenant* of the corrections machinery — 0 override rows had ever been written
  before this feature) and (2) a reinforce/decay nudge on the matching
  ``chronicler.routines`` row.

Transport isolation (coordinator decision on bu-whhll.12)
---------------------------------------------------------
The decision loop (RFC 0021 one-tap approvals + decision memory, epic bu-24lu6)
is the intended long-term home for this prompt but is still owner-gated behind
``bu-24lu6.1``. Per the coordinator's recorded decision, the ask/answer
*transport* is isolated behind the small :class:`GapInterviewTransport`
interface so the same deterministic engine can migrate onto the decision loop
when it releases, without touching this module. Nothing here imports or depends
on bu-24lu6 code.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from butlers.chronicler.adapters.occupation import (
    EPISODE_TYPE_OCCUPATION,
)
from butlers.chronicler.adapters.occupation import (
    SOURCE_NAME as OCCUPATION_SOURCE_NAME,
)
from butlers.chronicler.aggregations import untracked_seconds_for_window

# ── Tunables ────────────────────────────────────────────────────────────────

# ">2h of waking time unaccounted" (bead acceptance criterion). Kept as a
# module constant so the scheduled surface and the tests share one source of
# truth.
DEFAULT_UNACCOUNTED_THRESHOLD_SECONDS: int = 2 * 60 * 60

# Waking-hour window the unaccounted-time universe is measured against. Matches
# ``editorial.WAKING_HOUR_START``/``WAKING_HOUR_END`` (the same "awake" window
# the KPI waking-gap detector uses) — imported lazily by the scheduled surface,
# duplicated here only as defaults so the pure function stays dependency-light.
DEFAULT_WAKING_HOUR_START: int = 6
DEFAULT_WAKING_HOUR_END: int = 22

# Reinforce/decay step applied to the matching routine's confidence (a
# DOUBLE PRECISION in [0, 1], per migration ``chronicler_018``). A single
# owner confirmation should nudge, not slam, the mined statistic — the weekly
# miner still owns the bulk of the signal.
ROUTINE_REINFORCE_DELTA: float = 0.10
ROUTINE_DECAY_DELTA: float = 0.15


class GapInterviewAnswer(enum.StrEnum):
    """The three one-tap answers the owner can give.

    ``confirm`` — "yes, that was a work day": keep the inferred block, reinforce
        the routine.
    ``correct`` — "no, that was not a work day": tombstone the inferred block via
        an override, decay the routine.
    ``dismiss`` — "don't count this either way": record the interaction as an
        override note, leave the routine untouched.
    """

    CONFIRM = "confirm"
    CORRECT = "correct"
    DISMISS = "dismiss"


# ── Episode-field access (tolerates enum-or-str + canonical-or-raw shapes) ──
#
# Episode rows reach this module in two shapes: ``dataclasses.asdict(Episode)``
# (Enum members, raw ``start_at``) and ``v_episodes_corrected`` SQL rows (plain
# strings, ``canonical_start_at``). These helpers normalise both, exactly like
# ``reconciliation.py`` does.


def _str_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _coerce_dt(value: Any) -> datetime | None:
    """Normalise a start/end field to ``datetime``.

    ``asdict(Episode)`` rows carry ``datetime`` already; ``v_episodes_corrected``
    SQL rows can arrive as ISO strings (the two shapes this module documents it
    tolerates). Empty/missing values pass through as ``None`` so a malformed row
    is skipped by the caller's presence checks rather than crashing sorting or
    duration math downstream.
    """
    if not value:
        return None
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _window(ep: Mapping[str, Any]) -> tuple[datetime | None, datetime | None]:
    start = _coerce_dt(ep.get("canonical_start_at") or ep.get("start_at"))
    end = _coerce_dt(ep.get("canonical_end_at") or ep.get("end_at")) or start
    return start, end


def _is_low_confidence_occupation(ep: Mapping[str, Any]) -> bool:
    return (
        ep.get("source_name") == OCCUPATION_SOURCE_NAME
        and ep.get("episode_type") == EPISODE_TYPE_OCCUPATION
        and _str_value(ep.get("confidence", "low")) == "low"
    )


def _local_hhmm(dt: datetime, tz: tzinfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


def _coerce_uuid(value: Any) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


# ── Decision (output of the pure evaluator) ─────────────────────────────────


@dataclass(frozen=True)
class GapInterviewDecision:
    """A day that qualifies for one gap-interview prompt.

    ``local_date``
        ISO ``YYYY-MM-DD`` of the closed local day the prompt is about.
    ``question``
        The exact owner-facing one-line question.
    ``reasons``
        Why the day qualified: any of ``"unaccounted_gap"`` /
        ``"low_confidence_occupation"``. Never empty.
    ``unaccounted_seconds``
        Waking-window seconds left unexplained (informational; the trigger
        already fired).
    ``occupation_episode_id``
        The low-confidence ``occupation_block`` the answer's override targets,
        or ``None`` when only the unaccounted-gap rule fired (nothing inferred
        to correct — a ``confirm`` then only reinforces the routine).
    ``routine_id``
        The ``chronicler.routines`` row to reinforce/decay, resolved from the
        occupation block's payload when present.
    ``window_start_local`` / ``window_end_local``
        Local ``HH:MM`` bounds of the block in question (``None`` when there is
        no occupation block to bound it).
    """

    local_date: str
    question: str
    reasons: tuple[str, ...]
    unaccounted_seconds: float
    occupation_episode_id: UUID | None = None
    routine_id: UUID | None = None
    window_start_local: str | None = None
    window_end_local: str | None = None
    options: tuple[str, ...] = (
        GapInterviewAnswer.CONFIRM.value,
        GapInterviewAnswer.CORRECT.value,
        GapInterviewAnswer.DISMISS.value,
    )


def _format_question(
    *,
    window_start_local: str | None,
    window_end_local: str | None,
    unaccounted_seconds: float,
    has_occupation: bool,
) -> str:
    """Build the single owner-facing question line, deterministically."""
    if has_occupation and window_start_local and window_end_local:
        return f"Yesterday {window_start_local}-{window_end_local} looks like a work day — confirm?"
    hours = unaccounted_seconds / 3600.0
    return f"Yesterday has about {hours:.1f}h of unaccounted waking time — was that a work day?"


def evaluate_gap_interview(
    episodes: Sequence[Mapping[str, Any]],
    *,
    local_date: str,
    day_start_utc: datetime,
    day_end_utc: datetime,
    tz: tzinfo,
    waking_hour_start: int = DEFAULT_WAKING_HOUR_START,
    waking_hour_end: int = DEFAULT_WAKING_HOUR_END,
    unaccounted_threshold_seconds: int = DEFAULT_UNACCOUNTED_THRESHOLD_SECONDS,
) -> GapInterviewDecision | None:
    """Decide whether a closed local day warrants one gap-interview prompt.

    Pure function — no I/O, no LLM, no clock reads. ``episodes`` are the day's
    rows (any layer/shape; the same rows the day-close bundle reads). Returns a
    :class:`GapInterviewDecision` when the day qualifies, else ``None``.

    Trigger (either condition qualifies the day):

    1. **Unaccounted waking time** — more than ``unaccounted_threshold_seconds``
       of the local waking window (``[waking_hour_start, waking_hour_end)``) is
       not covered by any ``activity``-layer episode. Reuses
       :func:`aggregations.untracked_seconds_for_window`, the exact same math
       the aggregate pie's "untracked" slice uses (bu-whhll.13), so the prompt
       can never disagree with what the owner sees on the dashboard.
    2. **Low-confidence occupation** — at least one ``occupation_block``
       (always emitted at ``confidence=low`` by the occupation adapter) is
       present. The pipeline recognised a probable workday but was never sure;
       one tap either confirms it or corrects it.
    """
    activity_intervals: list[tuple[datetime, datetime]] = []
    occupation_blocks: list[Mapping[str, Any]] = []

    for ep in episodes:
        layer = _str_value(ep.get("layer", "evidence"))
        start, end = _window(ep)
        if layer == "activity" and start is not None and end is not None:
            # Every activity-layer span counts toward "tracked" (sleep included)
            # — an unmapped-lane activity is a "don't know how to bucket this"
            # problem, not "nothing happened", so it must not inflate the gap.
            activity_intervals.append((start, end))
        if _is_low_confidence_occupation(ep):
            occupation_blocks.append(ep)

    unaccounted_seconds = untracked_seconds_for_window(
        activity_intervals,
        day_start_utc,
        day_end_utc,
        tz,
        waking_hour_start=waking_hour_start,
        waking_hour_end=waking_hour_end,
    )

    reasons: list[str] = []
    if unaccounted_seconds > unaccounted_threshold_seconds:
        reasons.append("unaccounted_gap")
    if occupation_blocks:
        reasons.append("low_confidence_occupation")

    if not reasons:
        return None

    # The earliest-starting occupation block anchors the question window and the
    # override target; its payload carries the routine to reinforce/decay.
    occupation_episode_id: UUID | None = None
    routine_id: UUID | None = None
    window_start_local: str | None = None
    window_end_local: str | None = None
    if occupation_blocks:
        anchor = min(occupation_blocks, key=lambda e: _window(e)[0])
        occupation_episode_id = _coerce_uuid(anchor.get("id"))
        payload = anchor.get("payload") or {}
        if isinstance(payload, Mapping):
            routine_id = _coerce_uuid(payload.get("routine_id"))
        a_start, a_end = _window(anchor)
        if a_start is not None and a_end is not None:
            window_start_local = _local_hhmm(a_start, tz)
            window_end_local = _local_hhmm(a_end, tz)

    question = _format_question(
        window_start_local=window_start_local,
        window_end_local=window_end_local,
        unaccounted_seconds=unaccounted_seconds,
        has_occupation=bool(occupation_blocks),
    )

    return GapInterviewDecision(
        local_date=local_date,
        question=question,
        reasons=tuple(reasons),
        unaccounted_seconds=unaccounted_seconds,
        occupation_episode_id=occupation_episode_id,
        routine_id=routine_id,
        window_start_local=window_start_local,
        window_end_local=window_end_local,
    )


# ── Answer application (writes the override + reinforce/decay) ───────────────


async def apply_gap_interview_answer(
    chronicler_pool: Any,
    *,
    answer: GapInterviewAnswer,
    local_date: str,
    occupation_episode_id: UUID | None,
    routine_id: UUID | None,
    submitted_by: str = "owner:gap_interview",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply a one-tap gap-interview answer durably.

    First real tenant of the corrections machinery: writes a
    ``chronicler.overrides`` row (when there is an inferred block to target) and
    nudges the matching routine's confidence. Deterministic — the caller
    supplies ``now`` in tests. Returns a JSON-friendly summary.

    - ``confirm`` — keep the block; reinforce the routine (+delta, +1 support).
    - ``correct`` — the block was wrong; tombstone it via an override
      (``corrected_tombstone_at``) and decay the routine (-delta).
    - ``dismiss`` — record an override note only; leave the routine untouched.
    """
    from butlers.chronicler.models import Override, OverrideTarget
    from butlers.chronicler.storage import adjust_routine_confidence, insert_override

    # ``now`` stamps the tombstone on a ``correct``. Defaulting it here (rather
    # than leaving ``None``) means a caller that forgets to pass it can never
    # silently write a non-tombstoning "correction" — the fail mode this
    # feature exists to avoid. Tests still inject ``now`` for determinism.
    if now is None:
        now = datetime.now(UTC)

    answer = GapInterviewAnswer(answer)
    override_id: str | None = None
    routine_updated = False

    if occupation_episode_id is not None:
        note = (
            f"Gap interview {local_date}: owner "
            f"{'confirmed' if answer is GapInterviewAnswer.CONFIRM else answer.value}"
            f" the inferred work day."
        )
        override = Override(
            target_kind=OverrideTarget.EPISODE,
            target_id=occupation_episode_id,
            # 'correct' means the inferred occupation block was wrong: tombstone
            # it so it stops counting. 'confirm'/'dismiss' only annotate.
            corrected_tombstone_at=(now if answer is GapInterviewAnswer.CORRECT else None),
            note=note,
            submitted_by=submitted_by,
        )
        saved = await insert_override(chronicler_pool, override)
        override_id = str(saved.id)

    if routine_id is not None:
        if answer is GapInterviewAnswer.CONFIRM:
            updated = await adjust_routine_confidence(
                chronicler_pool,
                routine_id,
                confidence_delta=ROUTINE_REINFORCE_DELTA,
                support_delta=1,
            )
            routine_updated = updated is not None
        elif answer is GapInterviewAnswer.CORRECT:
            updated = await adjust_routine_confidence(
                chronicler_pool,
                routine_id,
                confidence_delta=-ROUTINE_DECAY_DELTA,
            )
            routine_updated = updated is not None

    return {
        "status": "applied",
        "answer": answer.value,
        "local_date": local_date,
        "override_id": override_id,
        "routine_updated": routine_updated,
    }


# ── One-tap callback round-trip (cgi: prefix) ───────────────────────────────
#
# The telegram inline-button transport encodes each button's answer into a
# ``callback_data`` string; the shared telegram_bot connector recognises the
# ``cgi:`` prefix (and ONLY that prefix — every other callback keeps its
# current drop behaviour) and hands the payload back to
# :func:`resolve_gap_interview_callback`. When the decision loop (RFC 0021)
# takes over the transport, it reuses the same resolver — only the encoding
# changes.

CALLBACK_PREFIX = "cgi:"


def build_callback_data(interview_id: str, answer: GapInterviewAnswer | str) -> str:
    """Encode one inline button's ``callback_data`` (``cgi:<interview_id>:<answer>``).

    Telegram caps ``callback_data`` at 64 bytes; ``interview_id`` is the closed
    day's ``date_label`` (``<YYYY-MM-DD>``, 10 chars; the ask tool sets
    ``interview_id == date_label``, one prompt per day), so the longest payload
    (``cgi:2026-07-02:confirm`` = 22 bytes) stays well under the cap.
    """
    return f"{CALLBACK_PREFIX}{interview_id}:{GapInterviewAnswer(answer).value}"


def parse_gap_interview_callback(data: str | None) -> tuple[str, str] | None:
    """Decode a ``cgi:`` ``callback_data`` into ``(interview_id, answer)``.

    Returns ``None`` for any payload that is not a well-formed gap-interview
    callback — the connector uses this both as the recognizer (``None`` ⇒ not
    ours, drop as before) and the parser. ``rpartition`` splits on the *last*
    colon so an ``interview_id`` that itself contains colons round-trips.
    """
    if not data or not data.startswith(CALLBACK_PREFIX):
        return None
    interview_id, sep, answer = data[len(CALLBACK_PREFIX) :].rpartition(":")
    if not sep or not interview_id or not answer:
        return None
    return interview_id, answer


async def resolve_gap_interview_callback(
    pool: Any,
    *,
    interview_id: str,
    answer: str,
    now: datetime,
) -> dict[str, Any]:
    """Apply a one-tap answer identified by ``interview_id`` (idempotent).

    Reads the pending mapping stashed by the ask side from the KV ``state``
    store, applies the answer via :func:`apply_gap_interview_answer`, and marks
    the interview answered so a duplicate tap (telegram re-delivers callbacks on
    retry) is a no-op. Shared by the ``chronicler_resolve_gap_interview`` MCP
    tool and the telegram_bot connector's ``cgi:`` handler; both callers pass a
    chronicler-scoped pool. Never raises for owner-facing conditions: an unknown
    or already-answered interview and an unparseable answer all return a
    ``status`` the caller can surface as a graceful toast.
    """
    return await _resolve_on_conn(pool, interview_id, answer, now)


async def _resolve_on_conn(
    conn: Any, interview_id: str, answer: str, now: datetime
) -> dict[str, Any]:
    from butlers.core.state import state_get, state_set

    pending_key = f"gap_interview:pending:{interview_id}"
    pending = await state_get(conn, pending_key)
    if pending is None:
        return {
            "status": "error",
            "error": "unknown_or_expired_interview",
            "interview_id": interview_id,
        }
    if pending.get("answered"):
        return {"status": "already_answered", "interview_id": interview_id}
    try:
        parsed = GapInterviewAnswer(str(answer).strip().lower())
    except ValueError:
        return {
            "status": "error",
            "error": f"invalid answer {answer!r}; expected confirm/correct/dismiss",
            "interview_id": interview_id,
        }

    occ = pending.get("occupation_episode_id")
    rid = pending.get("routine_id")
    result = await apply_gap_interview_answer(
        conn,
        answer=parsed,
        local_date=pending["local_date"],
        occupation_episode_id=UUID(occ) if occ else None,
        routine_id=UUID(rid) if rid else None,
        now=now,
    )
    pending["answered"] = True
    await state_set(conn, pending_key, pending)
    result["interview_id"] = interview_id
    return result


# ── Transport seam (telegram now, decision loop later) ──────────────────────


@dataclass(frozen=True)
class GapInterview:
    """A qualifying decision bound to a stable id for one-tap round-tripping.

    ``interview_id`` is the correlation token the transport encodes into its
    one-tap controls (e.g. telegram ``callback_data``) and hands back to
    :func:`apply_gap_interview_answer` when the owner answers.
    """

    interview_id: str
    decision: GapInterviewDecision


@dataclass(frozen=True)
class TransportResult:
    """Outcome of a transport's outbound ask."""

    delivered: bool
    detail: str = ""
    reference: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class GapInterviewTransport(Protocol):
    """The small ask/answer seam the coordinator asked for on bu-whhll.12.

    A transport is responsible only for *delivering the question* and getting
    the one-tap answer back to the engine; it holds no reconciliation or
    correction logic. The telegram inline-button transport implements this
    today; the decision loop (RFC 0021) becomes a drop-in replacement once
    ``bu-24lu6.1`` releases — no change to the engine above.
    """

    async def deliver_interview(self, interview: GapInterview) -> TransportResult:
        """Send the one-tap question to the owner. Idempotency/dedupe and
        quiet-hours gating are the *caller's* responsibility (they are
        transport-independent); a transport only performs the delivery."""
        ...


async def run_gap_interview(
    episodes: Sequence[Mapping[str, Any]],
    *,
    local_date: str,
    day_start_utc: datetime,
    day_end_utc: datetime,
    tz: tzinfo,
    interview_id: str,
    transport: GapInterviewTransport,
    already_asked: Any,
    mark_asked: Any,
    delivery_allowed: Any,
    waking_hour_start: int = DEFAULT_WAKING_HOUR_START,
    waking_hour_end: int = DEFAULT_WAKING_HOUR_END,
    unaccounted_threshold_seconds: int = DEFAULT_UNACCOUNTED_THRESHOLD_SECONDS,
) -> dict[str, Any]:
    """Orchestrate one gap-interview cycle for a closed day.

    Transport-agnostic and side-effect-injected so the whole one-message-per-day
    contract is unit-testable with fakes: ``already_asked``/``mark_asked`` are
    the per-day dedupe (backed by the butler KV state store in production),
    ``delivery_allowed`` is the quiet-hours / delivery-preferences gate, and
    ``transport`` is the ask seam. All three are awaitables.

    Ordering guarantees the acceptance criteria:

    1. **Dedupe first** — if the day was already asked, return immediately
       (never a second prompt for the same day), before any evaluation.
    2. **Evaluate** — return ``no_gap`` when nothing qualifies (no prompt when
       nothing qualifies).
    3. **Quiet-hours gate** — a deferred day is *not* marked asked, so it can be
       retried once delivery is allowed again.
    4. **Deliver, then mark** — the day is recorded as asked only after the
       transport confirms delivery, so a transient send failure does not burn
       the single daily prompt.
    """
    if await already_asked():
        return {"status": "already_asked", "local_date": local_date}

    decision = evaluate_gap_interview(
        episodes,
        local_date=local_date,
        day_start_utc=day_start_utc,
        day_end_utc=day_end_utc,
        tz=tz,
        waking_hour_start=waking_hour_start,
        waking_hour_end=waking_hour_end,
        unaccounted_threshold_seconds=unaccounted_threshold_seconds,
    )
    if decision is None:
        return {"status": "no_gap", "local_date": local_date}

    if not await delivery_allowed():
        return {"status": "deferred_quiet_hours", "local_date": local_date}

    result = await transport.deliver_interview(
        GapInterview(interview_id=interview_id, decision=decision)
    )
    if not result.delivered:
        return {
            "status": "delivery_failed",
            "local_date": local_date,
            "detail": result.detail,
        }

    await mark_asked(decision, interview_id)
    return {
        "status": "asked",
        "local_date": local_date,
        "interview_id": interview_id,
        "reasons": list(decision.reasons),
        "reference": result.reference,
    }


__all__ = [
    "CALLBACK_PREFIX",
    "DEFAULT_UNACCOUNTED_THRESHOLD_SECONDS",
    "DEFAULT_WAKING_HOUR_END",
    "DEFAULT_WAKING_HOUR_START",
    "ROUTINE_DECAY_DELTA",
    "ROUTINE_REINFORCE_DELTA",
    "GapInterview",
    "GapInterviewAnswer",
    "GapInterviewDecision",
    "GapInterviewTransport",
    "TransportResult",
    "apply_gap_interview_answer",
    "build_callback_data",
    "evaluate_gap_interview",
    "parse_gap_interview_callback",
    "resolve_gap_interview_callback",
    "run_gap_interview",
]

"""Bounded once-daily LLM labeling pass over daily rollups + anomaly flags
(bu-v9y18, telemetry-distillation bead 6, design doc §3.5/§6.6 + openspec
change ``chronicler-telemetry-distillation`` spec.md "Bounded Once-Daily LLM
Labeling (Optional)").

This is the **only** LLM call anywhere in the telemetry-distillation
capability (RFC 0014 §D5 — no per-event LLM). It runs at most once per local
calendar day, strictly *after* ``rollups.materialize_daily_rollups`` and
``flags.evaluate_and_write_daily_flags`` have written that day's rows
(bead 3/4), and reads only the already-reduced output of those two passes
plus a small, capped number of episode titles — never raw
``connectors.filtered_events``/``connectors.owntracks_points``/episode rows
at scale. Output is presentation polish only: a short natural-language
label per anomaly flag, and/or a one-line day summary. The deterministic
``daily_rollups``/``daily_rollup_flags`` rows are already complete and
correct without this pass (spec.md "Disabling the labeling pass preserves
correctness") — disabling it (``CHRONICLER_NARRATION_ENABLED=false``) or a
failed/degraded LLM call never blocks or corrupts them; it only leaves the
``narrative`` columns unset.

Honesty doctrine (classify-before-flagging, applied to narration): a day
with nothing genuine to narrate must skip cleanly and write nothing, never a
fabricated label. Gating is per lane, read from the frozen
``day_source_availability`` snapshot. Skip conditions, distinguishable in the
returned ``status``:

- ``skipped_no_rollup_data`` — no ``daily_rollups`` rows exist yet for the
  local date.
- ``skipped_all_lanes_dark`` — every rolled-up lane was uncaptured (dark,
  retired or configured-absent) that day.
- ``skipped_availability_unknown`` — the day was never snapshotted (or the
  snapshot is unreadable), so lane capture cannot be established.

A day with some uncaptured lanes is still narrated: the prompt carries
``uncaptured_lanes`` and the model is forbidden to describe activity or its
absence there; ``parse_narration_response`` rejects a summary that pairs a
negation with a keyword of an uncaptured lane (``llm_output_invalid``).

A further-degraded outcome, distinct from both skips, is surfaced if the
LLM call itself fails or returns unusable output (``llm_unavailable`` /
``llm_output_invalid``) — "the pass tried and failed" is not the same as
"the pass correctly found nothing to say."

Structurally mirrors ``rollups.py``/``flags.py``'s split: pure functions
(``should_skip_narration``, ``select_top_episode_titles``) exercised
directly by unit tests, and :func:`narrate_daily_rollup` as the async
orchestrator.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg

from butlers.chronicler.aggregations import lane_for_activity
from butlers.chronicler.flags import (
    load_day_availability,
    load_day_retired_since,
    uncaptured_lanes,
)
from butlers.chronicler.history_repair import (
    RepairAdmissionError,
    capture_fact_write_token,
    make_fact_write_guard,
)
from butlers.chronicler.storage import (
    list_daily_rollup_flags,
    list_daily_rollups,
    set_daily_rollup_day_narrative,
    set_daily_rollup_flag_narrative,
)
from butlers.core.discretion_dispatcher import DiscretionDispatcher
from butlers.core.model_routing import Complexity
from butlers.core.tool_call_capture import get_current_codex_auth_authority

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Asia/Singapore"

# [decision] Presentation-polish labeling of already-reduced (double-digit
# row count) rollup output — the cheapest catalog tier is the right fit,
# same posture as the dashboard briefing elaboration
# (`api/briefing/prompts.py::elaborate_llm`), the closest existing precedent
# for "single-turn, catalog-backed, narration-not-classification" LLM calls.
_COMPLEXITY_TIER = Complexity.CHEAP

# The butler identity forwarded to model resolution. Using the real butler
# name (rather than a synthetic identity like the briefing module's
# "__dashboard_briefing__") lets an operator target this specific call with
# a `public.butler_model_overrides` row scoped to "chronicler" if desired.
_BUTLER_NAME = "chronicler"

# Design doc §3.5: "episode titles for the top 1-2 episodes per lane".
TOP_EPISODES_PER_LANE = 2

# Same owner-toggle convention as `connectors/heartbeat.py`'s
# CONNECTOR_HEARTBEAT_ENABLED: read fresh on every call (not cached), so an
# operator flipping it takes effect on the next scheduled run without a
# daemon restart. Default enabled — the pass is additive/optional per the
# design, not opt-in-by-default.
_ENABLED_ENV_VAR = "CHRONICLER_NARRATION_ENABLED"

# Mirrors rollups.py's `_DEFAULT_PRIVACY_TIERS` (private to that module, so
# duplicated here rather than importing a leading-underscore name): the same
# default privacy filter `GET /aggregate/by-category` applies when its
# caller omits `privacy_tier`. Keeping the episode-title fetch consistent
# with the rollup's own privacy default means a narrated title can never
# reveal something the rollup itself would have excluded.
_DEFAULT_PRIVACY_TIERS: tuple[str, ...] = ("normal", "sensitive")

_SYSTEM_PROMPT = """\
You label one already-computed day of a personal activity rollup for a multi-agent \
dashboard. You are not inferring behavior from raw data — the lane totals and anomaly \
flags in the input are already final and correct; your only job is to make them \
readable.

Voice rules (all mandatory, per the dashboard's design language):
- Past tense for the day's events. No first person ("I," "we," "our").
- Avoid "your" when "the" works. Write "the day" not "your day."
- No hedging adverbs: do not write "currently," "presently," "just," "simply," \
or "basically."
- No exclamation marks. No em-dashes (use a comma, colon, or parentheses).
- Do not restate raw numbers already visible elsewhere (e.g. exact seconds) — \
describe the shape of the day in plain language instead.
- Never assert something the input does not support. If a flag's detail is sparse, \
write a short, honest, generic label rather than fabricating specifics.

Output STRICT JSON only, no markdown fences, no prose outside the JSON object, \
matching exactly this shape:
{"day_summary": "one sentence, max 40 words, or empty string if nothing notable",
 "flag_labels": {"<flag_type>": "one short clause, max 20 words", ...}}

The input's "uncaptured_lanes" lists lanes whose data source was dark or not captured \
that day. For those lanes the data says nothing: never describe activity in them, and \
never describe its absence (no "skipped", "no meals", "without"). Their absence is a \
capture gap, not behavior. You may say plainly that such a lane was "not captured".

Include a key in "flag_labels" ONLY for flag_type values that appear in the input's \
"flags" list. Never invent a flag_type not present in the input. If a field has \
nothing worth saying, use an empty string for it rather than omitting the key.
"""


def narration_enabled() -> bool:
    """Owner toggle, read fresh on every call (see ``_ENABLED_ENV_VAR``)."""
    raw = os.environ.get(_ENABLED_ENV_VAR, "true").lower()
    return raw not in ("false", "0", "no", "off")


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def should_skip_narration(
    rollup_rows: Sequence[Any],
    flag_rows: Sequence[Any],
    *,
    uncaptured: Sequence[Mapping[str, Any]] | None,
) -> tuple[bool, str | None]:
    """Whether the labeling pass has nothing genuine to narrate.

    Returns ``(True, reason)`` for a clean skip, ``(False, None)`` otherwise.
    Gating is per lane: a day is skipped only when it has no rollup rows
    (``no_rollup_data``), when its frozen availability is unreadable
    (``availability_unknown``; ``uncaptured is None``), or when every rolled-up
    lane was uncaptured (``all_lanes_dark``). A ``feeder_dark`` flag alone does
    not skip the day; the prompt tells the model which lanes it must not
    describe. Pure function: no I/O, no LLM, no side effects.
    """
    if not rollup_rows:
        return True, "no_rollup_data"
    if uncaptured is None:
        return True, "availability_unknown"
    dark_lanes = {u["lane"] for u in uncaptured}
    if all(r.lane in dark_lanes for r in rollup_rows):
        return True, "all_lanes_dark"
    return False, None


# Fixed per-lane keywords for the absence guard. A summary that pairs a negation with one of
# these for an uncaptured lane asserts absence the data cannot support.
_LANE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sleep": ("sleep", "slept", "nap", "bed"),
    "exercise": ("exercise", "workout", "run", "ran", "gym", "walk"),
    "work": ("work", "worked", "meeting", "office"),
    "butler_ops": ("butler", "session", "task"),
    "play": ("play", "played", "game", "gaming"),
    "social": ("social", "message", "messages", "call", "chat"),
    "travel": ("travel", "trip", "commute", "moved", "location"),
    "eat": ("meal", "meals", "eat", "ate", "eaten", "lunch", "dinner", "breakfast", "food"),
    "rest": ("rest", "relax", "idle"),
}
_NEGATION_WORDS = frozenset(
    {"no", "not", "never", "none", "nothing", "without", "skipped", "skip", "missed", "zero",
     "lacked", "absent", "cannot"}
)  # fmt: skip
#: Honest capture wording: a clause using it says the data is missing, not that nothing happened.
_CAPTURE_WORDS = frozenset(
    {"captured", "capture", "recorded", "record", "logged", "tracked", "data", "available",
     "unavailable", "visible", "evidence", "known", "unknown", "reported", "measured", "seen",
     "observed", "collected", "detected", "dark", "offline"}
)  # fmt: skip
#: A negation reaches this many words ahead, or this many words back ("meals skipped").
_REACH_AHEAD = 3
_REACH_BACK = 2
_CLAUSE_SPLIT = re.compile(r"[.;!?,:()\n]|\b(?:but|and|while|though|although|so|because)\b")


def _is_negation(word: str) -> bool:
    return word in _NEGATION_WORDS or word.endswith("n't")


def _asserts_absence(text: str, uncaptured: Sequence[Mapping[str, Any]]) -> bool:
    """Does any clause negate an uncaptured lane's keyword close by, without capture wording?

    Matching is per clause and by proximity, so "didn't sleep before a work day" does not
    assert absence of work, and "the rest of the day" is not the rest lane. Curly apostrophes
    are folded. A clause that says the data was not captured, recorded or available is honest.
    """
    keywords = {kw for u in uncaptured for kw in _LANE_KEYWORDS.get(u["lane"], ())}
    if not keywords:
        return False
    folded = text.lower().replace("\u2019", "'").replace("\u2018", "'").replace("\u02bc", "'")
    folded = folded.replace("rest of", "remainder of")
    for clause in _CLAUSE_SPLIT.split(folded):
        words = re.findall(r"[a-z']+", clause)
        if not words or _CAPTURE_WORDS & set(words):
            continue
        for index, word in enumerate(words):
            if not _is_negation(word):
                continue
            ahead = words[index + 1 : index + 1 + _REACH_AHEAD]
            back = words[max(0, index - _REACH_BACK) : index]
            if keywords & set(ahead) or keywords & set(back):
                return True
    return False


def _local_day_bounds_utc(local_date: date, tzinfo: ZoneInfo) -> tuple[datetime, datetime]:
    day_start_local = datetime.combine(local_date, time.min, tzinfo)
    day_end_local = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo)
    return day_start_local.astimezone(UTC), day_end_local.astimezone(UTC)


def select_top_episode_titles(
    episodes: Sequence[Mapping[str, Any]],
    *,
    day_start_utc: datetime,
    day_end_utc: datetime,
    limit: int = TOP_EPISODES_PER_LANE,
) -> dict[str, list[str]]:
    """Pick up to *limit* episode titles per lane, longest-duration first.

    ``episodes`` entries carry the same shape ``rollups.compute_daily_lane_rollup``
    reads (``source_name``, ``episode_type``, ``start_at``, ``end_at``,
    ``layer``, optionally ``trigger_source``), plus ``title``. Each episode's
    span is clipped to ``[day_start_utc, day_end_utc)`` identically to the
    rollup computation, so the "top" ranking reflects the same lived-time
    accounting the rollup itself used — never a divergent measure. Episodes
    with no title, or that resolve to no lane, or with no overlap in the
    window, are excluded. Lanes with no titled episodes are omitted from the
    result entirely (never an empty list) so the prompt payload stays small.

    Pure function: no I/O, no LLM, no side effects.
    """
    by_lane: dict[str, list[tuple[float, str]]] = defaultdict(list)

    for ep in episodes:
        title = ep.get("title")
        if not title:
            continue

        ep_start: datetime = ep["start_at"]
        ep_end: datetime | None = ep.get("end_at")
        ep_end_resolved = ep_end if ep_end is not None else day_end_utc

        overlap_start = max(ep_start, day_start_utc)
        overlap_end = min(ep_end_resolved, day_end_utc)
        if overlap_end <= overlap_start:
            continue

        lane = lane_for_activity(
            ep["layer"],
            ep["source_name"],
            ep["episode_type"],
            trigger_source=ep.get("trigger_source"),
        )
        if lane is None:
            continue

        duration = (overlap_end - overlap_start).total_seconds()
        by_lane[lane].append((duration, title))

    return {
        lane: [title for _duration, title in sorted(entries, reverse=True)[:limit]]
        for lane, entries in by_lane.items()
    }


def build_narration_prompt(
    *,
    local_date: date,
    rollup_rows: Sequence[Any],
    flag_rows: Sequence[Any],
    top_episode_titles: Mapping[str, list[str]],
    uncaptured: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Render the reduced-output user message the LLM narrates over.

    Pure function: no I/O, no LLM, no side effects.
    """
    payload = {
        "local_date": local_date.isoformat(),
        "lanes": [
            {
                "lane": r.lane,
                "seconds": r.seconds,
                "episode_count": r.episode_count,
                "top_episode_titles": top_episode_titles.get(r.lane, []),
            }
            for r in rollup_rows
        ],
        "flags": [
            {"flag_type": f.flag_type, "severity": f.severity, "detail": f.detail}
            for f in flag_rows
        ],
        "uncaptured_lanes": [
            {"lane": u["lane"], "state": u["state"], "since": u.get("since")} for u in uncaptured
        ],
    }
    return json.dumps(payload, default=str)


def parse_narration_response(
    response_text: str,
    *,
    known_flag_types: Sequence[str],
    uncaptured: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, dict[str, str]] | None:
    """Parse and validate the LLM's JSON response.

    Returns ``(day_summary, flag_labels)`` on success, or ``None`` if the
    response is not valid JSON, is not an object, or carries no usable
    content. ``flag_labels`` is filtered to only keys present in
    *known_flag_types* and to non-empty string values — an unknown
    ``flag_type`` or an empty label is dropped rather than written, so the
    pass never fabricates a label for a flag that was not actually raised
    today. Pure function: no I/O, no LLM, no side effects.
    """
    text = response_text.strip()
    if text.startswith("```"):
        # Defensive: strip an accidental markdown code fence even though the
        # system prompt forbids it — LLMs sometimes ignore that instruction.
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    known = set(known_flag_types)
    raw_labels = parsed.get("flag_labels")
    flag_labels: dict[str, str] = {}
    if isinstance(raw_labels, dict):
        for flag_type, label in raw_labels.items():
            if flag_type in known and isinstance(label, str) and label.strip():
                flag_labels[flag_type] = label.strip()

    day_summary = parsed.get("day_summary")
    day_summary = day_summary.strip() if isinstance(day_summary, str) else ""

    if not day_summary and not flag_labels:
        return None

    # A summary or label asserting absence in a lane that was never captured is rejected whole.
    if any(_asserts_absence(t, uncaptured) for t in (day_summary, *flag_labels.values())):
        return None

    return day_summary, flag_labels


# ---------------------------------------------------------------------------
# Async orchestrator
# ---------------------------------------------------------------------------


async def _fetch_top_episode_titles(
    pool: asyncpg.Pool,
    *,
    day_start_utc: datetime,
    day_end_utc: datetime,
) -> dict[str, list[str]]:
    privacy_placeholders = ", ".join(f"${i + 3}" for i in range(len(_DEFAULT_PRIVACY_TIERS)))
    rows = await pool.fetch(
        f"""
        SELECT
            source_name,
            episode_type,
            title,
            start_at,
            end_at,
            layer,
            payload->>'trigger_source' AS trigger_source
        FROM v_episodes_corrected
        WHERE start_at < $2
          AND (end_at IS NULL OR end_at > $1)
          AND tombstone_at IS NULL
          AND privacy IN ({privacy_placeholders})
          AND title IS NOT NULL
        """,
        day_start_utc,
        day_end_utc,
        *_DEFAULT_PRIVACY_TIERS,
    )
    episodes = [dict(r) for r in rows]
    return select_top_episode_titles(episodes, day_start_utc=day_start_utc, day_end_utc=day_end_utc)


async def _load_uncaptured(
    pool: asyncpg.Pool, *, local_date: date, timezone: str
) -> list[dict[str, Any]] | None:
    """Uncaptured lanes from the frozen availability snapshot, or ``None`` when unreadable."""
    try:
        async with pool.acquire() as conn:
            availability = await load_day_availability(
                conn, local_date=local_date, timezone=timezone
            )
            if availability is None:
                return None
            retired = await load_day_retired_since(conn, local_date=local_date, timezone=timezone)
    except Exception:
        logger.warning("chronicler narration: availability unreadable for %s", local_date)
        return None
    return uncaptured_lanes(availability, retired_since=retired)


async def narrate_daily_rollup(
    pool: asyncpg.Pool,
    *,
    local_date: date,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Run the bounded once-daily labeling pass for one already-materialized
    local day.

    Must run after ``rollups.materialize_daily_rollups`` and
    ``flags.evaluate_and_write_daily_flags`` have written *local_date*'s
    rows — reads them, never recomputes lane totals or re-derives flags.
    Invokes the LLM **at most once** (a single ``DiscretionDispatcher.call``)
    per invocation, over the reduced rollup/flag rows plus a small capped
    set of episode titles — never raw sensor/point-event rows (spec.md
    "At most one LLM call per local day").

    Returns a plain summary dict suitable as a scheduled-job result payload.
    ``status`` is one of: ``disabled``, ``skipped_no_rollup_data``,
    ``skipped_all_lanes_dark``, ``skipped_availability_unknown``, ``llm_unavailable``,
    ``llm_output_invalid``, ``labeled``.
    """
    if not narration_enabled():
        return {"local_date": local_date.isoformat(), "status": "disabled"}

    try:
        token = await capture_fact_write_token(
            pool,
            day_date=local_date,
            timezone=timezone,
            baseline_lookback_days=28,
        )
    except RepairAdmissionError as exc:
        logger.info("chronicler narration waiting for repair of %s: %s", local_date, exc)
        return {
            "local_date": local_date.isoformat(),
            "status": "waiting_repair",
            "reason": str(exc),
        }

    rollup_rows = await list_daily_rollups(pool, local_date=local_date)
    flag_rows = await list_daily_rollup_flags(pool, local_date=local_date)

    uncaptured = await _load_uncaptured(pool, local_date=local_date, timezone=timezone)
    skip, reason = should_skip_narration(rollup_rows, flag_rows, uncaptured=uncaptured)
    if skip:
        return {"local_date": local_date.isoformat(), "status": f"skipped_{reason}"}

    try:
        tzinfo = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone!r}") from exc
    day_start_utc, day_end_utc = _local_day_bounds_utc(local_date, tzinfo)

    top_episode_titles = await _fetch_top_episode_titles(
        pool, day_start_utc=day_start_utc, day_end_utc=day_end_utc
    )
    user_message = build_narration_prompt(
        local_date=local_date,
        rollup_rows=rollup_rows,
        flag_rows=flag_rows,
        top_episode_titles=top_episode_titles,
        uncaptured=uncaptured or (),
    )

    dispatcher = DiscretionDispatcher(
        pool,
        butler_name=_BUTLER_NAME,
        complexity_tier=_COMPLEXITY_TIER,
        codex_auth_authority=get_current_codex_auth_authority(),
    )
    try:
        response_text = await dispatcher.call(user_message, system_prompt=_SYSTEM_PROMPT)
    except Exception:
        # Do not retain an adapter/provider exception: Codex-dependent direct
        # dispatch may include credential-bearing transport context.
        logger.warning("chronicler narration: LLM call failed safely for local_date=%s", local_date)
        return {"local_date": local_date.isoformat(), "status": "llm_unavailable"}

    parsed = parse_narration_response(
        response_text,
        known_flag_types=[f.flag_type for f in flag_rows],
        uncaptured=uncaptured or (),
    )
    if parsed is None:
        logger.warning("chronicler narration: unusable LLM output for local_date=%s", local_date)
        return {"local_date": local_date.isoformat(), "status": "llm_output_invalid"}

    day_summary, flag_labels = parsed

    day_summary_written = False
    flags_labeled: list[str] = []
    if day_summary or flag_labels:
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await make_fact_write_guard(token)(conn)
                    if day_summary:
                        rows_updated = await set_daily_rollup_day_narrative(
                            conn, local_date=local_date, narrative=day_summary
                        )
                        day_summary_written = rows_updated > 0

                    for flag_type, label in flag_labels.items():
                        updated = await set_daily_rollup_flag_narrative(
                            conn, local_date=local_date, flag_type=flag_type, narrative=label
                        )
                        if updated is not None:
                            flags_labeled.append(flag_type)
        except RepairAdmissionError as exc:
            logger.info(
                "chronicler narration discarded for changed repair state %s: %s", local_date, exc
            )
            return {
                "local_date": local_date.isoformat(),
                "status": "waiting_repair",
                "reason": str(exc),
            }

    return {
        "local_date": local_date.isoformat(),
        "status": "labeled",
        "day_summary_written": day_summary_written,
        "flags_labeled": flags_labeled,
    }


__all__ = [
    "DEFAULT_TIMEZONE",
    "TOP_EPISODES_PER_LANE",
    "build_narration_prompt",
    "narrate_daily_rollup",
    "narration_enabled",
    "parse_narration_response",
    "select_top_episode_titles",
    "should_skip_narration",
]

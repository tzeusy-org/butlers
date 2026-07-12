"""State classification for the dashboard briefing.

classify(state) -> state_class
    Deterministic function mapping dashboard state to one of six classes.

headline_for(state_class, n) -> body string
    Deterministic headline body templated per the design.md D1 table.

time_of_day(hour) -> one of five labels used in the greeting.

Design reference: openspec/changes/dashboard-overview-briefing/design.md D1.
Spec reference: openspec/specs/dashboard-domain-pages/briefing/spec.md (bu-gcz9e.1
    -- headline classified from the composed board/attention model, plus the
    ``degraded`` partial-visibility class).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# State class type alias
# ---------------------------------------------------------------------------

# "degraded" is distinct from "degraded-quiet": degraded-quiet means a real
# butler is known to be unhealthy; degraded means one or more state sources
# could not be read at all, so the *true* state is unknown (bu-gcz9e.1 -- a
# swallowed fetch failure must never compose "quiet").
StateClass = str  # "urgent" | "busy" | "mild" | "degraded-quiet" | "degraded" | "quiet"


# ---------------------------------------------------------------------------
# time_of_day
# ---------------------------------------------------------------------------


def time_of_day(hour: int) -> str:
    """Compute time-of-day bucket from a 0-23 hour value.

    Buckets (from design.md D1):
        late-night   hour < 5
        morning      5 <= hour < 12
        afternoon    12 <= hour < 17
        evening      17 <= hour < 21
        night        21 <= hour <= 23
    """
    if hour < 5:
        return "late-night"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "night"


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def classify(state: dict) -> StateClass:
    """Classify dashboard state into one of six state classes.

    Reads:
        state["attention_items"]: list of dicts with a "severity" key.
            severity "high" drives the urgent class.
        state["butler_statuses"]: list of dicts with a "status" key.
            statuses "degraded" or "error" drive the degraded-quiet class.
        state["degraded_sources"]: list of source names that could not be
            read (board/notifications/approvals/qa/audit fetch failures).
            A non-empty list means the picture below is incomplete.

    Classification priority (top to bottom wins):
        urgent         any attention item with severity == "high"
        busy           3+ attention items, none high
        mild           1-2 attention items, none high
        degraded-quiet 0 attention items, 1+ butler known degraded/error
        degraded       0 attention items, no known-degraded butler, but
                       1+ state source could not be read (partial visibility)
        quiet          0 attention items, all butlers healthy, every source
                       answered

    "degraded" ranks below "degraded-quiet": a known, real signal (a specific
    butler reporting degraded/error) is more actionable than a vague "some
    data is missing" notice, so it takes priority when both are true.
    "degraded" always ranks above "quiet" -- a source that failed to answer
    must never be indistinguishable from a truthful all-clear (bu-gcz9e.1).

    Raises only if the state dict is fundamentally malformed in a way that
    cannot be recovered; callers should catch Exception and fall back to quiet.
    """
    attention_items: list[dict] = state.get("attention_items", [])
    butler_statuses: list[dict] = state.get("butler_statuses", [])
    degraded_sources: list[str] = state.get("degraded_sources", [])

    high_count = sum(1 for item in attention_items if item.get("severity") == "high")
    total = len(attention_items)

    if high_count >= 1:
        return "urgent"

    if total >= 3:
        return "busy"

    if total >= 1:
        return "mild"

    # Zero attention items: inspect butler health.
    degraded_count = sum(1 for b in butler_statuses if b.get("status") in ("degraded", "error"))
    if degraded_count >= 1:
        return "degraded-quiet"

    if degraded_sources:
        return "degraded"

    return "quiet"


# ---------------------------------------------------------------------------
# headline_for
# ---------------------------------------------------------------------------


def headline_for(state_class: StateClass, n: int) -> str:
    """Return the headline body string for a given state class and count.

    The body is the second line rendered by the frontend (first is greet).
    Singular vs plural follows the D1 table verbatim.

    Args:
        state_class: One of the six class values.
        n: The relevant count for the class (high items for urgent,
           total items for busy/mild, degraded butlers for degraded-quiet,
           unreadable sources for degraded). Ignored for quiet.
    """
    if state_class == "urgent":
        if n == 1:
            return "One thing needs you now."
        return f"{n} things need you now."

    if state_class == "busy":
        # busy always uses plural form (n >= 3 by classifier definition)
        return f"Things are busy with {n} items waiting."

    if state_class == "mild":
        if n == 1:
            return "Things are quiet, with 1 exception."
        return f"Things are quiet, with {n} exceptions."

    if state_class == "degraded-quiet":
        if n == 1:
            return "Quiet, but 1 butler is degraded."
        return f"Quiet, but {n} butlers are degraded."

    if state_class == "degraded":
        if n == 1:
            return "One source could not be reached, so this may be incomplete."
        return f"{n} sources could not be reached, so this may be incomplete."

    # quiet (and any unrecognised class)
    return "Everything is in hand."

"""State classification for the dashboard briefing.

classify(state) -> state_class
    Deterministic function mapping dashboard state to one of five classes.

headline_for(state_class, n) -> body string
    Deterministic headline body templated per the design.md D1 table.

time_of_day(hour) -> one of five labels used in the greeting.

Design reference: openspec/changes/dashboard-overview-briefing/design.md D1.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# State class type alias
# ---------------------------------------------------------------------------

StateClass = str  # "urgent" | "busy" | "mild" | "degraded-quiet" | "quiet"


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
    """Classify dashboard state into one of five state classes.

    Reads:
        state["attention_items"]: list of dicts with a "severity" key.
            severity "high" drives the urgent class.
        state["butler_statuses"]: list of dicts with a "status" key.
            statuses "degraded" or "error" drive the degraded-quiet class.

    Classification priority (top to bottom wins):
        urgent         any attention item with severity == "high"
        busy           3+ attention items, none high
        mild           1-2 attention items, none high
        degraded-quiet 0 attention items, 1+ butler degraded/error
        quiet          0 attention items, all butlers healthy

    Raises only if the state dict is fundamentally malformed in a way that
    cannot be recovered; callers should catch Exception and fall back to quiet.
    """
    attention_items: list[dict] = state.get("attention_items", [])
    butler_statuses: list[dict] = state.get("butler_statuses", [])

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

    return "quiet"


# ---------------------------------------------------------------------------
# headline_for
# ---------------------------------------------------------------------------


def headline_for(state_class: StateClass, n: int) -> str:
    """Return the headline body string for a given state class and count.

    The body is the second line rendered by the frontend (first is greet).
    Singular vs plural follows the D1 table verbatim.

    Args:
        state_class: One of the five class values.
        n: The relevant count for the class (high items for urgent,
           total items for busy/mild, degraded butlers for degraded-quiet).
           Ignored for quiet.
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

    # quiet (and any unrecognised class)
    return "Everything is in hand."

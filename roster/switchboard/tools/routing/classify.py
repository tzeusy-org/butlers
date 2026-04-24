"""Message classification utilities — butler capability helpers for routing."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from butlers.tools.switchboard.registry import discover_butlers, list_butlers

logger = logging.getLogger(__name__)
_DEFAULT_ROSTER_DIR = Path(__file__).resolve().parents[3]
_SCHEDULING_INTENT_RE = re.compile(
    r"\b("
    r"schedule(?:d|ing)?|"
    r"reschedule(?:d|ing)?|"
    r"meeting(?:s)?|"
    r"appointment(?:s)?|"
    r"calendar|"
    r"availability|"
    r"free[- ]?busy|"
    r"time ?slot(?:s)?|"
    r"book time|"
    r"set up (?:a )?(?:meeting|call)|"
    r"invite(?:s|d)?"
    r")\b",
    flags=re.IGNORECASE,
)
_FOOD_INTENT_RE = re.compile(
    r"\b("
    r"breakfast|lunch|dinner|supper|brunch|snack(?:s|ed|ing)?|"
    r"eat(?:s|en|ing)?|ate|"
    r"meal(?:s)?|"
    r"cook(?:s|ed|ing)?|"
    r"recipe(?:s)?|"
    r"calorie(?:s)?|carb(?:s)?|protein|fat(?:s)?|fiber|macro(?:s)?|"
    r"diet(?:s|ing|ary)?|"
    r"nutrition(?:al)?|nutrient(?:s)?|"
    r"food(?:s)?|"
    r"vegetarian|vegan|keto|paleo|gluten[- ]?free|"
    r"allerg(?:y|ies|ic)|intoleran(?:t|ce)|"
    r"chicken|beef|pork|fish|salmon|tuna|shrimp|"
    r"rice|pasta|noodle(?:s)?|bread|"
    r"vegetable(?:s)?|fruit(?:s)?|salad|soup|"
    r"vitamin(?:s)?|supplement(?:s)?|"
    r"hungry|hunger|appetite|"
    r"fasting|intermittent"
    r")\b",
    flags=re.IGNORECASE,
)
# Retrospective time-review intents (RFC 0014 §D6).
# Only explicit retrospective asks route to Chronicler. Domain
# next-action questions (e.g. "recommend music") and scheduling intents
# stay with the owning butler / calendar-capable butler.
_RETROSPECTIVE_INTENT_RE = re.compile(
    r"\b("
    r"what did i (?:do|have|eat|watch|listen(?:ed)?(?: to)?|play(?:ed)?) "
    r"(?:yesterday|last (?:night|week|month|weekend)|today)|"
    r"when did i last|"
    r"how (?:much|many hours|long)(?: time)? (?:did|have) i "
    r"(?:spend|spent|work(?:ed)?|listen(?:ed)?|play(?:ed)?|watch(?:ed)?)"
    r"[^\n]{0,60}?\b(?:yesterday|today|last (?:night|week|month|weekend)|this (?:week|month))|"
    r"time (?:i )?spent (?:on|listening|playing|watching|working)|"
    r"recap (?:of )?(?:my )?(?:yesterday|last (?:week|night|month|weekend)|day)|"
    r"what happened (?:yesterday|last (?:week|night|month|weekend))|"
    r"looking back|retrospective"
    r")\b",
    flags=re.IGNORECASE,
)
# Correction intents against a retrospective record (also Chronicler).
_RETROSPECTIVE_CORRECTION_RE = re.compile(
    r"\b("
    r"fix (?:the )?(?:start|end|timestamp|(?:start|end) time|time)"
    r"(?: of)?|"
    r"actually (?:that|the meeting|the session)|"
    r"correct (?:the )?(?:start|end|timestamp|title|(?:start|end) time|time)"
    r")\b",
    flags=re.IGNORECASE,
)


def is_retrospective_time_intent(text: str) -> bool:
    """True iff the text is an explicit retrospective time-review request.

    Used by the Switchboard classifier to prefer Chronicler routing only
    for unambiguously retrospective asks. Passive timestamped events
    (Spotify playback change, Steam game started, OwnTracks point,
    Google Health reading) never hit this helper — they arrive through
    connector ingestion, not user-message classification.
    """
    if _RETROSPECTIVE_INTENT_RE.search(text):
        return True
    if _RETROSPECTIVE_CORRECTION_RE.search(text):
        return True
    return False


def _normalize_modules(raw_modules: Any) -> set[str]:
    """Normalize registry module payloads into a lowercase module-name set."""
    if raw_modules is None:
        return set()

    modules_data = raw_modules
    if isinstance(raw_modules, str):
        candidate = raw_modules.strip()
        if not candidate:
            return set()
        try:
            modules_data = json.loads(candidate)
        except json.JSONDecodeError:
            modules_data = [candidate]

    if isinstance(modules_data, dict):
        items = modules_data.keys()
    elif isinstance(modules_data, (list, tuple, set)):
        items = modules_data
    else:
        return set()

    modules: set[str] = set()
    for item in items:
        if isinstance(item, str):
            name = item.strip().lower()
            if name:
                modules.add(name)
    return modules


def _calendar_capable_butlers(butlers: list[dict[str, Any]]) -> set[str]:
    """Return butler names that advertise calendar capability."""
    capable: set[str] = set()
    for butler in butlers:
        name = str(butler.get("name", "")).strip().lower()
        if not name:
            continue
        if "calendar" in _normalize_modules(butler.get("modules")):
            capable.add(name)
    return capable


def _pick_preferred_calendar_butler(capable_butlers: set[str]) -> str | None:
    """Pick the preferred calendar-capable butler for schedule-centric fallbacks."""
    if not capable_butlers:
        return None
    if "calendar" in capable_butlers:
        return "calendar"
    return sorted(capable_butlers)[0]


def _format_capabilities(butler: dict[str, Any]) -> str:
    """Format module capabilities for prompt context."""
    modules = sorted(_normalize_modules(butler.get("modules")))
    return ", ".join(modules) if modules else "none"


def _is_scheduling_intent(text: str) -> bool:
    """Return True when text appears to describe calendar scheduling intent."""
    return bool(_SCHEDULING_INTENT_RE.search(text))


def _is_food_intent(text: str) -> bool:
    """Return True when text mentions food, meals, or dietary topics."""
    return bool(_FOOD_INTENT_RE.search(text))


def _build_routing_guidance(butlers: list[dict[str, Any]]) -> str:
    """Build routing guidance based on available butlers and their capabilities."""
    butler_names = {str(b["name"]).strip().lower() for b in butlers}
    lines = [
        "Routing guidance:",
        "- Preserve domain ownership for specialist domains.",
        "- Only route to butlers listed under 'Available butlers' above.",
    ]

    if _calendar_capable_butlers(butlers):
        lines.append(
            "- For calendar/scheduling intents, prefer butlers that list calendar capability."
        )

    if "chronicler" in butler_names:
        lines.append(
            "- Route ONLY explicit retrospective time-review requests to the\n"
            "  chronicler butler: 'what did I do yesterday', 'how much time did I\n"
            "  spend listening', 'when did I last go running', 'recap of last week',\n"
            "  or corrections to a past event ('fix the start time of yesterday's\n"
            "  meeting', 'actually that session ended at 4pm').\n"
            "- Domain-next-action questions (e.g. 'recommend me music',\n"
            "  'schedule lunch with Alice') stay with the owning butler, NOT\n"
            "  chronicler.\n"
            "- Passive timestamped events (Spotify now-playing, Steam game started,\n"
            "  OwnTracks points, Google Health readings) NEVER route to chronicler;\n"
            "  they continue to route to their owning domain butler."
        )

    if "lifestyle" in butler_names:
        lines.append(
            "- Food preferences, favorite cuisines, restaurant visits, recipes, and\n"
            "  entertainment (music, TV, books, games, podcasts), hobbies, and daily\n"
            "  routines belong to the lifestyle butler."
        )
        if "health" in butler_names:
            lines.append(
                "- Nutrition tracking (calories, macros, diet goals), health metrics,\n"
                "  and meal logging belong to the health butler, not lifestyle."
            )
    elif "health" in butler_names:
        lines.append(
            "- Food preferences, dietary habits, meal mentions, and anything\n"
            "  related to eating or nutrition belong to the health butler."
        )

    return "\n".join(lines)


async def _load_available_butlers(pool: Any) -> list[dict[str, Any]]:
    """Load butler-typed agents eligible for user-message routing.

    Staffer-typed agents are excluded from the candidate set — they are
    never valid targets for user-message classification.  They remain
    reachable via butler-to-staffer routing paths (e.g., notify → messenger).
    """
    butlers = await list_butlers(pool, routable_only=True, butler_only=True)
    if butlers:
        return butlers

    try:
        await discover_butlers(pool, _DEFAULT_ROSTER_DIR)
        butlers = await list_butlers(pool, routable_only=True, butler_only=True)
    except Exception:
        logger.exception(
            "Failed to auto-discover butlers from %s",
            _DEFAULT_ROSTER_DIR,
        )

    return butlers

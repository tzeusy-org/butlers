"""Safe, pure session presentation at the dashboard API boundary.

Session prompts are audit/transcript material, not display authority. The
Timeline receives a persisted trigger source alongside each prompt, so it can
choose a stable, human-readable label without interpreting machine prose.
Only routed sessions with a complete, allowlisted message fence may expose
bounded prompt text.
"""

from __future__ import annotations

import re
from typing import Final, Literal

_SUMMARY_MAX_LEN = 120
_ROUTE_TRIGGER_SOURCE = "route"

_FENCE_TAG_PATTERN = r"routed_message|user_message"
_FENCE_START_RE = re.compile(
    rf"</?(?P<tag>{_FENCE_TAG_PATTERN})",
)
_FENCE_TOKEN_RE = re.compile(
    rf"<(?P<closing>/)?(?P<tag>{_FENCE_TAG_PATTERN})>",
)

# These exact labels cover the persisted trigger-source vocabulary plus
# compatibility values found in historical rows.  Unknown values fail closed
# to a generic label rather than turning their prompt into display text.
_EXACT_TRIGGER_LABELS = {
    "classification": "Switchboard classification",
    "dashboard": "Dashboard request",
    "deadline": "Deadline task",
    "external": "External request",
    "healing": "Recovery investigation",
    "heartbeat": "Heartbeat",
    "manual": "Manual trigger",
    "qa": "QA investigation",
    "route": "Routed message",
    "schedule": "Scheduled task",
    "tick": "Scheduled tick",
    "trigger": "Manual trigger",
}

_SAFE_TASK_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9:_-]*[A-Za-z0-9])?$")


MachineClass = Literal["owner", "heartbeat", "maintenance"]

# Presentation taxonomy, not scheduler or lifecycle policy. Exact keys keep
# owner-value schedules visible by default and avoid coupling this API boundary
# to module startup internals. `schedule:consolidation` is the runtime session
# emitted by memory consolidation; the remaining keys mirror reviewed module
# schedule names for historical or direct session rows.
_MACHINE_CLASS_BY_TRIGGER_SOURCE: Final[dict[str, MachineClass]] = {
    "tick": "heartbeat",
    "classification": "heartbeat",
    "heartbeat": "heartbeat",
    "schedule:consolidation": "maintenance",
    "schedule:memory_decay_sweep": "maintenance",
    "schedule:memory_consolidation": "maintenance",
    "schedule:memory_episode_cleanup": "maintenance",
    "schedule:memory_purge_superseded": "maintenance",
    "schedule:memory_ann_observability": "maintenance",
    "schedule:memory_consolidation_backfill": "maintenance",
    "schedule:memory_catalog_backfill": "maintenance",
}


def derive_session_machine_class(trigger_source: str | None) -> MachineClass:
    """Classify a session for presentation from exact structured metadata only.

    Unknown and future sources default to owner activity. This deliberately
    leaves the Spawner's exact consolidation episode exclusion untouched.
    """
    if not isinstance(trigger_source, str):
        return "owner"
    return _MACHINE_CLASS_BY_TRIGGER_SOURCE.get(trigger_source, "owner")


def _truncate(text: str) -> str:
    """Collapse whitespace and cap text to a glanceable summary length."""
    collapsed = " ".join(text.split())
    if len(collapsed) > _SUMMARY_MAX_LEN:
        return collapsed[:_SUMMARY_MAX_LEN] + "..."
    return collapsed


def _humanize_task_name(task_name: str) -> str | None:
    """Return a bounded label fragment for a safe structured task name."""
    if not _SAFE_TASK_NAME_RE.fullmatch(task_name):
        return None
    return _truncate(re.sub(r"[-_:]+", " ", task_name).casefold())


def _prefixed_task_label(trigger_source: str, *, prefix: str, fallback: str) -> str:
    """Humanize a validated source suffix, or return its generic safe label."""
    _source, _separator, task_name = trigger_source.partition(":")
    task_label = _humanize_task_name(task_name)
    if task_label is None:
        return fallback
    return f"{prefix}: {task_label}"


def _terminal_route_fence_body(prompt: str) -> str | None:
    """Return one unambiguous, complete terminal routed payload, if present.

    The persisted prompt is a context prefix followed by the routed payload.
    To prevent context from becoming display authority, the complete prompt may
    contain exactly one allowlisted fence pair: a matching opening/closing pair
    whose close is terminal apart from whitespace. Any extra, nested,
    mismatched, tag-like malformed, or partial allowed fence is ambiguous and
    therefore rejected.
    """
    tokens: list[tuple[int, int, bool, str]] = []
    for possible_start in _FENCE_START_RE.finditer(prompt):
        # A doubled opening angle bracket is malformed fence syntax, not a
        # valid payload boundary.
        if possible_start.start() > 0 and prompt[possible_start.start() - 1] == "<":
            return None

        token_end = prompt.find(">", possible_start.end())
        if token_end < 0:
            return None

        token = prompt[possible_start.start() : token_end + 1]
        token_match = _FENCE_TOKEN_RE.fullmatch(token)
        if token_match is None:
            return None
        tokens.append(
            (
                possible_start.start(),
                token_end + 1,
                token_match.group("closing") is not None,
                token_match.group("tag"),
            )
        )

    if len(tokens) != 2:
        return None

    opening, closing = tokens
    if opening[2] or not closing[2] or opening[3] != closing[3]:
        return None
    if prompt[closing[1] :].strip():
        return None

    body = prompt[opening[1] : closing[0]].strip()
    return body or None


def _summary_from_route_fence(prompt: str | None) -> str:
    """Extract only an allowlisted, complete terminal routed-message body."""
    if not isinstance(prompt, str):
        return _EXACT_TRIGGER_LABELS[_ROUTE_TRIGGER_SOURCE]

    body = _terminal_route_fence_body(prompt)
    if body is None:
        return _EXACT_TRIGGER_LABELS[_ROUTE_TRIGGER_SOURCE]
    return _truncate(body)


def _summary_from_trigger_source(trigger_source: str | None) -> str:
    """Return a safe summary without consulting untrusted prompt text."""
    if not isinstance(trigger_source, str):
        return "Activity"

    if trigger_source.startswith("schedule:"):
        return _prefixed_task_label(
            trigger_source,
            prefix="Scheduled",
            fallback=_EXACT_TRIGGER_LABELS["schedule"],
        )
    if trigger_source.startswith("deadline:"):
        return _prefixed_task_label(
            trigger_source,
            prefix="Deadline",
            fallback=_EXACT_TRIGGER_LABELS["deadline"],
        )
    return _EXACT_TRIGGER_LABELS.get(trigger_source, "Activity")


def derive_session_summary(prompt: str | None, *, trigger_source: str | None) -> str:
    """Return a safe, structured-trigger-first session summary for Timeline.

    A routed session is the sole case where prompt text can surface, and even
    then only a complete ``<routed_message>`` or ``<user_message>`` fence is
    accepted.  All other values are derived from the structured source first;
    this prevents system prompts, chat envelopes, and malformed historical
    rows from becoming human-facing event summaries.
    """
    if trigger_source == _ROUTE_TRIGGER_SOURCE:
        return _summary_from_route_fence(prompt)
    return _summary_from_trigger_source(trigger_source)

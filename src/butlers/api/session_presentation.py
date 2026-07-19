"""Safe, pure session presentation at the dashboard API boundary.

Session prompts are audit/transcript material, not display authority. The
Timeline receives a persisted trigger source alongside each prompt, so it can
choose a stable, human-readable label without interpreting machine prose.
Only routed sessions with a complete, allowlisted message fence may expose
bounded prompt text.
"""

from __future__ import annotations

import re

_SUMMARY_MAX_LEN = 120
_ROUTE_TRIGGER_SOURCE = "route"

_FENCE_TAGS = ("routed_message", "user_message")
_FENCE_RE = re.compile(
    r"<(?P<tag>" + "|".join(_FENCE_TAGS) + r")>"
    r"(?P<body>(?:(?!<(?P=tag)>).)*?)"
    r"</(?P=tag)>",
    re.DOTALL,
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

_SAFE_TASK_NAME_RE = re.compile(r"^[A-Za-z0-9]+(?:[A-Za-z0-9:_-]*[A-Za-z0-9])?$")


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


def _summary_from_route_fence(prompt: str | None) -> str:
    """Extract only an allowlisted, complete routed-message body."""
    if not isinstance(prompt, str):
        return _EXACT_TRIGGER_LABELS[_ROUTE_TRIGGER_SOURCE]

    match = _FENCE_RE.search(prompt)
    if match is None:
        return _EXACT_TRIGGER_LABELS[_ROUTE_TRIGGER_SOURCE]

    body = match.group("body").strip()
    if not body:
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

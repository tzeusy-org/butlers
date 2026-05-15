"""QA case severity labels and dossier helper utilities."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Literal

from butlers.core.qa.models import QaFinding
from butlers.core.qa.notes import InvestigationNotes

SeverityLabel = Literal["high", "medium", "low"]
CaseState = Literal["detect", "diagnose", "pr", "landed", "escalated"]

_HUMAN_ACTION_MARKERS = ("human action", "operator", "escalat")
_HUMAN_ACTION_TERMINAL_STATUSES = frozenset({"unfixable", "failed"})


def map_severity(severity: int) -> SeverityLabel:
    """Map the stored 0..4 QA severity integer into a dossier label."""

    if severity in (0, 1):
        return "high"
    if severity == 2:
        return "medium"
    if severity in (3, 4):
        return "low"
    raise ValueError(f"Unknown QA severity: {severity}")


def short_id_from_uuid(value: uuid.UUID) -> str:
    """Return a stable ``#NNN`` ID from a UUIDv7 timestamp.

    UUIDv7 stores its Unix epoch millisecond timestamp in the high 48 bits.
    The dossier short ID uses the lowest three decimal digits of that timestamp
    portion, so it is deterministic for a given attempt UUID while staying
    compact enough for the case rail.
    """

    timestamp_ms = value.int >> 80
    return f"#{timestamp_ms % 1000:03d}"


def state_of_case(attempt: Mapping[str, Any] | object) -> CaseState:
    """Map a healing attempt row or object into the QA dossier state track."""

    status = _get_field(attempt, "status")
    if status == "pr_merged":
        return "landed"
    if status == "unfixable":
        return "escalated"
    if failed_with_human_action(attempt):
        return "escalated"
    if status == "pr_open":
        return "pr"
    if status == "investigating":
        return "diagnose"
    if status == "dispatch_pending":
        return "detect"
    return "detect"


def headline_for_case(attempt: Mapping[str, Any] | object, finding: QaFinding | None) -> str:
    """Choose the best available human headline for a QA case."""

    headline = _investigation_notes_headline(finding)
    if headline:
        return headline

    event_summary = getattr(finding, "event_summary", None) if finding is not None else None
    if event_summary:
        return event_summary

    exception_type = _get_field(attempt, "exception_type") or "UnknownError"
    butler_name = _get_field(attempt, "butler_name") or "unknown"
    return f"{exception_type} in {butler_name}"


def failed_with_human_action(attempt: Mapping[str, Any] | object | None) -> bool:
    """Return whether a terminal attempt carries a human-action marker.

    The shipped ``healing_attempts.error_detail`` column is TEXT, so markers are
    matched case-insensitively as substrings. If the schema later moves to a
    structured JSONB human-action field, replace this with a structured lookup.
    """

    if attempt is None:
        return False
    status = _get_field(attempt, "status")
    if status not in _HUMAN_ACTION_TERMINAL_STATUSES:
        return False
    detail = _get_field(attempt, "error_detail")
    value = getattr(detail, "value", detail)
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(marker in lowered for marker in _HUMAN_ACTION_MARKERS)


def escalated_open_cases_sql(*, qa_only: bool = False) -> str:
    """Return SQL for terminal-but-unresolved human-action QA cases."""

    marker_predicate = _human_action_marker_sql()
    qa_patrol_predicate = "\n  AND qa_patrol_id IS NOT NULL" if qa_only else ""
    return f"""
SELECT COUNT(*)
FROM public.healing_attempts
WHERE status IN ('unfixable', 'failed')
  AND ({marker_predicate})
  AND (closed_at IS NULL OR closed_at >= now() - INTERVAL '7 days'){qa_patrol_predicate}
""".strip()


def _human_action_marker_sql() -> str:
    return "\n       OR ".join(
        f"error_detail ILIKE {_sql_string_literal(f'%{marker}%')}"
        for marker in _HUMAN_ACTION_MARKERS
    )


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _investigation_notes_headline(finding: QaFinding | None) -> str | None:
    if finding is None:
        return None

    structured_evidence = finding.structured_evidence
    if not isinstance(structured_evidence, Mapping):
        return None

    notes = structured_evidence.get("investigation_notes")
    if isinstance(notes, InvestigationNotes):
        return notes.headline or None
    if isinstance(notes, Mapping):
        headline = notes.get("headline")
        if isinstance(headline, str) and headline:
            return headline
    return None


def _get_field(source: Mapping[str, Any] | object, field: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(field)
    return getattr(source, field, None)

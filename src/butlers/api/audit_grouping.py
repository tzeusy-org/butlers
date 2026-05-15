"""Shared audit-error grouping logic for the dashboard.

Both the Issues router and the Briefing router aggregate audit-log errors by
normalized first-line message. This module owns the shared CTE SQL and the
row-to-domain projection helpers so the two consumers stay in sync.

Key normalization rule
----------------------
Temporary-path prefixes like ``/tmp/tmpABC123/`` are collapsed to ``/tmp/.../``
before grouping. Without this step, the same underlying error produces a
distinct group for every ephemeral temp directory, inflating the issues count
and making the Issues page and the Briefing disagree on the number of distinct
problems.

Severity model
--------------
- ``critical`` — error originated from a **scheduled** session
  (trigger_source starts with ``schedule:``)
- ``warning`` — all other errors

Callers apply their own window/LIMIT constraints after the CTE.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from butlers.api.models import Issue

# ---------------------------------------------------------------------------
# SQL building-block
# ---------------------------------------------------------------------------

#: Shared CTE fragment. Paste into a larger query; the outer SELECT operates on
#: the ``normalized_errors`` CTE, then callers add WHERE/LIMIT as needed.
_AUDIT_GROUP_CTE = """
WITH normalized_errors AS (
    SELECT
        butler,
        created_at,
        COALESCE(
            NULLIF(BTRIM(
                REGEXP_REPLACE(
                    SPLIT_PART(error, E'\\n', 1),
                    '/tmp/tmp[a-zA-Z0-9_]+/',
                    '/tmp/.../',
                    'g'
                )
            ), ''),
            'Unknown error'
        ) AS error_summary,
        (
            operation = 'session'
            AND COALESCE(request_summary->>'trigger_source', '') LIKE 'schedule:%'
        ) AS is_schedule,
        NULLIF(
            SPLIT_PART(COALESCE(request_summary->>'trigger_source', ''), ':', 2),
            ''
        ) AS schedule_name
    FROM dashboard_audit_log
    WHERE result = 'error'{where_extra}
),
grouped_errors AS (
    SELECT
        error_summary,
        MIN(created_at) AS first_seen_at,
        MAX(created_at) AS last_seen_at,
        COUNT(*)::int AS occurrences,
        ARRAY_AGG(DISTINCT butler ORDER BY butler) AS butlers,
        BOOL_OR(is_schedule) AS has_schedule,
        ARRAY_REMOVE(
            ARRAY_AGG(DISTINCT schedule_name ORDER BY schedule_name),
            NULL
        ) AS schedule_names
    FROM normalized_errors
    GROUP BY error_summary
    ORDER BY last_seen_at DESC{limit_clause}
)
"""

_GROUPED_SELECT = "SELECT * FROM grouped_errors"


def build_audit_group_query(
    *,
    where_extra: str = "",
    limit: int | None = None,
) -> str:
    """Return a complete SELECT query using the shared audit grouping CTE.

    Args:
        where_extra: Extra SQL appended to the inner WHERE clause after
            ``result = 'error'``.  Must start with a newline + whitespace and
            a SQL keyword, e.g. ``"\\n                  AND created_at >= ..."``.
        limit: If given, adds a LIMIT clause to the grouped CTE.

    Returns:
        A complete SQL string ready to be passed to ``pool.fetch()``.
    """
    limit_clause = f"\n    LIMIT {int(limit)}" if limit is not None else ""
    cte = _AUDIT_GROUP_CTE.format(where_extra=where_extra, limit_clause=limit_clause)
    return cte + _GROUPED_SELECT


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------

_ISSUE_TYPE_MAX_LEN = 80


def _slug(value: str) -> str:
    """Build a short, deterministic slug suitable for issue type keys."""
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not normalized:
        return "unknown"
    return normalized[:_ISSUE_TYPE_MAX_LEN]


def issue_from_audit_group_row(row: object) -> Issue:
    """Map one grouped audit row into an :class:`~butlers.api.models.Issue`.

    This is the authoritative severity model for audit-derived issues:
    - ``critical`` for scheduled-task failures
    - ``warning`` for ad-hoc errors
    """
    error_message = str(row["error_summary"])  # type: ignore[index]
    butlers = [str(b) for b in (row["butlers"] or [])]  # type: ignore[index]
    if not butlers:
        butlers = ["unknown"]

    schedule_names = [str(name) for name in (row["schedule_names"] or [])]  # type: ignore[index]
    has_schedule = bool(row["has_schedule"])  # type: ignore[index]

    if has_schedule:
        severity = "critical"
        issue_type = (
            f"scheduled_task_failure:{_slug(schedule_names[0])}"
            if len(schedule_names) == 1
            else "scheduled_task_failure:multiple"
        )
        if len(schedule_names) == 1 and len(butlers) == 1:
            description = (
                f"Scheduled task '{schedule_names[0]}' failure on '{butlers[0]}': {error_message}"
            )
        elif len(schedule_names) == 1:
            description = (
                f"Scheduled task '{schedule_names[0]}' failures across "
                f"{len(butlers)} butlers: {error_message}"
            )
        elif len(butlers) == 1:
            description = f"Scheduled task failures on '{butlers[0]}': {error_message}"
        else:
            description = f"Scheduled task failures across {len(butlers)} butlers: {error_message}"
    else:
        severity = "warning"
        issue_type = f"audit_error_group:{_slug(error_message)}"
        if len(butlers) == 1:
            description = f"{error_message} ({butlers[0]})"
        else:
            description = f"{error_message} ({len(butlers)} butlers)"

    butler = butlers[0] if len(butlers) == 1 else "multiple"

    link_params: dict[str, str] = {}
    if len(butlers) == 1:
        link_params["butler"] = butlers[0]
    if has_schedule:
        link_params["operation"] = "session"
    link = f"/audit-log?{urlencode(link_params)}" if link_params else "/audit-log"

    return Issue(
        severity=severity,
        type=issue_type,
        butler=butler,
        description=description,
        link=link,
        error_message=error_message,
        occurrences=int(row["occurrences"] or 1),  # type: ignore[index]
        first_seen_at=row["first_seen_at"],  # type: ignore[index]
        last_seen_at=row["last_seen_at"],  # type: ignore[index]
        butlers=butlers,
    )


def attention_item_from_audit_group_row(row: object) -> dict:
    """Map one grouped audit row into a briefing attention-item dict.

    Uses the same severity model as :func:`issue_from_audit_group_row` —
    scheduled-task errors become ``"high"`` (briefing maps ``"critical"`` to
    ``"high"`` for display), ad-hoc errors become ``"medium"``.

    The briefing attention-item shape is intentionally a flat dict (not the
    :class:`~butlers.api.models.Issue` Pydantic model) so the briefing router
    can extend it with ``source`` and ``link`` without coupling the Issue model
    to briefing-specific fields.
    """
    error_summary = str(row["error_summary"])  # type: ignore[index]
    butlers_raw = [str(b) for b in (row["butlers"] or [])]  # type: ignore[index]
    butlers = butlers_raw or ["unknown"]
    has_schedule = bool(row["has_schedule"])  # type: ignore[index]

    # Map to briefing severity scale: critical -> high, warning -> medium.
    severity = "high" if has_schedule else "medium"
    issue_type = "scheduled_task_failure" if has_schedule else "audit_error_group"

    if len(butlers) == 1:
        description = f"{error_summary} ({butlers[0]})"
        butler = butlers[0]
    else:
        description = f"{error_summary} ({len(butlers)} butlers)"
        butler = "multiple"

    first_seen_at = row["first_seen_at"]  # type: ignore[index]
    last_seen_at = row["last_seen_at"]  # type: ignore[index]

    return {
        "severity": severity,
        "type": issue_type,
        "butler": butler,
        "description": description,
        "link": "/audit-log",
        "error_message": error_summary,
        "occurrences": int(row["occurrences"] or 1),  # type: ignore[index]
        "first_seen_at": (
            first_seen_at.isoformat()
            if hasattr(first_seen_at, "isoformat")
            else (str(first_seen_at) if first_seen_at is not None else None)
        ),
        "last_seen_at": (
            last_seen_at.isoformat()
            if hasattr(last_seen_at, "isoformat")
            else (str(last_seen_at) if last_seen_at is not None else None)
        ),
        "butlers": butlers,
        "source": "audit_log",
    }

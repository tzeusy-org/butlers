"""Investigation agent prompt builder for QA-originated investigations.

Composes the prompt from normalized finding context (fingerprint, exception type,
sanitized summary, source type, occurrence count) without including any raw log
content or user data.

Spec reference
--------------
openspec/changes/qa-staffer/specs/qa-investigation-dispatch/spec.md
  §Requirement: QA Investigation Agent Prompt
"""

from __future__ import annotations

import uuid

from butlers.core.qa.models import QaFinding

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_QA_INVESTIGATION_PROMPT_TEMPLATE = """\
You are a QA investigation agent for the butler system. An automated patrol \
cycle has detected a recurring error in the {source_butler} butler and you have \
been spawned to investigate the root cause and propose a fix.

## Error Context

**Fingerprint:** {fingerprint}
**Exception type:** {exception_type}
**Call site:** {call_site}
**Severity:** {severity}
**Sanitized summary:** {event_summary}
**Source butler:** {source_butler}
**Discovery source:** {source_type}
**Occurrences:** {occurrence_count} (first seen: {first_seen}, last seen: {last_seen})

{context_section}{dashboard_section}\
## Your Task

1. Read the relevant source code (your CWD is an isolated worktree branched \
from main).
2. Identify the root cause of this error.
3. If it is a code bug: write a fix with tests, then commit. The dispatcher \
will open a PR automatically (do NOT push yourself).
4. If it is NOT a code bug (external service outage, missing infrastructure, \
bad user data, known limitation): signal this by creating an ``UNFIXABLE`` \
file in the worktree root, then committing it. See the protocol below.

## Signaling an Unfixable Error

When the error cannot be fixed with a code change, create a file named \
``UNFIXABLE`` in the repository root with a plain-text explanation (≤500 words):

- Why this is NOT a code bug
- The actual root cause
- What a human operator should do to resolve it

Then commit it::

    git add UNFIXABLE
    git commit -m "chore: unfixable — <brief reason>"

The dispatcher detects this file after your session ends and transitions the \
attempt to ``unfixable`` status (no PR will be opened).

## Important Rules

- Do NOT create a PR yourself — the dispatcher handles that.
- Do NOT include any PII, user data, credentials, environment-specific \
information, or sensitive context in commit messages, code changes, or the \
UNFIXABLE file.
- Run tests after a code fix: ``uv run pytest`` and \
``uv run ruff check src/ tests/``.
- Stay within the scope of this specific error.
- This investigation was triggered automatically by the QA patrol system, \
not by a live user session — the error context above is all the information \
available.
"""

_CONTEXT_SECTION_TEMPLATE = """\
## Diagnostic Context

The discovery source provided the following diagnostic context for this error.
Use it as a starting point, but verify independently:

{context}

"""

_DASHBOARD_SECTION_TEMPLATE = """\
## Investigation Dashboard

View investigation details at: {dashboard_url}

"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_investigation_prompt(
    finding: QaFinding,
    attempt_id: uuid.UUID,
    dashboard_base_url: str | None = None,
) -> str:
    """Build the investigation prompt for a QA agent session.

    The prompt includes normalized error context from the discovery source.
    No raw log content, user data, or session-specific details are included.

    Parameters
    ----------
    finding:
        The ``QaFinding`` that triggered this investigation.
    attempt_id:
        UUID of the healing_attempts row (used to build the dashboard link).
    dashboard_base_url:
        Optional base URL for the dashboard (e.g. ``"https://dashboard.example.com"``).
        When provided, the prompt includes a link to the investigation detail page.
        When ``None``, the link is omitted (the dashboard may be on a private
        tailnet and the link would leak the hostname to a public PR).

    Returns
    -------
    str
        Formatted investigation prompt string.
    """
    # Build optional context section (diagnostic reasoning from butler_reports source)
    context_section = ""
    if finding.context and finding.context.strip():
        context_section = _CONTEXT_SECTION_TEMPLATE.format(context=finding.context.strip())

    # Build optional dashboard link section
    dashboard_section = ""
    if dashboard_base_url:
        dashboard_url = f"{dashboard_base_url.rstrip('/')}/qa/investigations/{attempt_id}"
        dashboard_section = _DASHBOARD_SECTION_TEMPLATE.format(dashboard_url=dashboard_url)

    return _QA_INVESTIGATION_PROMPT_TEMPLATE.format(
        fingerprint=finding.fingerprint,
        exception_type=finding.exception_type,
        call_site=finding.call_site,
        severity=finding.severity,
        event_summary=finding.event_summary,
        source_butler=finding.source_butler,
        source_type=finding.source_type,
        occurrence_count=finding.occurrence_count,
        first_seen=finding.first_seen.isoformat(),
        last_seen=finding.last_seen.isoformat(),
        context_section=context_section,
        dashboard_section=dashboard_section,
    )

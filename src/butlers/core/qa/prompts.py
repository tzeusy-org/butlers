"""Investigation agent prompt builder for QA-originated investigations.

Composes the prompt from normalized finding context (fingerprint, exception type,
sanitized summary, source type, occurrence count) without including any raw log
content or user data.

Also provides ``build_review_followup_prompt`` for follow-up agents dispatched
when a QA PR receives reviewer feedback (changes requested or unresolved threads).

Spec reference
--------------
openspec/changes/qa-staffer/specs/qa-investigation-dispatch/spec.md
  §Requirement: QA Investigation Agent Prompt
"""

from __future__ import annotations

import uuid
from typing import Any

from butlers.core.qa.models import QaFinding

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

# Runtime note: the shared RuntimeAdapter.invoke contract does not expose a
# structured-output/schema parameter, and Claude is invoked through the same
# prompt/runtime_args path as other runtimes. Keep this as a portable file
# contract until the runtime interface grows a real structured-output hook.
_INVESTIGATION_NOTES_JSON_GUIDANCE = """\
## Writing Structured Investigation Notes (./.qa/investigation_notes.json)

Before terminating, write a JSON file at ``./.qa/investigation_notes.json`` \
inside the repository root matching the ``InvestigationNotes`` schema below. \
Create the ``.qa`` directory if needed. This JSON is separate from \
``INVESTIGATION_NOTES.md`` and is persisted into the internal QA case record.

Fields:

- ``schema_version``: Always the integer ``1``.
- ``headline``: One concise operator-facing title for the case.
- ``hypothesis``: The final technical root-cause hypothesis you accepted.
- ``blurb_segments``: Ordered narrative chunks; use strings for glue text and \
objects with ``claim`` and ``text`` when a sentence should point at a claim.
- ``claims``: Object keyed by claim id; each value has ``evidence_ids`` and a \
short ``note`` explaining what those evidence lines establish.
- ``evidence_lines``: Array of raw internal evidence objects with ``id``, \
``ts``, ``lvl``, ``butler``, and ``msg``.
- ``counter_evidence``: Array of hypotheses you considered, each with \
``hypothesis``, ``verdict`` (``rejected``, ``accepted``, or ``pending``), and \
``reason``.
- ``why_this_fix``: One or two sentences explaining why the committed change \
addresses the accepted hypothesis.
- ``diff_snapshot``: Array of diff line objects with ``kind`` (``meta``, \
``+``, ``-``, or a single space) and ``text``.

Small example:

```json
{{
  "schema_version": 1,
  "headline": "Spotify ingestion failing - scope renamed upstream",
  "hypothesis": "A hard-coded Spotify scope string drifted from the provider contract.",
  "blurb_segments": [
    {{"claim": "c1", "text": "The connector now receives scope_mismatch from Spotify."}},
    " Updating the requested scope and surfacing reauthorization resolves the failure."
  ],
  "claims": {{
    "c1": {{"evidence_ids": ["e1"], "note": "The failing call reached scope validation."}}
  }},
  "evidence_lines": [
    {{
      "id": "e1",
      "ts": "2026-05-14T14:30:11Z",
      "lvl": "ERROR",
      "butler": "chronicler",
      "msg": "spotify.ingest 401 scope_mismatch /me/player/recently-played"
    }}
  ],
  "counter_evidence": [
    {{
      "hypothesis": "Token expiry",
      "verdict": "rejected",
      "reason": "Refresh reached scope validation before token validation."
    }}
  ],
  "why_this_fix": "Renaming the scope and showing reauth clears the connector failure.",
  "diff_snapshot": [
    {{"kind": "meta", "text": "src/butlers/connectors/spotify.py"}},
    {{"kind": "-", "text": "scope=user-read-currently-playing"}},
    {{"kind": "+", "text": "scope=user-read-playback-state"}}
  ]
}}
```

Rules for the JSON file:

- Emit valid JSON only; no Markdown wrapper in ``./.qa/investigation_notes.json``.
- Keep all non-evidence narrative safe for external review: no PII, secrets, \
hostnames, or environment-specific identifiers.
- Include every top-level field, even if an array or object is empty.

"""

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

{evidence_section}{context_section}{dashboard_section}\
## Your Task

1. Read the relevant source code (your CWD is a QA helper directory inside an \
isolated worktree branched from main; the git repository root is nearby).
2. Identify the root cause of this error.
3. If it is a code bug: write a fix with tests, then commit. The dispatcher \
will open a PR automatically (do NOT push yourself).
4. If it is NOT a code bug (external service outage, missing infrastructure, \
bad user data, known limitation): signal this by creating an ``UNFIXABLE`` \
file in the worktree root, then committing it. See the protocol below.
5. After committing your fix, write an investigation notes file so the \
dispatcher can populate the PR description. See the protocol below.

## Writing the PR Description (INVESTIGATION_NOTES.md)

After your fix is committed, write a file named ``INVESTIGATION_NOTES.md`` \
**in your current working directory** (NOT the repository root — your CWD is \
already gitignored, so the file will not be committed). The dispatcher reads \
this file and substitutes its sections into the PR body before opening the PR.

The file MUST follow this exact structure — three H2 headers in this order, \
each followed by plain prose:

```
## Root Cause
<1-3 paragraphs explaining what was actually wrong in the code>

## Fix Summary
<1-3 paragraphs describing what you changed and why it fixes the root cause>

## Test Coverage
<short description of the tests you added or updated, and what they assert>
```

Rules for the notes file:

- Use exactly the three H2 headers above, spelled and capitalized as shown.
- Do NOT include any PII, user data, credentials, hostnames, file paths \
outside the repo, or environment-specific identifiers — the dispatcher \
re-runs anonymization, but your notes should already be safe.
- Keep total length under ~500 words. Prose is fine; bullet lists are fine; \
code fences are fine. Do not include screenshots or attachments.
- If you skip this file, the PR will be opened with placeholder text instead. \
The fix still ships, but reviewers lose the context — please write it.

{investigation_notes_json_guidance}\
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
attempt to ``unfixable`` status (no PR will be opened). You do not need to \
write ``INVESTIGATION_NOTES.md`` in the unfixable case.

## Important Rules

- Do NOT create a PR yourself — the dispatcher handles that.
- Do NOT include any PII, user data, credentials, environment-specific \
information, or sensitive context in commit messages, code changes, the \
UNFIXABLE file, or the INVESTIGATION_NOTES.md file.
- Beads is NOT part of the QA workflow. Do NOT run ``bd`` or follow repo-level \
issue-tracker/session-close procedures that are unrelated to this investigation.
- Run tests after a code fix: ``uv run pytest`` and \
``uv run ruff check src/ tests/``.
- Stay within the scope of this specific error.
- This investigation was triggered automatically by the QA patrol system, \
not by a live user session — the error context above is all the information \
available.
"""

_EVIDENCE_SECTION_TEMPLATE = """\
## Structured Evidence

The QA patrol recorded the following diagnostic identifiers from the discovery \
source.  These do not contain raw log content or user data.  Use them to \
correlate with session logs and metrics:

{evidence_lines}

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
# Internal helpers
# ---------------------------------------------------------------------------


def _format_evidence_lines(evidence: dict[str, Any]) -> str:
    """Format a structured evidence dict into human-readable prompt lines.

    Renders each key-value pair as a bullet point.  Lists (e.g. ``session_ids``)
    are rendered as a comma-separated value.  ``None`` values are omitted.

    This function does NOT apply ``_escape()`` — evidence dict values come from
    trusted internal sources (session IDs, log file stems, log levels) that do
    not contain ``{`` or ``}`` characters, so escaping is unnecessary and would
    introduce double-braces in the rendered prompt.
    """
    lines = []
    for key, value in evidence.items():
        if value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
            rendered = ", ".join(str(v) for v in value)
        else:
            rendered = str(value)
        lines.append(f"- **{key}**: {rendered}")
    return "\n".join(lines)


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

    def _escape(s: str) -> str:
        """Escape curly braces in user-controlled strings to prevent str.format() errors.

        event_summary, context, call_site, etc. may come from log messages or
        error text that contains ``{`` / ``}`` (e.g. JSON, Python format strings,
        stack traces).  These must be escaped before being interpolated into the
        prompt template via str.format() or they raise KeyError / ValueError.
        """
        return s.replace("{", "{{").replace("}", "}}")

    # Build optional structured evidence section
    evidence_section = ""
    if finding.structured_evidence:
        evidence_lines = _format_evidence_lines(finding.structured_evidence)
        if evidence_lines:
            evidence_section = _EVIDENCE_SECTION_TEMPLATE.format(
                evidence_lines=evidence_lines,
            )

    # Build optional context section (diagnostic reasoning from butler_reports source)
    context_section = ""
    if finding.context and finding.context.strip():
        context_section = _CONTEXT_SECTION_TEMPLATE.format(context=_escape(finding.context.strip()))

    # Build optional dashboard link section
    dashboard_section = ""
    if dashboard_base_url:
        dashboard_url = f"{dashboard_base_url.rstrip('/')}/qa/investigations/{attempt_id}"
        dashboard_section = _DASHBOARD_SECTION_TEMPLATE.format(dashboard_url=dashboard_url)

    return _QA_INVESTIGATION_PROMPT_TEMPLATE.format(
        fingerprint=finding.fingerprint,
        exception_type=_escape(finding.exception_type),
        call_site=_escape(finding.call_site),
        severity=finding.severity,
        event_summary=_escape(finding.event_summary),
        source_butler=_escape(finding.source_butler),
        source_type=_escape(finding.source_type),
        occurrence_count=finding.occurrence_count,
        first_seen=finding.first_seen.isoformat(),
        last_seen=finding.last_seen.isoformat(),
        evidence_section=evidence_section,
        context_section=context_section,
        dashboard_section=dashboard_section,
        investigation_notes_json_guidance=_INVESTIGATION_NOTES_JSON_GUIDANCE,
    )


# ---------------------------------------------------------------------------
# PR review follow-up prompt
# ---------------------------------------------------------------------------

_PR_REVIEW_FOLLOWUP_PROMPT_TEMPLATE = """\
You are a QA review follow-up agent. A QA investigation PR for the \
{source_butler} butler has received reviewer feedback that must be addressed \
before merging.

## PR Context

**PR Number:** {pr_number}
**PR URL:** {pr_url}
**Fingerprint:** {fingerprint}
**Attempt ID:** {attempt_id}

## Reviewer Feedback

{feedback_summary}

## Your Task

1. Fetch the latest PR state and full review thread details::

       gh pr view {pr_number} --json reviews,reviewThreads,files

2. Read each reviewer comment carefully.
3. Address all outstanding review comments by making the requested code changes.
4. Run tests and lint after making changes::

       uv run pytest
       uv run ruff check src/ tests/

5. Commit your changes with a clear message referencing the reviewer feedback::

       git commit -m "fix(qa-review): address reviewer feedback [<fingerprint[:12]>]"

6. Do NOT push or create a new PR — the QA dispatcher will handle that.

## Important Rules

- Respond to reviewer feedback accurately and completely.
- Do NOT include any PII, user data, credentials, or environment-specific \
information in commit messages or code changes.
- Keep changes focused on the specific reviewer feedback — do not refactor \
unrelated code.
- If a reviewer request is unclear, make a conservative interpretation that \
satisfies the spirit of the feedback.
- This follow-up was triggered automatically by the QA review tracker; \
the feedback summary above is the primary context available.
{dashboard_section}"""

_PR_REVIEW_DASHBOARD_SECTION_TEMPLATE = """\

## Investigation Dashboard

View investigation details at: {dashboard_url}
"""


def build_review_followup_prompt(
    pr_number: int,
    pr_url: str,
    fingerprint: str,
    source_butler: str,
    attempt_id: uuid.UUID,
    feedback_summary: str,
    dashboard_base_url: str | None = None,
) -> str:
    """Build the follow-up prompt for a PR review response agent.

    Called when ``check_open_pr_statuses`` detects unresolved review threads
    or "changes requested" state on a QA investigation PR.

    Parameters
    ----------
    pr_number:
        GitHub PR number.
    pr_url:
        Full GitHub PR URL.
    fingerprint:
        Fingerprint from the original healing attempt.
    source_butler:
        Butler that originated the investigation.
    attempt_id:
        UUID of the healing_attempts row.
    feedback_summary:
        Concise summary of the outstanding reviewer feedback (already anonymized
        by the caller).
    dashboard_base_url:
        Optional base URL for the dashboard investigation detail page.

    Returns
    -------
    str
        Formatted follow-up prompt string.
    """

    def _escape(s: str) -> str:
        return s.replace("{", "{{").replace("}", "}}")

    dashboard_section = ""
    if dashboard_base_url:
        dashboard_url = f"{dashboard_base_url.rstrip('/')}/qa/investigations/{attempt_id}"
        dashboard_section = _PR_REVIEW_DASHBOARD_SECTION_TEMPLATE.format(
            dashboard_url=dashboard_url
        )

    return _PR_REVIEW_FOLLOWUP_PROMPT_TEMPLATE.format(
        source_butler=_escape(source_butler),
        pr_number=pr_number,
        pr_url=_escape(pr_url),
        fingerprint=fingerprint,
        attempt_id=attempt_id,
        feedback_summary=_escape(feedback_summary),
        dashboard_section=dashboard_section,
    )

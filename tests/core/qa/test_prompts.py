"""Tests for butlers.core.qa.prompts prompt builder.

Covers:
- build_investigation_prompt: all required fields present
- build_investigation_prompt: context section included when finding.context is set
- build_investigation_prompt: context section omitted when finding.context is None/empty
- build_investigation_prompt: dashboard link included when dashboard_base_url provided
- build_investigation_prompt: dashboard link omitted when dashboard_base_url is None
- build_investigation_prompt: dashboard URL includes attempt_id
- build_investigation_prompt: UNFIXABLE protocol documented
- build_investigation_prompt: no PR creation instruction
- build_investigation_prompt: PII exclusion instruction present
- build_investigation_prompt: first_seen and last_seen as ISO strings
- build_investigation_prompt: source_butler in prompt
- build_investigation_prompt: source_type in prompt
- build_investigation_prompt: occurrence_count in prompt
- build_investigation_prompt: severity in prompt
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from butlers.core.qa.models import QaFinding
from butlers.core.qa.prompts import build_investigation_prompt

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(
    fingerprint: str = "abcdef" * 10 + "abcd",  # 64 chars
    exception_type: str = "KeyError",
    call_site: str = "finance.api.router:128",
    severity: int = 1,
    event_summary: str = "Missing key in response dict",
    source_butler: str = "finance",
    source_type: str = "log_scanner",
    occurrence_count: int = 7,
    context: str | None = None,
) -> QaFinding:
    now = datetime.now(UTC)
    return QaFinding(
        fingerprint=fingerprint,
        source_type=source_type,
        source_butler=source_butler,
        severity=severity,
        exception_type=exception_type,
        event_summary=event_summary,
        call_site=call_site,
        occurrence_count=occurrence_count,
        first_seen=now,
        last_seen=now,
        timestamp=now,
        context=context,
    )


# ---------------------------------------------------------------------------
# Required fields tests
# ---------------------------------------------------------------------------


def test_prompt_includes_fingerprint():
    """Prompt contains the full fingerprint."""
    fp = "deadbeef" * 8  # 64 chars
    finding = _make_finding(fingerprint=fp)
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert fp in prompt


def test_prompt_includes_exception_type():
    """Prompt contains the exception type."""
    finding = _make_finding(exception_type="AttributeError")
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "AttributeError" in prompt


def test_prompt_includes_call_site():
    """Prompt contains the call site."""
    finding = _make_finding(call_site="travel.jobs:99")
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "travel.jobs:99" in prompt


def test_prompt_includes_severity():
    """Prompt contains the severity value."""
    finding = _make_finding(severity=0)
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "0" in prompt


def test_prompt_includes_event_summary():
    """Prompt contains the sanitized event summary."""
    finding = _make_finding(event_summary="Unexpected null in pipeline stage 3")
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "Unexpected null in pipeline stage 3" in prompt


def test_prompt_includes_source_butler():
    """Prompt contains the source butler name."""
    finding = _make_finding(source_butler="travel")
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "travel" in prompt


def test_prompt_includes_source_type():
    """Prompt contains the discovery source type."""
    finding = _make_finding(source_type="butler_reports")
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "butler_reports" in prompt


def test_prompt_includes_occurrence_count():
    """Prompt contains the occurrence count."""
    finding = _make_finding(occurrence_count=42)
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "42" in prompt


def test_prompt_includes_first_seen_iso():
    """Prompt contains first_seen as ISO string."""
    finding = _make_finding()
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert finding.first_seen.isoformat() in prompt


def test_prompt_includes_last_seen_iso():
    """Prompt contains last_seen as ISO string."""
    finding = _make_finding()
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert finding.last_seen.isoformat() in prompt


# ---------------------------------------------------------------------------
# Context section tests
# ---------------------------------------------------------------------------


def test_prompt_context_section_included_when_set():
    """Diagnostic context section is included when finding.context is non-empty."""
    ctx = "Root cause likely in the pagination logic around line 42."
    finding = _make_finding(context=ctx)
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "Diagnostic Context" in prompt
    assert ctx in prompt


def test_prompt_context_section_omitted_when_none():
    """Diagnostic context section is omitted when finding.context is None."""
    finding = _make_finding(context=None)
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "Diagnostic Context" not in prompt


def test_prompt_context_section_omitted_when_empty_string():
    """Diagnostic context section is omitted when finding.context is empty string."""
    finding = _make_finding(context="")
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "Diagnostic Context" not in prompt


def test_prompt_context_section_omitted_when_whitespace_only():
    """Diagnostic context section is omitted when finding.context is whitespace only."""
    finding = _make_finding(context="   \n  ")
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "Diagnostic Context" not in prompt


# ---------------------------------------------------------------------------
# Dashboard section tests
# ---------------------------------------------------------------------------


def test_prompt_dashboard_section_included_when_url_provided():
    """Investigation dashboard section is included when dashboard_base_url is set."""
    finding = _make_finding()
    attempt_id = uuid.uuid4()
    prompt = build_investigation_prompt(
        finding, attempt_id, dashboard_base_url="https://dash.example.com"
    )
    assert "Investigation Dashboard" in prompt
    assert str(attempt_id) in prompt


def test_prompt_dashboard_url_includes_attempt_id():
    """Dashboard URL in prompt contains the attempt_id."""
    finding = _make_finding()
    attempt_id = uuid.uuid4()
    prompt = build_investigation_prompt(
        finding, attempt_id, dashboard_base_url="https://dash.example.com"
    )
    expected_url = f"https://dash.example.com/qa/investigations/{attempt_id}"
    assert expected_url in prompt


def test_prompt_dashboard_url_strips_trailing_slash():
    """dashboard_base_url trailing slash is stripped in the URL."""
    finding = _make_finding()
    attempt_id = uuid.uuid4()
    prompt = build_investigation_prompt(
        finding, attempt_id, dashboard_base_url="https://dash.example.com/"
    )
    expected_url = f"https://dash.example.com/qa/investigations/{attempt_id}"
    assert expected_url in prompt


def test_prompt_dashboard_section_omitted_when_none():
    """Dashboard section is omitted when dashboard_base_url is None."""
    finding = _make_finding()
    prompt = build_investigation_prompt(finding, uuid.uuid4(), dashboard_base_url=None)
    assert "Investigation Dashboard" not in prompt


# ---------------------------------------------------------------------------
# Safety and protocol tests
# ---------------------------------------------------------------------------


def test_prompt_no_pr_creation_instruction():
    """Prompt instructs agent NOT to create a PR."""
    finding = _make_finding()
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    # Should say "do NOT push" or "do NOT create a PR"
    prompt_lower = prompt.lower()
    assert "do not" in prompt_lower or "not push" in prompt_lower


def test_prompt_unfixable_protocol_present():
    """Prompt documents the UNFIXABLE file protocol."""
    finding = _make_finding()
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "UNFIXABLE" in prompt


def test_prompt_no_pii_instruction():
    """Prompt includes instruction about not including PII."""
    finding = _make_finding()
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    prompt_lower = prompt.lower()
    assert "pii" in prompt_lower or "user data" in prompt_lower or "sensitive" in prompt_lower

"""Tests for butlers.core.qa.prompts prompt builder — condensed.

Covers:
- build_investigation_prompt: required fields (fingerprint, exception, call site,
  severity, summary, source_butler, source_type, occurrence_count, ISO timestamps)
- Context section: included with content, omitted when None/empty/whitespace
- Dashboard section: included with attempt_id URL when base_url given; omitted when None;
  trailing slash stripped
- Safety/protocol: UNFIXABLE documented, no PR instruction, PII exclusion present
- Braces in dynamic fields do not raise
- build_review_followup_prompt: required fields, feedback included, PII rules present,
  braces in feedback do not raise, dashboard section optional
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from butlers.core.qa.models import QaFinding
from butlers.core.qa.prompts import build_investigation_prompt, build_review_followup_prompt

pytestmark = pytest.mark.unit


def _make_finding(**kwargs) -> QaFinding:
    now = datetime.now(UTC)
    defaults = dict(
        fingerprint="deadbeef" * 8,
        source_type="log_scanner",
        source_butler="finance",
        severity=1,
        exception_type="KeyError",
        event_summary="Missing key in response dict",
        call_site="finance.api.router:128",
        occurrence_count=7,
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )
    defaults.update(kwargs)
    return QaFinding(**defaults)


def test_prompt_required_fields():
    """All required fields appear in the prompt output."""
    fp = "deadbeef" * 8
    finding = _make_finding(
        fingerprint=fp,
        exception_type="AttributeError",
        call_site="travel.jobs:99",
        severity=0,
        event_summary="Unexpected null in pipeline",
        source_butler="travel",
        source_type="butler_reports",
        occurrence_count=42,
    )
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert fp in prompt
    assert "AttributeError" in prompt
    assert "travel.jobs:99" in prompt
    assert "0" in prompt
    assert "Unexpected null in pipeline" in prompt
    assert "travel" in prompt
    assert "butler_reports" in prompt
    assert "42" in prompt
    assert finding.first_seen.isoformat() in prompt
    assert finding.last_seen.isoformat() in prompt


@pytest.mark.parametrize(
    "context,expected_present",
    [
        ("Root cause likely in the pagination logic.", True),
        (None, False),
        ("", False),
        ("   \n  ", False),
    ],
)
def test_prompt_context_section(context, expected_present):
    """Diagnostic context section included when non-empty, omitted otherwise."""
    finding = _make_finding(context=context)
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    if expected_present:
        assert "Diagnostic Context" in prompt
        assert context in prompt
    else:
        assert "Diagnostic Context" not in prompt


def test_prompt_dashboard_section():
    """Dashboard section: included with attempt_id URL when base_url given; trailing slash stripped; omitted when None."""
    finding = _make_finding()
    attempt_id = uuid.uuid4()

    # With base URL (no trailing slash)
    prompt = build_investigation_prompt(
        finding, attempt_id, dashboard_base_url="https://dash.example.com"
    )
    assert "Investigation Dashboard" in prompt
    expected_url = f"https://dash.example.com/qa/investigations/{attempt_id}"
    assert expected_url in prompt

    # With trailing slash — stripped
    prompt2 = build_investigation_prompt(
        finding, attempt_id, dashboard_base_url="https://dash.example.com/"
    )
    assert expected_url in prompt2

    # Without base URL
    prompt3 = build_investigation_prompt(finding, uuid.uuid4(), dashboard_base_url=None)
    assert "Investigation Dashboard" not in prompt3


def test_prompt_safety_and_braces():
    """UNFIXABLE protocol present; no-PR instruction; PII exclusion; braces in fields do not raise."""
    prompt = build_investigation_prompt(_make_finding(), uuid.uuid4())
    assert "UNFIXABLE" in prompt
    assert "do not" in prompt.lower() or "not push" in prompt.lower()
    assert "do not run ``bd``" in prompt.lower()
    assert "pii" in prompt.lower() or "user data" in prompt.lower() or "sensitive" in prompt.lower()

    # Braces in dynamic fields
    curly_finding = _make_finding(
        event_summary='{"key": "value", "error": "unexpected token {"}',
        call_site="module.{dynamic}:42",
        exception_type="ValueError({msg})",
    )
    p = build_investigation_prompt(curly_finding, uuid.uuid4())
    assert "key" in p and "dynamic" in p

    # Braces in context
    ctx = 'Root cause in JSON: {"field": "value {placeholder}"}'
    p2 = build_investigation_prompt(_make_finding(context=ctx), uuid.uuid4())
    assert "Diagnostic Context" in p2 and "field" in p2


# ---------------------------------------------------------------------------
# build_review_followup_prompt tests
# ---------------------------------------------------------------------------


def _make_followup_prompt(**kwargs) -> str:
    defaults = dict(
        pr_number=42,
        pr_url="https://github.com/org/repo/pull/42",
        fingerprint="deadbeef" * 8,
        source_butler="finance",
        attempt_id=uuid.uuid4(),
        feedback_summary="Please fix the test coverage.",
    )
    defaults.update(kwargs)
    return build_review_followup_prompt(**defaults)


def test_review_followup_prompt_required_fields():
    """All required context fields appear in the follow-up prompt."""
    attempt_id = uuid.uuid4()
    fp = "deadbeef" * 8
    prompt = build_review_followup_prompt(
        pr_number=99,
        pr_url="https://github.com/org/repo/pull/99",
        fingerprint=fp,
        source_butler="travel",
        attempt_id=attempt_id,
        feedback_summary="Reviewer says: fix the edge case.",
    )
    assert "99" in prompt
    assert fp in prompt
    assert "travel" in prompt
    assert str(attempt_id) in prompt
    assert "fix the edge case" in prompt


def test_review_followup_prompt_no_dashboard():
    """No dashboard URL → section absent."""
    prompt = _make_followup_prompt(dashboard_base_url=None)
    assert "Investigation Dashboard" not in prompt


def test_review_followup_prompt_with_dashboard():
    """Dashboard URL → section with correct link present."""
    attempt_id = uuid.uuid4()
    prompt = build_review_followup_prompt(
        pr_number=42,
        pr_url="https://github.com/org/repo/pull/42",
        fingerprint="a" * 64,
        source_butler="general",
        attempt_id=attempt_id,
        feedback_summary="Add tests.",
        dashboard_base_url="https://dash.example.com",
    )
    assert "Investigation Dashboard" in prompt
    expected_url = f"https://dash.example.com/qa/investigations/{attempt_id}"
    assert expected_url in prompt


def test_review_followup_prompt_dashboard_trailing_slash():
    """Trailing slash in base URL is stripped correctly."""
    attempt_id = uuid.uuid4()
    prompt = build_review_followup_prompt(
        pr_number=42,
        pr_url="https://github.com/org/repo/pull/42",
        fingerprint="a" * 64,
        source_butler="general",
        attempt_id=attempt_id,
        feedback_summary="Comments.",
        dashboard_base_url="https://dash.example.com/",
    )
    expected_url = f"https://dash.example.com/qa/investigations/{attempt_id}"
    assert expected_url in prompt
    assert "//qa" not in prompt  # no double slash


def test_review_followup_prompt_pii_rules():
    """Prompt includes PII exclusion instructions."""
    prompt = _make_followup_prompt()
    lower = prompt.lower()
    assert "pii" in lower or "user data" in lower or "credentials" in lower


def test_review_followup_prompt_braces_in_feedback():
    """Curly braces in feedback_summary do not raise."""
    curly_feedback = 'Reviewer says: fix {"key": "value {placeholder}"}.'
    prompt = _make_followup_prompt(feedback_summary=curly_feedback)
    assert "key" in prompt
    assert "placeholder" in prompt


def test_review_followup_prompt_no_pr_instruction():
    """Prompt instructs NOT to create a new PR."""
    prompt = _make_followup_prompt()
    lower = prompt.lower()
    assert "not push" in lower or "do not" in lower or "don't" in lower


# ---------------------------------------------------------------------------
# Structured evidence section tests
# ---------------------------------------------------------------------------


def test_prompt_evidence_section_present_when_structured_evidence_set():
    """## Structured Evidence section appears when finding has structured_evidence."""
    finding = _make_finding(
        structured_evidence={
            "source": "session_records",
            "status": "error",
            "session_ids": ["abc-123", "def-456"],
        }
    )
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "Structured Evidence" in prompt
    assert "session_ids" in prompt
    assert "abc-123" in prompt
    assert "session_records" in prompt


def test_prompt_evidence_section_absent_when_no_structured_evidence():
    """## Structured Evidence section is absent when structured_evidence is None."""
    finding = _make_finding(structured_evidence=None)
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "Structured Evidence" not in prompt


def test_prompt_evidence_section_absent_when_empty_structured_evidence():
    """## Structured Evidence section is absent when structured_evidence is an empty dict."""
    finding = _make_finding(structured_evidence={})
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "Structured Evidence" not in prompt


def test_prompt_evidence_section_omits_none_values():
    """None values in structured_evidence dict are not rendered in the prompt."""
    finding = _make_finding(
        structured_evidence={
            "source": "log_scanner",
            "log_file": "finance.log",
            "level": "error",
            "trigger_source": None,
        }
    )
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "Structured Evidence" in prompt
    assert "log_scanner" in prompt
    assert "finance.log" in prompt
    # None value for trigger_source should not appear as "None" in the prompt
    assert "trigger_source" not in prompt


def test_prompt_evidence_section_renders_list_values():
    """List values in structured_evidence are rendered as comma-separated strings."""
    session_ids = ["id-1", "id-2", "id-3"]
    finding = _make_finding(
        structured_evidence={
            "source": "session_records",
            "session_ids": session_ids,
        }
    )
    prompt = build_investigation_prompt(finding, uuid.uuid4())
    assert "id-1" in prompt
    assert "id-2" in prompt
    assert "id-3" in prompt

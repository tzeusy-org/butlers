"""Tests for butlers.core.qa.prompts prompt builder — condensed.

Covers:
- build_investigation_prompt: required fields (fingerprint, exception, call site,
  severity, summary, source_butler, source_type, occurrence_count, ISO timestamps)
- Context section: included with content, omitted when None/empty/whitespace
- Dashboard section: included with attempt_id URL when base_url given; omitted when None;
  trailing slash stripped
- Safety/protocol: UNFIXABLE documented, no PR instruction, PII exclusion present
- Braces in dynamic fields do not raise
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from butlers.core.qa.models import QaFinding
from butlers.core.qa.prompts import build_investigation_prompt

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
        fingerprint=fp, exception_type="AttributeError", call_site="travel.jobs:99",
        severity=0, event_summary="Unexpected null in pipeline",
        source_butler="travel", source_type="butler_reports", occurrence_count=42,
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


@pytest.mark.parametrize("context,expected_present", [
    ("Root cause likely in the pagination logic.", True),
    (None, False),
    ("", False),
    ("   \n  ", False),
])
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
    prompt = build_investigation_prompt(finding, attempt_id, dashboard_base_url="https://dash.example.com")
    assert "Investigation Dashboard" in prompt
    expected_url = f"https://dash.example.com/qa/investigations/{attempt_id}"
    assert expected_url in prompt

    # With trailing slash — stripped
    prompt2 = build_investigation_prompt(finding, attempt_id, dashboard_base_url="https://dash.example.com/")
    assert expected_url in prompt2

    # Without base URL
    prompt3 = build_investigation_prompt(finding, uuid.uuid4(), dashboard_base_url=None)
    assert "Investigation Dashboard" not in prompt3


def test_prompt_safety_and_braces():
    """UNFIXABLE protocol present; no-PR instruction; PII exclusion; braces in fields do not raise."""
    prompt = build_investigation_prompt(_make_finding(), uuid.uuid4())
    assert "UNFIXABLE" in prompt
    assert "do not" in prompt.lower() or "not push" in prompt.lower()
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

"""Tests for ``_build_dashboard_confirm_block`` — the propose-then-act split.

[bu-0ynlk.1] The dashboard confirm-loop block must never instruct a routed
butler to apply an ACTION REQUEST's write before the approval gate parks it
(``about/heart-and-soul/security.md:103-105`` — consent precedes effect).
Covers:
- Distinct STATEMENT and ACTION-REQUEST instruction sets are both rendered.
- The ACTION-REQUEST set forbids applying/writing before the gate parks the
  call, and requires the gated tool path instead of an ungated one.
- A dedicated failure-mode sentence names the act-before-propose violation.
- conversation_id / page_context / conversation_reply plumbing (regression
  anchor for the pre-existing single-lane behavior) still holds.
"""

from __future__ import annotations

import pytest

from butlers.core_tools._switchboard import _build_dashboard_confirm_block

pytestmark = pytest.mark.unit


def test_renders_distinct_statement_and_action_sections():
    block = _build_dashboard_confirm_block(conversation_id="conv-1", page_context=None)

    assert "STATEMENT" in block
    assert "ACTION REQUEST" in block
    statement_idx = block.index("STATEMENT")
    action_idx = block.index("ACTION REQUEST")
    assert statement_idx != action_idx


def test_action_section_forbids_applying_before_the_gate_parks():
    block = _build_dashboard_confirm_block(conversation_id="conv-1", page_context=None)

    action_section = block[block.index("ACTION REQUEST") :]
    assert "Do NOT write directly" in action_section
    assert "approval-gated tool" in action_section
    assert "the gate parks it" in action_section
    assert "ungated" in action_section


def test_action_section_forbids_claiming_completion():
    block = _build_dashboard_confirm_block(conversation_id="conv-1", page_context=None)

    action_section = block[block.index("ACTION REQUEST") :]
    assert "proposed and awaiting the owner's approval" in action_section
    assert "Never phrase this as something you already did" in action_section


def test_failure_mode_sentence_is_present():
    block = _build_dashboard_confirm_block(conversation_id="conv-1", page_context=None)

    assert "FAILURE MODE" in block
    failure_sentence = block[block.index("FAILURE MODE") :]
    assert "before the approval gate parks it" in failure_sentence
    assert "never acceptable" in failure_sentence


def test_statement_section_keeps_apply_then_confirm():
    block = _build_dashboard_confirm_block(conversation_id="conv-1", page_context=None)

    statement_header = block.index("STATEMENT (the owner is asserting")
    action_header = block.index("ACTION REQUEST (the owner is asking")
    statement_section = block[statement_header:action_header]
    assert "Apply it" in statement_section
    assert "conversation_reply" in statement_section


def test_carries_conversation_id_and_page_context():
    block = _build_dashboard_confirm_block(
        conversation_id="conv-42",
        page_context={"route": "/entities/concentration"},
    )

    assert "conv-42" in block
    assert "/entities/concentration" in block
    assert "conversation_reply" in block


def test_omits_page_context_line_when_absent():
    block = _build_dashboard_confirm_block(conversation_id="conv-1", page_context=None)

    assert "page_context:" not in block


def test_requires_conversation_reply_before_finishing():
    block = _build_dashboard_confirm_block(conversation_id="conv-1", page_context=None)

    assert "You MUST call `conversation_reply` before finishing this session" in block

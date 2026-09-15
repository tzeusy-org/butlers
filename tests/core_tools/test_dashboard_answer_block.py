"""Tests for ``_build_dashboard_answer_block`` — the read-only answer-lane
instruction block injected by ``answer_question`` (scope="domain") (bu-0ynlk.2).

Covers:
- The block states the turn is read-only and forbids writes.
- It instructs a grounded answer to cite sources via conversation_reply.
- It instructs an honest decline (no fabricated sources) when no grounded
  answer can be found.
- conversation_id / page_context / question text plumbing.
"""

from __future__ import annotations

import pytest

from butlers.core_tools._switchboard import _build_dashboard_answer_block

pytestmark = pytest.mark.unit


def test_states_read_only_and_forbids_writes():
    block = _build_dashboard_answer_block(
        conversation_id="conv-1", page_context=None, question="What is my budget?"
    )

    assert "READ-ONLY" in block
    assert "MUST NOT write" in block


def test_instructs_citing_sources_for_a_grounded_answer():
    block = _build_dashboard_answer_block(
        conversation_id="conv-1", page_context=None, question="What is my budget?"
    )

    assert "conversation_reply" in block
    assert "sources" in block
    assert "non-empty" in block


def test_instructs_honest_decline_without_fabricating_sources():
    block = _build_dashboard_answer_block(
        conversation_id="conv-1", page_context=None, question="What is my budget?"
    )

    assert "do NOT fabricate" in block
    assert "omit `sources` entirely" in block


def test_carries_conversation_id_page_context_and_question():
    block = _build_dashboard_answer_block(
        conversation_id="conv-42",
        page_context={"route": "/entities/concentration"},
        question="How much did I spend on groceries?",
    )

    assert "conv-42" in block
    assert "/entities/concentration" in block
    assert "How much did I spend on groceries?" in block


def test_omits_page_context_line_when_absent():
    block = _build_dashboard_answer_block(
        conversation_id="conv-1", page_context=None, question="hi?"
    )

    assert "page_context:" not in block


def test_requires_conversation_reply_before_finishing():
    block = _build_dashboard_answer_block(
        conversation_id="conv-1", page_context=None, question="hi?"
    )

    assert "You MUST call `conversation_reply` before finishing this session" in block

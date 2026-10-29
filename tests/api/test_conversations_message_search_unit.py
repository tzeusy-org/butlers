"""Unit tests for message-search helpers in ``butlers.api.conversations``.

Covers the pure function backing ``message_search`` (bu-0ynlk.9):
``_parse_headline`` (ts_headline marker stripping -> highlight ranges), without
needing a database.
"""

from __future__ import annotations

import pytest

from butlers.api.conversations import _HL_START, _HL_STOP, _parse_headline

pytestmark = pytest.mark.unit


def test_parse_headline_single_match():
    headline = f"call the {_HL_START}landlord{_HL_STOP} today"
    snippet, ranges = _parse_headline(headline)

    assert snippet == "call the landlord today"
    assert ranges == [[9, 17]]
    assert snippet[9:17] == "landlord"


def test_parse_headline_multiple_matches():
    headline = f"{_HL_START}landlord{_HL_STOP} said the {_HL_START}landlord{_HL_STOP} called"
    snippet, ranges = _parse_headline(headline)

    assert snippet == "landlord said the landlord called"
    assert ranges == [[0, 8], [18, 26]]
    for start, end in ranges:
        assert snippet[start:end] == "landlord"


def test_parse_headline_no_match():
    snippet, ranges = _parse_headline("no markers here")

    assert snippet == "no markers here"
    assert ranges == []


def test_parse_headline_unterminated_marker_degrades_to_plain_text():
    headline = f"call the {_HL_START}landlord today"
    snippet, ranges = _parse_headline(headline)

    assert snippet == "call the landlord today"
    assert ranges == []

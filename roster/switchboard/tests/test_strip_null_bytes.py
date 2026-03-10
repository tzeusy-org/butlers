"""Unit tests for _strip_null_bytes sanitization."""

from __future__ import annotations

from butlers.tools.switchboard.ingestion.ingest import _strip_null_bytes


def test_strips_null_from_string():
    assert _strip_null_bytes("hello\x00world") == "helloworld"


def test_strips_multiple_nulls():
    assert _strip_null_bytes("\x00a\x00b\x00") == "ab"


def test_strips_null_from_dict_values():
    result = _strip_null_bytes({"key": "val\x00ue", "nested": {"inner": "a\x00b"}})
    assert result == {"key": "value", "nested": {"inner": "ab"}}


def test_strips_null_from_list():
    result = _strip_null_bytes(["a\x00b", "c\x00d"])
    assert result == ["ab", "cd"]


def test_strips_null_from_tuple():
    result = _strip_null_bytes(("a\x00b", "c\x00d"))
    assert result == ("ab", "cd")


def test_preserves_non_string_types():
    assert _strip_null_bytes(42) == 42
    assert _strip_null_bytes(None) is None
    assert _strip_null_bytes(True) is True
    assert _strip_null_bytes(3.14) == 3.14


def test_clean_string_unchanged():
    assert _strip_null_bytes("hello world") == "hello world"


def test_empty_string():
    assert _strip_null_bytes("") == ""


def test_unicode_null_escape():
    """Test the exact pattern PostgreSQL rejects: \\u0000."""
    assert _strip_null_bytes("before\u0000after") == "beforeafter"

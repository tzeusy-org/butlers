"""Unit tests for switchboard registry API module normalization.

Regression suite for butlers-992: the Modules column was rendering module
names as character-spaced strings when the JSONB column stored a JSON string
value instead of a JSON array.  asyncpg decodes JSONB strings as Python str
objects; calling list() on a str iterates its characters.

These tests exercise _normalize_jsonb_string_list directly — no DB required.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load the router module dynamically (mirrors how it is loaded at runtime).
_router_path = Path(__file__).parent.parent / "api" / "router.py"
_spec = importlib.util.spec_from_file_location("switchboard_api_router_test", _router_path)
assert _spec is not None and _spec.loader is not None
_router_mod = importlib.util.module_from_spec(_spec)
sys.modules["switchboard_api_router_test"] = _router_mod
_spec.loader.exec_module(_router_mod)

_normalize = _router_mod._normalize_jsonb_string_list  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Happy path: well-formed list (asyncpg normal decode)
# ---------------------------------------------------------------------------


def test_normalize_list_of_strings() -> None:
    """Plain list of strings passes through unchanged."""
    assert _normalize(["telegram", "email"]) == ["telegram", "email"]


def test_normalize_empty_list() -> None:
    assert _normalize([]) == []


def test_normalize_none() -> None:
    assert _normalize(None) == []


# ---------------------------------------------------------------------------
# String variants (edge cases that trigger the char-splitting regression)
# ---------------------------------------------------------------------------


def test_normalize_json_serialized_array_string() -> None:
    """JSON-serialized array string is parsed to intact module tokens.

    This is the payload shape produced by double-serialization or by a JSONB
    column storing a JSON string value (e.g. '"[\\\"telegram\\\",\\\"email\\\"]"'::jsonb).
    asyncpg decodes it to the Python str '["telegram","email"]'.
    _normalize must NOT call list() on this string (char-splitting).
    """
    result = _normalize('["telegram","email"]')
    assert result == ["telegram", "email"], (
        f"Expected ['telegram','email'] but got {result!r} — possible char-splitting regression"
    )


def test_normalize_plain_comma_string() -> None:
    """Comma-separated string is split on commas."""
    result = _normalize("telegram, email")
    assert result == ["telegram", "email"]


def test_normalize_single_module_string() -> None:
    """Single module name as plain string must not be char-split."""
    result = _normalize("telegram")
    assert result == ["telegram"], (
        f"Expected ['telegram'] but got {result!r} — possible char-splitting regression"
    )


def test_normalize_empty_string() -> None:
    assert _normalize("") == []


def test_normalize_whitespace_string() -> None:
    assert _normalize("   ") == []


# ---------------------------------------------------------------------------
# Regression: list("telegram") would produce 8 single-char items
# ---------------------------------------------------------------------------


def test_normalize_never_returns_single_char_items_for_module_name() -> None:
    """No normalization path should produce single-character module tokens.

    This is the observable symptom of the char-splitting regression: when
    asyncpg returns a Python str for a JSONB string value and the caller does
    list(str), each character becomes a separate list item.
    """
    for raw in ("telegram", '["telegram"]', '["telegram","email"]'):
        result = _normalize(raw)
        single_chars = [tok for tok in result if len(tok) == 1]
        assert single_chars == [], (
            f"_normalize({raw!r}) produced single-char tokens {single_chars!r}; "
            "char-splitting regression detected"
        )


# ---------------------------------------------------------------------------
# Non-string, non-list inputs
# ---------------------------------------------------------------------------


def test_normalize_integer_returns_empty() -> None:
    assert _normalize(42) == []  # type: ignore[arg-type]


def test_normalize_dict_returns_empty() -> None:
    assert _normalize({"a": 1}) == []  # type: ignore[arg-type]

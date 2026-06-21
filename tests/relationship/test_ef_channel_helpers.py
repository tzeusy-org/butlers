"""Unit tests for roster/relationship/tools/_ef_channel_helpers.py.

Covers the encode/decode helpers introduced in bead bu-wni4z to fix the
telegram has-handle encoding mismatch between write and read paths.
"""

from __future__ import annotations

import pytest

from butlers.tools.relationship._ef_channel_helpers import (
    ef_object_to_display_value,
    ef_predicate_to_ci_type,
    encode_handle_object,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# encode_handle_object — write-side encoding (telegram types get 'telegram:'
# prefix, idempotently; everything else passes through verbatim).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ci_type,raw,expected",
    [
        ("telegram_user_id", "86807245", "telegram:86807245"),
        ("telegram_username", "alice_tg", "telegram:alice_tg"),
        ("telegram", "210454304", "telegram:210454304"),
        # Idempotent: already-prefixed values are not double-prefixed.
        ("telegram_user_id", "telegram:86807245", "telegram:86807245"),
        ("telegram_username", "telegram:alice_tg", "telegram:alice_tg"),
        # Non-telegram channels pass through verbatim.
        ("linkedin", "alice-smith", "alice-smith"),
        ("twitter", "@alice", "@alice"),
        ("other", "somehandle", "somehandle"),
        ("email", "user@example.com", "user@example.com"),
        ("phone", "+15550100", "+15550100"),
        # Empty value still gets the prefix for telegram; passthrough otherwise.
        ("telegram_user_id", "", "telegram:"),
        ("linkedin", "", ""),
    ],
)
def test_encode_handle_object(ci_type: str, raw: str, expected: str) -> None:
    assert encode_handle_object(ci_type, raw) == expected


# ---------------------------------------------------------------------------
# ef_predicate_to_ci_type — read-side classification.
#
# bu-wni4z: prefixed telegram → telegram_user_id; verbatim/legacy and every
# other non-telegram has-handle (linkedin/twitter) → 'handle'; has-email/phone/
# website map to their channel. The linkedin/twitter→'handle' branch is the sole
# guard that they are NOT misclassified as telegram.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "predicate,obj,expected",
    [
        ("has-handle", "telegram:86807245", "telegram_user_id"),
        ("has-handle", "telegram:alice_tg", "telegram_user_id"),
        # Verbatim (legacy, pre-rel_019) numeric is classified as plain handle.
        ("has-handle", "86807245", "handle"),
        ("has-handle", "alice-smith", "handle"),  # linkedin
        ("has-handle", "@alice", "handle"),  # twitter
        ("has-email", "user@example.com", "email"),
        ("has-phone", "+15550100", "phone"),
        ("has-website", "https://example.com", "website"),
    ],
)
def test_ef_predicate_to_ci_type(predicate: str, obj: str, expected: str) -> None:
    assert ef_predicate_to_ci_type(predicate, obj) == expected


# ---------------------------------------------------------------------------
# ef_object_to_display_value — strips the 'telegram:' prefix on read.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "predicate,obj,expected",
    [
        ("has-handle", "telegram:86807245", "86807245"),
        ("has-handle", "telegram:alice_tg", "alice_tg"),
        ("has-handle", "alice-smith", "alice-smith"),  # non-telegram passthrough
        ("has-email", "user@example.com", "user@example.com"),
        ("has-phone", "+15550100", "+15550100"),
    ],
)
def test_ef_object_to_display_value(predicate: str, obj: str, expected: str) -> None:
    assert ef_object_to_display_value(predicate, obj) == expected


# ---------------------------------------------------------------------------
# Round-trip: encode then classify/strip must recover the original type+value.
# Guards the bu-wni4z write/read mismatch end to end, including the
# linkedin/twitter→'handle' read-side classification.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ci_type,raw,expected_type,expected_display",
    [
        ("telegram_user_id", "86807245", "telegram_user_id", "86807245"),
        ("telegram_username", "alice_tg", "telegram_user_id", "alice_tg"),
        ("telegram", "210454304", "telegram_user_id", "210454304"),
        ("linkedin", "alice-smith", "handle", "alice-smith"),
        ("twitter", "@alice", "handle", "@alice"),
    ],
)
def test_encode_decode_roundtrip(
    ci_type: str, raw: str, expected_type: str, expected_display: str
) -> None:
    encoded = encode_handle_object(ci_type, raw)
    assert ef_predicate_to_ci_type("has-handle", encoded) == expected_type
    assert ef_object_to_display_value("has-handle", encoded) == expected_display

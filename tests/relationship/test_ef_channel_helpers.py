"""Unit tests for roster/relationship/tools/_ef_channel_helpers.py.

Covers the encode/decode helpers introduced in bead bu-wni4z to fix the
telegram has-handle encoding mismatch between write and read paths.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# encode_handle_object — write-side encoding
# ---------------------------------------------------------------------------


class TestEncodeHandleObject:
    """encode_handle_object adds 'telegram:' prefix for telegram types only."""

    def setup_method(self):
        from butlers.tools.relationship._ef_channel_helpers import encode_handle_object

        self.encode = encode_handle_object

    def test_telegram_user_id_gets_prefix(self):
        assert self.encode("telegram_user_id", "86807245") == "telegram:86807245"

    def test_telegram_username_gets_prefix(self):
        assert self.encode("telegram_username", "alice_tg") == "telegram:alice_tg"

    def test_telegram_type_gets_prefix(self):
        assert self.encode("telegram", "210454304") == "telegram:210454304"

    def test_already_prefixed_is_idempotent(self):
        """If the value already starts with 'telegram:', it must not be double-prefixed."""
        assert self.encode("telegram_user_id", "telegram:86807245") == "telegram:86807245"

    def test_telegram_username_already_prefixed_idempotent(self):
        assert self.encode("telegram_username", "telegram:alice_tg") == "telegram:alice_tg"

    def test_linkedin_not_prefixed(self):
        assert self.encode("linkedin", "alice-smith") == "alice-smith"

    def test_twitter_not_prefixed(self):
        assert self.encode("twitter", "@alice") == "@alice"

    def test_other_not_prefixed(self):
        assert self.encode("other", "somehandle") == "somehandle"

    def test_email_passthrough(self):
        assert self.encode("email", "user@example.com") == "user@example.com"

    def test_phone_passthrough(self):
        assert self.encode("phone", "+15550100") == "+15550100"

    def test_empty_string_telegram(self):
        """Empty value gets the prefix (the caller should guard against empty values)."""
        assert self.encode("telegram_user_id", "") == "telegram:"

    def test_empty_string_linkedin(self):
        assert self.encode("linkedin", "") == ""


# ---------------------------------------------------------------------------
# ef_predicate_to_ci_type — read-side classification
# ---------------------------------------------------------------------------


class TestEfPredicateToCiType:
    """ef_predicate_to_ci_type correctly classifies prefixed and non-prefixed has-handle rows."""

    def setup_method(self):
        from butlers.tools.relationship._ef_channel_helpers import ef_predicate_to_ci_type

        self.classify = ef_predicate_to_ci_type

    def test_prefixed_telegram_classified_as_telegram_user_id(self):
        assert self.classify("has-handle", "telegram:86807245") == "telegram_user_id"

    def test_prefixed_telegram_username_classified_as_telegram_user_id(self):
        assert self.classify("has-handle", "telegram:alice_tg") == "telegram_user_id"

    def test_verbatim_numeric_classified_as_handle(self):
        """Verbatim (legacy) rows are classified as 'handle', not 'telegram_user_id'.

        Once data-migration (rel_019) runs, all telegram rows will carry the prefix
        and this case will not occur for new or migrated data.
        """
        assert self.classify("has-handle", "86807245") == "handle"

    def test_linkedin_classified_as_handle(self):
        assert self.classify("has-handle", "alice-smith") == "handle"

    def test_twitter_classified_as_handle(self):
        assert self.classify("has-handle", "@alice") == "handle"

    def test_has_email_classified_as_email(self):
        assert self.classify("has-email", "user@example.com") == "email"

    def test_has_phone_classified_as_phone(self):
        assert self.classify("has-phone", "+15550100") == "phone"

    def test_has_website_classified_as_website(self):
        assert self.classify("has-website", "https://example.com") == "website"


# ---------------------------------------------------------------------------
# ef_object_to_display_value — prefix stripping
# ---------------------------------------------------------------------------


class TestEfObjectToDisplayValue:
    """ef_object_to_display_value strips the 'telegram:' prefix on read."""

    def setup_method(self):
        from butlers.tools.relationship._ef_channel_helpers import ef_object_to_display_value

        self.strip = ef_object_to_display_value

    def test_prefixed_telegram_stripped(self):
        assert self.strip("has-handle", "telegram:86807245") == "86807245"

    def test_prefixed_telegram_username_stripped(self):
        assert self.strip("has-handle", "telegram:alice_tg") == "alice_tg"

    def test_non_telegram_has_handle_passthrough(self):
        assert self.strip("has-handle", "alice-smith") == "alice-smith"

    def test_has_email_passthrough(self):
        assert self.strip("has-email", "user@example.com") == "user@example.com"

    def test_has_phone_passthrough(self):
        assert self.strip("has-phone", "+15550100") == "+15550100"


# ---------------------------------------------------------------------------
# Round-trip: encode then classify/strip
# ---------------------------------------------------------------------------


class TestEncodeDecodeRoundTrip:
    """Write→read round-trip: encode_handle_object then ef_predicate_to_ci_type / ef_object_to_display_value."""

    def setup_method(self):
        from butlers.tools.relationship._ef_channel_helpers import (
            ef_object_to_display_value,
            ef_predicate_to_ci_type,
            encode_handle_object,
        )

        self.encode = encode_handle_object
        self.classify = ef_predicate_to_ci_type
        self.display = ef_object_to_display_value

    def _roundtrip(self, ci_type: str, raw_value: str):
        """Encode, then classify and strip — must recover the original type and value."""
        encoded = self.encode(ci_type, raw_value)
        ci_type_out = self.classify("has-handle", encoded)
        display_val = self.display("has-handle", encoded)
        return ci_type_out, display_val

    def test_telegram_user_id_roundtrip(self):
        ci_type_out, display_val = self._roundtrip("telegram_user_id", "86807245")
        assert ci_type_out == "telegram_user_id"
        assert display_val == "86807245"

    def test_telegram_username_roundtrip(self):
        ci_type_out, display_val = self._roundtrip("telegram_username", "alice_tg")
        assert ci_type_out == "telegram_user_id"  # both classify as telegram_user_id
        assert display_val == "alice_tg"

    def test_telegram_type_roundtrip(self):
        ci_type_out, display_val = self._roundtrip("telegram", "210454304")
        assert ci_type_out == "telegram_user_id"
        assert display_val == "210454304"

    def test_linkedin_not_classified_as_telegram(self):
        ci_type_out, display_val = self._roundtrip("linkedin", "alice-smith")
        assert ci_type_out == "handle"
        assert display_val == "alice-smith"  # no stripping

    def test_twitter_not_classified_as_telegram(self):
        ci_type_out, display_val = self._roundtrip("twitter", "@alice")
        assert ci_type_out == "handle"
        assert display_val == "@alice"

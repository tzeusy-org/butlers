"""Content-Type charset resolution for Gmail MIME parts.

The connector decodes each part's bytes with the charset declared in that
part's ``Content-Type`` header. RFC 2045 permits the parameter value to be a
quoted string, and non-conformant senders emit single-quoted values too, so
every one of those forms must resolve to the declared codec rather than
silently falling back to UTF-8 and producing mojibake.

[bu-4swcp]
"""

from __future__ import annotations

import codecs
from typing import Any
from unittest.mock import MagicMock

import pytest

from butlers.connectors.gmail import GmailConnectorConfig, GmailConnectorRuntime


@pytest.fixture
def gmail_runtime() -> GmailConnectorRuntime:
    config = GmailConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        connector_provider="gmail",
        connector_channel="email",
        connector_endpoint_identity="gmail:user:test@example.com",
        connector_max_inflight=4,
        gmail_client_id="test-client-id",
        gmail_client_secret="test-client-secret",
        gmail_refresh_token="test-refresh-token",
        gmail_watch_renew_interval_s=3600,
        gmail_poll_interval_s=5,
    )
    return GmailConnectorRuntime(config, cursor_pool=MagicMock())


def _headers(*content_types: str) -> list[dict[str, str]]:
    """Part headers carrying *content_types*, with unrelated headers around them.

    The gateway-added ``X-Original-Content-Type`` is a decoy: it declares a
    charset of its own, so any resolution that stops filtering on the header
    name picks it up and every expectation below shifts to UTF-16.
    """
    headers: list[dict[str, str]] = [
        {"name": "Subject", "value": "Test"},
        {"name": "X-Original-Content-Type", "value": "text/plain; charset=utf-16"},
    ]
    headers.extend({"name": "Content-Type", "value": value} for value in content_types)
    headers.append({"name": "Content-Transfer-Encoding", "value": "base64"})
    return headers


def _resolved(*content_types: str) -> str:
    """Charset name the connector reads out of these parts' headers."""
    return GmailConnectorRuntime._charset_from_headers(_headers(*content_types))


def _codec(*content_types: str) -> str:
    """Canonical name of the codec the connector would decode these parts with.

    Comparing canonical codec names rather than the raw header token keeps the
    assertions about decoding behaviour, which is what the charset is for, and
    keeps them insensitive to alias spelling and case.
    """
    return codecs.lookup(_resolved(*content_types)).name


def _payload(content_type: str) -> dict[str, Any]:
    return {"headers": _headers(content_type)}


def test_unquoted_charset_resolves() -> None:
    assert _codec("text/plain; charset=iso-8859-1") == codecs.lookup("iso-8859-1").name


def test_double_quoted_charset_resolves() -> None:
    assert _codec('text/plain; charset="iso-8859-1"') == codecs.lookup("iso-8859-1").name


def test_single_quoted_charset_resolves() -> None:
    assert _codec("text/plain; charset='windows-1252'") == codecs.lookup("windows-1252").name


def test_whitespace_around_equals_is_tolerated() -> None:
    assert _codec("text/plain; charset = iso-8859-1") == codecs.lookup("iso-8859-1").name


def test_parameter_name_is_case_insensitive() -> None:
    assert _codec("text/plain; CHARSET=ISO-8859-1") == codecs.lookup("iso-8859-1").name


def test_trailing_semicolon_is_not_part_of_the_value() -> None:
    assert _codec("text/plain; charset=iso-8859-1;") == codecs.lookup("iso-8859-1").name


def test_quoted_charset_alongside_other_parameters_resolves() -> None:
    assert (
        _codec('text/plain; charset="iso-8859-1"; format=flowed')
        == codecs.lookup("iso-8859-1").name
    )


def test_mismatched_quotes_still_recover_the_declared_codec() -> None:
    # The value is malformed, but the sender's intent is legible and the
    # alternative — decoding legacy bytes as UTF-8 with replacement — destroys
    # the text irrecoverably. ``codecs.lookup`` bounds the leniency: only a
    # token that names a real encoding is accepted.
    assert _resolved("text/plain; charset=\"latin-1'") == "latin-1"


def test_charset_inside_another_parameter_value_is_not_used() -> None:
    # ``charset=`` here is part of the quoted filename, not a Content-Type
    # parameter; substring matching would wrongly decode the part as latin-1.
    assert _codec('text/plain; name="charset=iso-8859-1"') == codecs.lookup("utf-8").name


def test_charsetless_content_type_does_not_shadow_a_later_declaration() -> None:
    assert (
        _codec("text/plain", "text/plain; charset=iso-8859-1") == codecs.lookup("iso-8859-1").name
    )


def test_empty_quoted_charset_does_not_shadow_a_later_declaration() -> None:
    assert (
        _codec('text/plain; charset=""', "text/plain; charset=iso-8859-1")
        == codecs.lookup("iso-8859-1").name
    )


def test_single_quoted_charset_resolves_to_an_unquoted_name() -> None:
    # ``codecs.lookup`` tolerates the stray quotes, but the returned name is
    # this function's contract and is logged and passed on, so it must be the
    # charset itself rather than the raw header token.
    assert _resolved("text/plain; charset='windows-1252'") == "windows-1252"


def test_whitespace_inside_a_quoted_value_is_not_part_of_the_name() -> None:
    assert _resolved('text/plain; charset="  iso-8859-1  "') == "iso-8859-1"


def test_absent_charset_falls_back_to_utf8() -> None:
    assert _codec("text/plain") == codecs.lookup("utf-8").name


def test_unknown_quoted_charset_falls_back_to_utf8() -> None:
    assert _codec('text/plain; charset="definitely-not-a-codec"') == codecs.lookup("utf-8").name


def test_double_quoted_charset_decodes_legacy_bytes_without_mojibake(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    raw = "Café — naïve".encode("iso-8859-1", errors="replace")
    decoded = gmail_runtime._decode_part_bytes(raw, _payload('text/plain; charset="iso-8859-1"'))
    assert decoded == "Café ? naïve"


def test_single_quoted_charset_decodes_legacy_bytes_without_mojibake(
    gmail_runtime: GmailConnectorRuntime,
) -> None:
    raw = b"\x93smart quotes\x94"
    decoded = gmail_runtime._decode_part_bytes(raw, _payload("text/plain; charset='windows-1252'"))
    assert decoded == "“smart quotes”"

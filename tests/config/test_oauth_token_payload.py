"""Contract tests for the shared OAuth token-payload validator.

Every value here is synthetic and generated in-test; none of it is or
resembles real credential material.
"""

from __future__ import annotations

from typing import Any

import pytest

from butlers.oauth_token_payload import (
    DEFAULT_EXPIRES_IN_S,
    MAX_EXPIRES_IN_S,
    OAuthTokenValidationError,
    validate_oauth_token_payload,
)

SYNTHETIC_ACCESS_TOKEN = "synthetic-access-token-not-real"
SYNTHETIC_REFRESH_TOKEN = "synthetic-refresh-token-not-real"


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "access_token": SYNTHETIC_ACCESS_TOKEN,
        "refresh_token": SYNTHETIC_REFRESH_TOKEN,
        "expires_in": 3600,
        "scope": "scope-a scope-b",
        "token_type": "Bearer",
    }
    payload.update(overrides)
    return payload


def test_valid_payload_is_accepted_and_normalized() -> None:
    token = validate_oauth_token_payload(_valid_payload())

    assert token.access_token == SYNTHETIC_ACCESS_TOKEN
    assert token.refresh_token == SYNTHETIC_REFRESH_TOKEN
    assert token.expires_in == 3600
    assert token.scope == "scope-a scope-b"


def test_surrounding_whitespace_is_stripped_from_tokens() -> None:
    token = validate_oauth_token_payload(
        _valid_payload(
            access_token=f"  {SYNTHETIC_ACCESS_TOKEN}\n",
            refresh_token=f"\t{SYNTHETIC_REFRESH_TOKEN}  ",
        )
    )

    assert token.access_token == SYNTHETIC_ACCESS_TOKEN
    assert token.refresh_token == SYNTHETIC_REFRESH_TOKEN


def test_absent_optional_fields_become_none() -> None:
    token = validate_oauth_token_payload({"access_token": SYNTHETIC_ACCESS_TOKEN})

    assert token.refresh_token is None
    assert token.scope is None
    assert token.expires_in == DEFAULT_EXPIRES_IN_S


def test_expires_in_upper_bound_is_inclusive() -> None:
    token = validate_oauth_token_payload(
        _valid_payload(expires_in=MAX_EXPIRES_IN_S),
    )

    assert token.expires_in == MAX_EXPIRES_IN_S


@pytest.mark.parametrize(
    ("case", "payload"),
    [
        ("not_a_mapping", ["access_token"]),
        ("payload_is_none", None),
        ("missing_access_token", {"expires_in": 3600}),
        ("null_access_token", _valid_payload(access_token=None)),
        ("empty_access_token", _valid_payload(access_token="")),
        ("whitespace_access_token", _valid_payload(access_token="   ")),
        ("non_string_access_token", _valid_payload(access_token=12345)),
        ("bool_access_token", _valid_payload(access_token=True)),
        ("empty_refresh_token", _valid_payload(refresh_token="")),
        ("whitespace_refresh_token", _valid_payload(refresh_token=" \t ")),
        ("non_string_refresh_token", _valid_payload(refresh_token=12345)),
        ("null_refresh_token", _valid_payload(refresh_token=None)),
        ("non_string_scope", _valid_payload(scope=7)),
        ("empty_scope", _valid_payload(scope="")),
        ("string_expires_in", _valid_payload(expires_in="3600")),
        ("bool_expires_in", _valid_payload(expires_in=True)),
        ("float_expires_in", _valid_payload(expires_in=3600.0)),
        ("null_expires_in", _valid_payload(expires_in=None)),
        ("zero_expires_in", _valid_payload(expires_in=0)),
        ("negative_expires_in", _valid_payload(expires_in=-3600)),
        ("absurd_expires_in", _valid_payload(expires_in=MAX_EXPIRES_IN_S + 1)),
    ],
)
def test_malformed_payloads_are_rejected(case: str, payload: Any) -> None:
    with pytest.raises(OAuthTokenValidationError):
        validate_oauth_token_payload(payload)


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (
            _valid_payload(access_token={"nested": "<script>alert(1)</script>"}),
            "OAuth token response has an invalid access_token.",
        ),
        (
            _valid_payload(expires_in="<script>alert(1)</script>"),
            "OAuth token response has an invalid expires_in.",
        ),
        (
            _valid_payload(refresh_token=["<script>alert(1)</script>"]),
            "OAuth token response has an invalid refresh_token.",
        ),
    ],
)
def test_rejection_message_never_carries_provider_supplied_content(
    payload: dict[str, Any], expected_message: str
) -> None:
    with pytest.raises(OAuthTokenValidationError) as exc_info:
        validate_oauth_token_payload(payload)

    assert str(exc_info.value) == expected_message
    assert "<script>" not in str(exc_info.value)

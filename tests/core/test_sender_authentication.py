"""Sender-authentication verdict (bu-s11n0s.20): the shared verifier and its ingest helper.

Synthetic domains only. The verdict is deterministic from one ``Authentication-Results``
value; the account-security sensor's provider view is pinned unchanged alongside it.
"""

from __future__ import annotations

import pytest

from butlers.account_security import (
    VERIFICATION_AUTHENTICATED,
    VERIFICATION_FAILED,
    VERIFICATION_UNVERIFIED,
    classify_account_security,
    verify_sender_domain,
)
from butlers.core.account_security_events import sender_authentication_verdict

pytestmark = pytest.mark.unit

DOMAIN = "vendor.example"
_ALIGNED_PASS = "mx.example; dkim=pass header.d=vendor.example; spf=pass"


@pytest.mark.parametrize(
    ("auth_results", "expected"),
    [
        pytest.param(_ALIGNED_PASS, VERIFICATION_AUTHENTICATED, id="aligned-dkim-pass"),
        pytest.param(
            "mx.example; dkim=pass header.d=mail.vendor.example",
            VERIFICATION_AUTHENTICATED,
            id="signer-is-subdomain",
        ),
        pytest.param(
            "mx.example; dkim=pass header.i=@vendor.example",
            VERIFICATION_UNVERIFIED,
            id="header-i-alone-does-not-align",
        ),
        pytest.param(
            "mx.example; dmarc=pass header.from=vendor.example",
            VERIFICATION_AUTHENTICATED,
            id="dmarc-pass",
        ),
        pytest.param(
            "mx.example; dmarc=fail header.from=vendor.example",
            VERIFICATION_FAILED,
            id="dmarc-fail",
        ),
        pytest.param(
            "mx.example; dmarc=permerror header.from=vendor.example",
            VERIFICATION_FAILED,
            id="dmarc-permerror",
        ),
        pytest.param(
            "mx.example; dkim=fail header.d=vendor.example",
            VERIFICATION_FAILED,
            id="aligned-dkim-fail",
        ),
        pytest.param(
            "mx.example; dkim=fail header.d=other.example; dkim=pass header.d=vendor.example",
            VERIFICATION_AUTHENTICATED,
            id="unaligned-fail-is-ignored",
        ),
        pytest.param(
            "mx.example; dkim=pass header.d=other.example",
            VERIFICATION_UNVERIFIED,
            id="unaligned-pass",
        ),
        pytest.param(
            "mx.example; dkim=pass header.d=evilvendor.example",
            VERIFICATION_UNVERIFIED,
            id="suffix-lookalike-is-not-a-subdomain",
        ),
        pytest.param(
            "mx.example; dkim=pass header.d=other.vendor.example",
            VERIFICATION_AUTHENTICATED,
            id="subdomain-signer-of-the-sender-org",
        ),
        pytest.param(
            "mx.example; dkim=pass header.d=example",
            VERIFICATION_UNVERIFIED,
            id="a-bare-tld-signer-does-not-vouch",
        ),
        pytest.param(
            "mx.example; dkim=temperror header.d=vendor.example",
            VERIFICATION_UNVERIFIED,
            id="temperror",
        ),
        pytest.param("mx.example; dkim=none", VERIFICATION_UNVERIFIED, id="none"),
        pytest.param(
            "mx.example; x-dkim=pass header.d=vendor.example",
            VERIFICATION_UNVERIFIED,
            id="method-must-start-its-clause",
        ),
        pytest.param(None, VERIFICATION_UNVERIFIED, id="missing-header"),
        pytest.param("  ", VERIFICATION_UNVERIFIED, id="blank-header"),
    ],
)
def test_verify_sender_domain(auth_results, expected):
    assert verify_sender_domain(auth_results, DOMAIN) == expected


def test_sibling_subdomains_do_not_vouch_for_each_other():
    # No public-suffix list: a tenant of a shared suffix must not authenticate a sibling.
    assert (
        verify_sender_domain("mx.example; dkim=pass header.d=a.github.example", "b.github.example")
        == VERIFICATION_UNVERIFIED
    )


def test_empty_sender_domain_is_unverified():
    assert verify_sender_domain(_ALIGNED_PASS, "") == VERIFICATION_UNVERIFIED


def _raw(*values: str) -> dict:
    return {
        "payload": {"headers": [{"name": "Authentication-Results", "value": v} for v in values]}
    }


def test_only_the_topmost_header_counts():
    forged_below = _raw("mx.example; dkim=none", _ALIGNED_PASS)
    assert (
        sender_authentication_verdict(
            source_channel="email", sender_address=f"billing@{DOMAIN}", raw=forged_below
        )
        == VERIFICATION_UNVERIFIED
    )


def test_a_forged_pass_below_a_failure_cannot_override_it():
    raw = _raw(f"mx.example; dmarc=fail header.from={DOMAIN}", _ALIGNED_PASS)
    assert (
        sender_authentication_verdict(
            source_channel="email", sender_address=f"billing@{DOMAIN}", raw=raw
        )
        == VERIFICATION_FAILED
    )


@pytest.mark.parametrize("raw", [None, {}, {"text": "hi"}], ids=["none", "empty", "no-headers"])
def test_an_email_without_a_kept_header_is_unverified(raw):
    assert (
        sender_authentication_verdict(
            source_channel="email", sender_address=f"billing@{DOMAIN}", raw=raw
        )
        == VERIFICATION_UNVERIFIED
    )


def test_the_sender_domain_comes_from_the_address_case_insensitively():
    assert (
        sender_authentication_verdict(
            source_channel="email",
            sender_address=" Billing@Vendor.Example ",
            raw=_raw(_ALIGNED_PASS),
        )
        == VERIFICATION_AUTHENTICATED
    )


@pytest.mark.parametrize("channel", ["telegram_bot", "whatsapp_user_client", "api"])
def test_non_email_channels_store_no_verdict(channel):
    assert (
        sender_authentication_verdict(
            source_channel=channel, sender_address=f"billing@{DOMAIN}", raw=_raw(_ALIGNED_PASS)
        )
        is None
    )


@pytest.mark.parametrize("sender", ["", "no-at-sign", "   "])
def test_an_email_sender_without_a_domain_stores_no_verdict(sender):
    assert (
        sender_authentication_verdict(
            source_channel="email", sender_address=sender, raw=_raw(_ALIGNED_PASS)
        )
        is None
    )


@pytest.mark.parametrize(
    ("auth_results", "verification"),
    [
        ("mx; dkim=pass header.d=google.com", VERIFICATION_AUTHENTICATED),
        ("mx; dkim=pass header.d=other.example", VERIFICATION_UNVERIFIED),
        (None, VERIFICATION_UNVERIFIED),
    ],
)
def test_provider_alert_classification_is_unchanged(auth_results, verification):
    verdict = classify_account_security(
        "no-reply@accounts.google.com",
        "Security alert",
        headers={"Authentication-Results": auth_results} if auth_results else None,
    )
    assert verdict is not None and verdict.sender_verification == verification


@pytest.mark.parametrize(
    "auth_results",
    [
        "mx; dmarc=fail header.from=google.com",
        "mx; dkim=fail header.d=google.com",
    ],
)
def test_provider_spoofs_still_drop_the_alert(auth_results):
    assert (
        classify_account_security(
            "no-reply@accounts.google.com",
            "Security alert",
            headers={"Authentication-Results": auth_results},
        )
        is None
    )

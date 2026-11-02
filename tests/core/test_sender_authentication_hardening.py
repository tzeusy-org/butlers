"""Hardening of the sender-authentication parser against echoed sender text (bu-s11n0s.20).

Receivers copy sender-chosen strings (a quoted MAIL FROM, a HELO literal, the ``header.i``
local part) into ``Authentication-Results``; RFC 8601 lets them hold ``;`` and ``)``. These
cases come from the security review's probe. Synthetic domains only.
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

V = "vendor.example"
_SPF = "spf=pass (google.com: domain of x@evil.example designates 192.0.2.1 as permitted sender)"

_CASE_A = f'mx.google.com; {_SPF} smtp.mailfrom="a;dkim=pass header.d=vendor.example"@evil.example'
_CASE_B = (
    'mx.google.com; spf=neutral (google.com: "a)b;dkim=pass header.d=vendor.example"'
    "@evil.example is neither permitted nor denied by best guess record for domain)"
)
_CASE_C = (
    "mx.google.com; spf=neutral (google.com: helo is neither permitted nor denied)"
    ' smtp.helo=[x:y;dkim=pass,header.d=vendor.example"]'
)
_CASE_D = (
    'mx.google.com; dkim=pass header.i="a;dmarc=pass header.from=vendor.example"'
    "@evil.example header.s=s1 header.b=abc"
)
_CASE_E = _CASE_A + "; dmarc=fail (p=NONE sp=NONE dis=NONE) header.from=vendor.example"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        pytest.param(_CASE_A, VERIFICATION_UNVERIFIED, id="A-quoted-mailfrom-semicolon"),
        pytest.param(_CASE_B, VERIFICATION_UNVERIFIED, id="B-paren-in-echoed-text"),
        pytest.param(_CASE_C, VERIFICATION_UNVERIFIED, id="C-helo-address-literal"),
        pytest.param(_CASE_D, VERIFICATION_UNVERIFIED, id="D-quoted-header-i-local-part"),
        pytest.param(_CASE_E, VERIFICATION_FAILED, id="E-real-dmarc-fail-still-fails"),
        pytest.param(
            "mx.google.com; dkim=pass header.d=vendor.example (unbalanced",
            VERIFICATION_UNVERIFIED,
            id="unterminated-comment",
        ),
        pytest.param(
            "mx.google.com; dkim=pass header.d=vendor.example)",
            VERIFICATION_UNVERIFIED,
            id="stray-close-paren",
        ),
        pytest.param(
            'mx.google.com; dkim=pass header.d="vendor.example',
            VERIFICATION_UNVERIFIED,
            id="unterminated-quote",
        ),
        pytest.param(
            "mx.google.com; dkim=pass header.d=vendor.example garbage",
            VERIFICATION_UNVERIFIED,
            id="leftover-token-fails-closed",
        ),
        pytest.param(
            "mx.google.com; dkim=pass,header.d=vendor.example",
            VERIFICATION_UNVERIFIED,
            id="comma-glued-property",
        ),
        pytest.param(
            "mx.google.com; spf=pass smtp.mailfrom=x@evil.example; dkim=pass header.d=vendor.example",
            VERIFICATION_AUTHENTICATED,
            id="honest-header-still-authenticates",
        ),
        pytest.param(
            "mx.google.com; dkim = pass (good sig) header.d=vendor.example",
            VERIFICATION_AUTHENTICATED,
            id="whitespace-around-equals",
        ),
        pytest.param("mx.google.com; none", VERIFICATION_UNVERIFIED, id="none-form"),
    ],
)
def test_echoed_sender_text_cannot_forge_a_clause(header, expected):
    assert verify_sender_domain(header, V) == expected


def test_the_ingest_helper_reads_the_same_hardened_header():
    raw = {"payload": {"headers": [{"name": "Authentication-Results", "value": _CASE_A}]}}

    assert (
        sender_authentication_verdict(
            source_channel="email", sender_address=f"billing@{V}", raw=raw
        )
        == VERIFICATION_UNVERIFIED
    )


def test_an_injected_provider_clause_reads_unverified():
    verdict = classify_account_security(
        "no-reply@accounts.google.com",
        "Security alert",
        headers={
            "Authentication-Results": "mx.google.com; spf=pass smtp.mailfrom="
            '"a;dkim=pass header.d=google.com"@evil.example'
        },
    )

    assert verdict is not None and verdict.sender_verification == VERIFICATION_UNVERIFIED


def test_a_parent_signer_of_two_labels_aligns_with_a_subdomain_sender():
    assert (
        verify_sender_domain("mx; dkim=pass header.d=vendor.example", "mail.vendor.example")
        == VERIFICATION_AUTHENTICATED
    )


def test_a_dmarc_fail_fails_whatever_its_header_from():
    header = "mx; dmarc=fail header.from=other.example; dkim=pass header.d=vendor.example"

    assert verify_sender_domain(header, V) == VERIFICATION_FAILED


def test_the_public_verifier_lowercases_and_trims_the_sender_domain():
    assert (
        verify_sender_domain("mx; dkim=pass header.d=mail.vendor.example", " Vendor.Example. ")
        == VERIFICATION_AUTHENTICATED
    )


def test_softfail_on_an_aligned_dkim_fails():
    assert verify_sender_domain("mx; dkim=softfail header.d=vendor.example", V) == (
        VERIFICATION_FAILED
    )


@pytest.mark.parametrize(
    "sender",
    [
        "x@",
        "@vendor.example",
        "x@evil.example, billing@vendor.example",
        "billing@vendor.example)<x@evil.example>",
        '"billing@vendor.example" <x@evil.example>',
        "a b@vendor.example",
    ],
)
def test_a_sender_identity_that_is_not_one_bare_address_stores_no_verdict(sender):
    raw = {
        "payload": {
            "headers": [
                {"name": "Authentication-Results", "value": "mx; dkim=pass header.d=vendor.example"}
            ]
        }
    }

    assert sender_authentication_verdict(
        source_channel="email", sender_address=sender, raw=raw
    ) is (None)


def test_provider_alerts_still_need_the_providers_own_signing_domain():
    verdict = classify_account_security(
        "account-security-noreply@accountprotection.microsoft.com",
        "Security alert",
        headers={"Authentication-Results": "mx; dkim=pass header.d=notmicrosoft.com"},
    )

    assert verdict is not None and verdict.sender_verification == VERIFICATION_UNVERIFIED


# The tokenizer and domain reader are tested directly: the cases above are also stopped by a
# second layer, so each layer needs a case only it can fail.
def test_the_tokenizer_never_splits_inside_quotes_brackets_or_comments():
    from butlers.account_security import _tokenize

    assert _tokenize('mx; a=b smtp.x="p;q r" c=d') == [["mx"], ["a=b", 'smtp.x="p;q r"', "c=d"]]
    assert _tokenize("mx; smtp.helo=[a;b c] k=v") == [["mx"], ["smtp.helo=[a;b c]", "k=v"]]
    assert _tokenize('mx; a=b (note "x)y;z" more) c=d') == [["mx"], ["a=b", "c=d"]]
    assert _tokenize('mx; a="q\\"r;s" b=c') == [["mx"], ['a="q\\"r;s"', "b=c"]]


@pytest.mark.parametrize(
    "value", ["mx; a=b)", "mx; a=b]", 'mx; a="b', "mx; a=[b", "mx; a=b (c", 'mx; a=b (c "d)']
)
def test_the_tokenizer_rejects_unbalanced_input(value):
    from butlers.account_security import _tokenize

    assert _tokenize(value) is None


def test_a_segment_that_is_not_a_clause_fails_the_whole_header():
    from butlers.account_security import _clauses

    assert _clauses("mx; foo; dkim=pass header.d=vendor.example") is None
    assert _clauses("mx; dkim=pass header.d=vendor.example stray") is None
    assert _clauses("mx; dkim=pass header.d=vendor.example;") == [
        ("dkim", "pass", {"header.d": "vendor.example"})
    ]


@pytest.mark.parametrize(
    ("value", "domain"),
    [
        ("vendor.example", "vendor.example"),
        ("@vendor.example", "vendor.example"),
        ("user@vendor.example", "vendor.example"),
        ('"a;b"@vendor.example', "vendor.example"),
        ('"a\\"b"@vendor.example', "vendor.example"),
        ('"vendor.example"', ""),
        ('"a"@b"@vendor.example', ""),
        ("vendor.example,evil", ""),
        ("a b", ""),
        ("", ""),
    ],
)
def test_the_property_domain_is_a_domain_or_empty(value, domain):
    from butlers.account_security import _property_domain

    assert _property_domain(value) == domain

"""An unreadable header may hide a forgery but never a real failure (bu-s11n0s.20).

One stray character in sender-chosen echoed text makes the strict parser refuse the whole
header. The refusal must fail closed for trust (never ``authenticated``) and open for failure:
a genuine dmarc fail or permerror, or a fail on an aligned dkim, still reads ``failed``, and a
spoofed provider alert is still dropped.
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

pytestmark = pytest.mark.unit

V = "vendor.example"
_SPF = "spf=neutral (google.com: helo is neither permitted nor denied) smtp.helo="
_DMARC_FAIL = "dmarc=fail (p=NONE sp=NONE dis=NONE) header.from=vendor.example"


@pytest.mark.parametrize("stray", ["mail.evil.example", "a(b", 'a"b', "a b"])
def test_a_real_dmarc_fail_survives_stray_echoed_text(stray):
    header = f"mx.google.com; {_SPF}{stray}; {_DMARC_FAIL}"

    assert verify_sender_domain(header, V) == VERIFICATION_FAILED


@pytest.mark.parametrize(
    "tail",
    [
        "dmarc=permerror header.from=other.example",
        "dkim=fail header.d=vendor.example",
        "dkim=softfail header.d=mail.vendor.example",
    ],
)
def test_permerror_and_an_aligned_dkim_fail_survive_unreadable_text(tail):
    header = f"mx.google.com; {_SPF}a(b; {tail}"

    assert verify_sender_domain(header, V) == VERIFICATION_FAILED


@pytest.mark.parametrize(
    "tail",
    [
        "dkim=pass header.d=vendor.example",
        "dmarc=pass header.from=vendor.example",
        "dkim=fail header.d=other.example",
        "dkim=temperror header.d=vendor.example",
    ],
)
def test_an_unreadable_header_is_never_authenticated(tail):
    header = f"mx.google.com; {_SPF}a(b; {tail}"

    assert verify_sender_domain(header, V) in {VERIFICATION_UNVERIFIED, VERIFICATION_FAILED}
    assert verify_sender_domain(header, V) != VERIFICATION_AUTHENTICATED


def test_an_unaligned_dkim_fail_in_an_unreadable_header_stays_unverified():
    header = f"mx.google.com; {_SPF}a(b; dkim=fail header.d=other.example"

    assert verify_sender_domain(header, V) == VERIFICATION_UNVERIFIED


def test_a_dkim_fail_clause_does_not_borrow_another_clauses_signer():
    header = f"mx.google.com; {_SPF}a(b; dkim=fail header.d=other.example; dkim=pass header.d={V}"

    assert verify_sender_domain(header, V) == VERIFICATION_UNVERIFIED


@pytest.mark.parametrize("stray", ["mail.evil.example", "a(b", 'a"b', "a b"])
def test_a_spoofed_provider_alert_is_still_dropped(stray):
    header = (
        f"mx.google.com; dkim=fail header.d=google.com; {_SPF}{stray}; "
        "dmarc=fail (p=REJECT) header.from=accounts.google.com"
    )

    assert (
        classify_account_security(
            "no-reply@accounts.google.com",
            "Security alert",
            headers={"Authentication-Results": header},
        )
        is None
    )


def test_a_provider_dmarc_permerror_with_unreadable_text_is_still_dropped():
    header = "mx.google.com; spf=pass smtp.helo=a(b; dmarc=permerror header.from=google.com"

    assert (
        classify_account_security(
            "no-reply@accounts.google.com",
            "Security alert",
            headers={"Authentication-Results": header},
        )
        is None
    )


def test_header_i_does_not_align_the_sender_domain_but_still_aligns_provider_alerts():
    assert verify_sender_domain("mx; dkim=pass header.i=@vendor.example", V) == (
        VERIFICATION_UNVERIFIED
    )
    verdict = classify_account_security(
        "no-reply@accounts.google.com",
        "Security alert",
        headers={"Authentication-Results": "mx; dkim=pass header.i=@mail.google.com"},
    )
    assert verdict is not None and verdict.sender_verification == VERIFICATION_AUTHENTICATED

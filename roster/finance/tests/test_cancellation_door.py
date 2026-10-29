"""Inert display domain for unverified subscription cancellation links (bu-svqpzo.10)."""

from __future__ import annotations

import re

import pytest

from butlers.tools.finance.cancellation_door import defanged_domain, registrable_domain


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example-phish.test/cancel", "example-phish.test"),
        ("https://login.account.Example.COM:8443/cancel?token=abc#x", "example.com"),
        ("https://user:secret@evil.example/path", "evil.example"),
        ("https://www.shop.co.uk/cancel", "shop.co.uk"),
        ("https://tenant.github.io/cancel", "tenant.github.io"),
        ("https://bücher.example/cancel", "xn--bcher-kva.example"),
        ("https://example.com./cancel", "example.com"),
    ],
)
def test_registrable_domain_is_the_plain_text_domain_only(url: str, expected: str) -> None:
    assert registrable_domain(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "   ",
        "javascript:alert(1)",
        "ftp://example.com/cancel",
        "http://127.0.0.1/cancel",
        "https://[::1]/cancel",
        "https://localhost/cancel",
        "https://ex ample.com/cancel",
        "not a url",
    ],
)
def test_registrable_domain_is_none_when_the_link_names_no_domain(url: str | None) -> None:
    assert registrable_domain(url) is None


def test_defanged_domain_cannot_be_linkified() -> None:
    assert defanged_domain("example-phish.test") == "example-phish[.]test"
    assert re.search(r"[A-Za-z0-9-]+\.[A-Za-z]{2,}", defanged_domain("shop.co.uk")) is None

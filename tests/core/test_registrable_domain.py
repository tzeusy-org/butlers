"""Registrable domain is the eTLD+1 under the vendored Public Suffix List (REQ-vendor-doors-002)."""

from __future__ import annotations

import pytest

from butlers.core.registrable_domain import registrable_domain, sender_registrable_domain


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("example.com", "example.com"),
        ("www.netflix.com", "netflix.com"),
        ("A.B.Example.CO.UK", "example.co.uk"),
        ("attacker.com.my", "attacker.com.my"),
        ("vendor.com.my", "vendor.com.my"),
        ("a.myshopify.com", "a.myshopify.com"),
        ("x.a.myshopify.com", "a.myshopify.com"),
        ("b.kobe.jp", None),
        ("a.b.kobe.jp", "a.b.kobe.jp"),
        ("city.kobe.jp", "city.kobe.jp"),
    ],
)
def test_etld_plus_one(name: str, expected: str | None) -> None:
    assert registrable_domain(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "com",
        "com.my",
        "myshopify.com",
        "foo.invalidtld",
        "127.0.0.1",
        "[::1]",
        "example.com.",
        ".example.com",
        "exa mple.com",
        "bücher.de",
        "xn--bcher-kva.de",
        "a..com",
        "",
        "a" * 64 + ".com",
    ],
)
def test_uncomputable_names_fail_closed(name: str) -> None:
    assert registrable_domain(name) is None


def test_distinct_suffix_boundaries_are_distinct_domains() -> None:
    assert registrable_domain("attacker.com.my") != registrable_domain("vendor.com.my")
    assert registrable_domain("a.myshopify.com") != registrable_domain("b.myshopify.com")


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("billing@mail.netflix.com", "netflix.com"),
        ("Billing <billing@Netflix.com>", None),
        ("x@y@netflix.com", None),
        ("no-at-sign", None),
        ("a@localhost", None),
        ("", None),
    ],
)
def test_sender_domain_requires_one_bare_address(address: str, expected: str | None) -> None:
    assert sender_registrable_domain(address) == expected

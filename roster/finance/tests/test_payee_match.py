"""Unit tests for the payee token-subset matcher specificity guard (bu-r8i07).

These are pure-function tests for ``_payee_match`` — no database or Docker — so
they run in the default suite. They lock in the short-payee-name false-positive
guard added in bu-r8i07: a single very short token (1-2 chars) must not anchor a
cross-payee match, while legitimate payees (>= 3 chars) keep matching.
"""

from __future__ import annotations

import pytest

from butlers.tools.finance.reconciliation import _payee_match


class TestShortPayeeFalsePositiveGuard:
    """Very short payee names must NOT match unrelated payees."""

    @pytest.mark.parametrize(
        ("bill_payee", "txn_merchant"),
        [
            # 2-char token is a whole-token subset of an unrelated multi-token payee.
            ("AT", "AT&T Wireless"),
            ("BP", "BP Gas"),
            # 2-char string is a substring of an unrelated payee ("se[at]tle").
            ("AT", "Seattle Water"),
            # Reverse direction: the short *transaction* token must not anchor either.
            ("BP Gas Station", "BP"),
        ],
    )
    def test_short_payee_does_not_match(self, bill_payee: str, txn_merchant: str) -> None:
        is_match, is_exact = _payee_match(bill_payee, txn_merchant)
        assert is_match is False
        assert is_exact is False

    def test_short_token_in_alt_merchant_does_not_match(self) -> None:
        # The normalized-merchant alternate path is guarded too.
        is_match, _ = _payee_match("AT", "Some Vendor", txn_normalized_merchant="AT&T Mobility")
        assert is_match is False


class TestLegitimatePayeeMatchesPreserved:
    """The guard must not regress real payee matches (>= 3-char tokens)."""

    @pytest.mark.parametrize(
        ("bill_payee", "txn_merchant", "want_exact"),
        [
            ("HSBC", "HSBC Credit Card", False),  # 4-char token subset
            ("DBS", "DBS GIRO", False),  # 3-char token subset (boundary)
            ("UOB", "UOB One Card", False),  # 3-char token subset (boundary)
            ("HSBC Credit Card", "HSBC", False),  # reverse: txn is the 4-char subset
            ("Netflix", "NETFLIX.COM", False),  # substring containment fallback
            ("HSBC Credit Card", "hsbc credit card", True),  # exact (normalized)
        ],
    )
    def test_legit_payee_still_matches(
        self, bill_payee: str, txn_merchant: str, want_exact: bool
    ) -> None:
        is_match, is_exact = _payee_match(bill_payee, txn_merchant)
        assert is_match is True
        assert is_exact is want_exact

    def test_three_char_alt_merchant_matches(self) -> None:
        is_match, _ = _payee_match("DBS", "Unknown", txn_normalized_merchant="DBS GIRO")
        assert is_match is True

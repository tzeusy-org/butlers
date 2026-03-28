"""Unit tests for _infer_account_type heuristic in overview.py.

These are pure unit tests — no database or Docker required.

Covers:
- The motivating bug: "Credit Union Checking" must resolve to "checking",
  not "credit".
- All four account types with representative account names.
- Default fallback when no keyword matches.
- Edge cases: case-insensitivity, compound names, abbreviations.
"""

from __future__ import annotations

import pytest

from butlers.tools.finance.overview import _infer_account_type


class TestCheckingInference:
    """Accounts whose name contains 'checking' must always resolve as checking."""

    @pytest.mark.parametrize(
        "name",
        [
            "Checking",
            "Personal Checking",
            "Joint Checking Account",
            # The canonical bug case: "credit" appears before "checking"
            "Credit Union Checking",
            "My Credit Union Checking Account",
            "Alliant CU Checking",
        ],
    )
    def test_checking_accounts(self, name: str) -> None:
        assert _infer_account_type(name) == "checking", (
            f"Expected 'checking' for {name!r}, got {_infer_account_type(name)!r}"
        )

    def test_checking_beats_credit_keyword(self) -> None:
        """'checking' in the name overrides any credit-related substring."""
        assert _infer_account_type("Credit Union Checking") == "checking"

    def test_checking_beats_savings_keyword(self) -> None:
        """'checking' takes priority over 'savings' when both appear."""
        assert _infer_account_type("Savings and Checking Account") == "checking"


class TestCreditInference:
    """Credit-card and payment-network names resolve as credit."""

    @pytest.mark.parametrize(
        "name",
        [
            "Credit Card",
            "Chase Credit Card",
            "Visa",
            "Mastercard",
            "Amex",
            "Visa Signature Card",
            "Debit Card",  # 'card' is the trigger, type is still 'credit' for classification
            "CC",
            "My CC Account",
        ],
    )
    def test_credit_accounts(self, name: str) -> None:
        assert _infer_account_type(name) == "credit", (
            f"Expected 'credit' for {name!r}, got {_infer_account_type(name)!r}"
        )

    def test_credit_union_does_not_match_credit(self) -> None:
        """'Credit Union' alone (no 'checking' or 'card') falls back to default."""
        # A bare "Credit Union" account name has no explicit type markers;
        # it should use the supplied default rather than inferring 'credit'.
        assert _infer_account_type("Credit Union", default="checking") == "checking"

    def test_credit_union_savings_resolves_savings(self) -> None:
        """'Credit Union Savings' should resolve to 'savings', not 'credit'."""
        assert _infer_account_type("Credit Union Savings") == "savings"


class TestSavingsInference:
    """Savings and HSA accounts resolve as savings."""

    @pytest.mark.parametrize(
        "name",
        [
            "Savings",
            "High-Yield Savings",
            "Emergency Fund Save",
            "HSA",
            "Health Savings Account (HSA)",
        ],
    )
    def test_savings_accounts(self, name: str) -> None:
        assert _infer_account_type(name) == "savings", (
            f"Expected 'savings' for {name!r}, got {_infer_account_type(name)!r}"
        )


class TestInvestmentInference:
    """Investment and brokerage accounts resolve as investment."""

    @pytest.mark.parametrize(
        "name",
        [
            "Investment Account",
            "Investing Portfolio",
            "IRA",
            "Roth IRA",
            "Traditional IRA",
            "401k",
            "Brokerage",
            "Fidelity",
            "Fidelity Roth IRA",
        ],
    )
    def test_investment_accounts(self, name: str) -> None:
        assert _infer_account_type(name) == "investment", (
            f"Expected 'investment' for {name!r}, got {_infer_account_type(name)!r}"
        )


class TestDefaultFallback:
    """When no keyword matches, the default is returned."""

    def test_unknown_name_uses_default_checking(self) -> None:
        assert _infer_account_type("Main Account") == "checking"

    def test_unknown_name_respects_explicit_default(self) -> None:
        assert _infer_account_type("Main Account", default="savings") == "savings"

    def test_empty_string_uses_default(self) -> None:
        assert _infer_account_type("", default="checking") == "checking"


class TestCaseInsensitivity:
    """All matching is case-insensitive."""

    def test_all_caps_checking(self) -> None:
        assert _infer_account_type("CHECKING") == "checking"

    def test_mixed_case_visa(self) -> None:
        assert _infer_account_type("VISA Card") == "credit"

    def test_mixed_case_savings(self) -> None:
        assert _infer_account_type("High-Yield SAVINGS") == "savings"

    def test_mixed_case_ira(self) -> None:
        assert _infer_account_type("Roth IRA") == "investment"

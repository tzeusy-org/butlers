"""Tests for the capture() ownership refusal map (bu-2jtfw.9)."""

from __future__ import annotations

import pytest

from butlers.core.ownership_refusal import check_ownership_refusal

pytestmark = pytest.mark.unit


def test_bank_alert_is_refused_naming_finance_and_its_tool() -> None:
    refusal = check_ownership_refusal(
        "ALERT: Your bank account balance is now $42.10 after a withdrawal"
    )

    assert refusal is not None
    assert refusal.owner_butler == "Finance"
    assert refusal.tool_hint == "finance.record_transaction"
    assert "Finance" in refusal.reason
    assert "finance.record_transaction" in refusal.reason


def test_home_maintenance_content_is_refused_naming_home() -> None:
    refusal = check_ownership_refusal("The furnace filter needs replacing next month")

    assert refusal is not None
    assert refusal.owner_butler == "Home"
    assert refusal.tool_hint == "home.ha_maintenance_create"


def test_plain_note_is_not_refused() -> None:
    refusal = check_ownership_refusal("remember to water the plants this weekend")

    assert refusal is None


def test_relationship_birthday_content_is_refused_naming_relationship() -> None:
    refusal = check_ownership_refusal("Don't forget, it's her birthday next Tuesday")

    assert refusal is not None
    assert refusal.owner_butler == "Relationship"
    assert refusal.tool_hint == "relationship.fact_set"

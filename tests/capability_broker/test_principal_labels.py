"""S1: the broker's principal carries the invocation's label; missing fails closed.

No database: ``BrokerLedger._principal`` is the one place a stored invocation
row becomes the authority every policy decision reads (bu-dhloi1.6).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from butlers.capability_broker.contracts import BrokerRefused, Reason, declared_categories
from butlers.capability_broker.ledger import BrokerLedger
from butlers.core.goal_admission import digest, encode_grants
from butlers.core.goal_labels import CATEGORIES

GOAL = {"id": uuid4(), "origin": "owner", "exposed": True, "catalog_digest": "c"}


def _row(**label):
    snapshot = encode_grants(())
    row = {
        "id": uuid4(),
        "depth": 0,
        "exposed": False,
        "grant_snapshot": snapshot,
        "grant_digest": digest(snapshot),
        "label_version": 1,
        "label_instr": ["owner"],
        "label_data": ["owner"],
        "label_sens": [],
    }
    row.update(label)
    return row


def test_a_clean_invocation_is_unexposed_even_when_its_goal_union_is():
    principal = BrokerLedger._principal(GOAL, _row())
    assert principal.exposed is False and not principal.label.exposed


def test_an_exposed_label_or_legacy_boolean_exposes_the_principal():
    assert BrokerLedger._principal(GOAL, _row(label_data=["owner", "world"])).exposed
    assert BrokerLedger._principal(GOAL, _row(exposed=True)).exposed
    assert BrokerLedger._principal(GOAL, _row(exposed=None)).exposed


@pytest.mark.parametrize(
    "label",
    [
        {"label_version": None},
        {"label_version": 2},
        {"label_instr": None},
        {"label_data": []},
        {"label_sens": None},
        {"label_sens": ["horoscope"]},
        {"label_data": ["mallory"]},
    ],
)
def test_a_missing_or_unknown_label_refuses_authentication(label):
    with pytest.raises(BrokerRefused) as refused:
        BrokerLedger._principal(GOAL, _row(**label))
    assert refused.value.reason is Reason.AUTHENTICATION


@pytest.mark.parametrize(
    ("sensitivity", "expected"),
    [("ordinary", frozenset()), ("sensitive", CATEGORIES), (None, CATEGORIES), ("x", CATEGORIES)],
)
def test_only_ordinary_output_carries_no_category(sensitivity, expected):
    descriptor = SimpleNamespace(output_sensitivity=sensitivity)
    assert declared_categories(descriptor) == expected


def test_a_row_without_label_columns_refuses_authentication():
    row = _row()
    del row["label_sens"]
    with pytest.raises(BrokerRefused) as refused:
        BrokerLedger._principal(GOAL, row)
    assert refused.value.reason is Reason.AUTHENTICATION

"""Pure tests for the conservative canonical payee identity (bu-swkpoo.15)."""

from __future__ import annotations

import importlib
import itertools
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from butlers.roster_imports import roster_module
from butlers.testing.fake_pool import FakeConnection

canonical_payee_key = roster_module("finance", "tools/reconciliation").canonical_payee_key


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Acme Broadband", "Acme Broadband (acme.example.com)"),
        ("Acme Broadband", "  ACME   broadband (www.acme.example) "),
        ("Acme Broadband.", "acme broadband (https://acme.example)"),
        ("Zeta Bank CC", "Zeta Bank Credit Card"),
        ("zeta bank cc", "ZETA  BANK  CREDIT  CARD"),
        ("Zeta Bank Credit Card (zeta.example)", "Zeta Bank CC"),
    ],
)
def test_equivalent_variants_share_a_canonical_key(a: str, b: str) -> None:
    assert canonical_payee_key(a) == canonical_payee_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # A bare name prefix is not evidence of identity.
        ("Quark Fund", "Quark Fund Retirement Account"),
        ("Pat Smith", "Landlord Pat Smith"),
        # Different banks stay distinct even with the same product suffix.
        ("Zeta Bank CC", "Omega Bank Credit Card"),
        # A parenthesised non-domain qualifier is a real distinction.
        ("Acme Broadband", "Acme Broadband (business)"),
        # Product words are only equivalent as a trailing suffix.
        ("Credit Card Services", "CC Services"),
    ],
)
def test_non_equivalent_payees_stay_distinct(a: str, b: str) -> None:
    assert canonical_payee_key(a) != canonical_payee_key(b)


def test_canonical_key_is_idempotent() -> None:
    once = canonical_payee_key("Zeta Bank Credit Card (zeta.example)")
    assert canonical_payee_key(once) == once


# --- Docker-free proof of the roll grouping (bu-swkpoo.15) --------------------


_ids = itertools.count(1)


def _bill(payee: str, due: date, *, frequency="monthly", status="paid", created=0) -> dict:
    return {
        "id": next(_ids),
        "payee": payee,
        "payee_key": payee.lower(),
        "key": payee.lower(),
        "amount": Decimal("10.00"),
        "currency": "USD",
        "due_date": due,
        "frequency": frequency,
        "status": status,
        "autopay": False,
        "payment_method": None,
        "account_id": None,
        "metadata": {},
        "created_at": datetime(2031, 1, 1, tzinfo=UTC) + timedelta(seconds=created),
    }


async def _roll(rows: list[dict], today: date) -> list[tuple]:
    fj = importlib.import_module("butlers.jobs._roster.finance_jobs")
    conn = FakeConnection(fetch=rows, execute=None)
    await fj._roll_recurring_bills(conn, today)
    return [call.args[1:] for call in conn.execute.call_args_list]


async def test_roll_groups_variants_into_one_insert() -> None:
    today = date(2031, 6, 20)
    rows = [
        _bill("Acme Broadband", date(2031, 6, 10), created=1),
        _bill("Acme Broadband (acme.example.com)", date(2031, 6, 10), created=2),
        _bill("Zeta Bank CC", date(2031, 5, 22), created=3),
        _bill("Zeta Bank Credit Card", date(2031, 6, 19), created=4),
    ]
    inserts = await _roll(rows, today)
    assert len(inserts) == 2
    assert sorted(i[0] for i in inserts) == [
        "Acme Broadband (acme.example.com)",
        "Zeta Bank Credit Card",
    ]


async def test_roll_keeps_bare_prefix_payees_separate() -> None:
    today = date(2031, 6, 20)
    rows = [
        _bill("Quark Fund", date(2031, 6, 10)),
        _bill("Quark Fund Retirement", date(2031, 6, 10)),
    ]
    assert len(await _roll(rows, today)) == 2


async def test_roll_skips_when_variant_has_later_bill_in_target_period() -> None:
    today = date(2031, 6, 20)
    rows = [
        _bill("Acme Broadband", date(2031, 6, 10)),
        _bill(
            "Acme Broadband (acme.example.com)",
            date(2031, 7, 12),
            frequency="one_time",
            status="pending",
        ),
    ]
    assert await _roll(rows, today) == []


async def test_roll_writes_metadata_as_object_not_json_string() -> None:
    rows = [_bill("Acme Broadband", date(2031, 6, 10))]
    inserts = await _roll(rows, date(2031, 6, 20))
    assert isinstance(inserts[0][-1], dict)
    assert inserts[0][-1]["rolled_from"] == str(rows[0]["id"])

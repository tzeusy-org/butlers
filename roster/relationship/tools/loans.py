"""Loans — track money lent or borrowed."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.tools.relationship._schema import table_columns
from butlers.tools.relationship.feed import _log_activity


async def loan_create(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID | None = None,
    amount: Decimal | None = None,
    direction: str | None = None,
    description: str | None = None,
    *,
    lender_contact_id: uuid.UUID | None = None,
    borrower_contact_id: uuid.UUID | None = None,
    amount_cents: int | None = None,
    currency: str = "USD",
) -> dict[str, Any]:
    """Create a loan record with legacy + spec-compatible fields."""
    cols = await table_columns(pool, "loans")

    if amount_cents is None and amount is not None:
        amount_cents = int((amount * 100).quantize(Decimal("1")))
    if amount is None and amount_cents is not None:
        amount = (Decimal(amount_cents) / Decimal(100)).quantize(Decimal("0.01"))

    if lender_contact_id is None and borrower_contact_id is None and contact_id is not None:
        if direction == "lent":
            lender_contact_id = contact_id
        elif direction == "borrowed":
            borrower_contact_id = contact_id

    insert_cols: list[str] = []
    values: list[Any] = []

    def add(col: str, value: Any) -> None:
        if col in cols:
            insert_cols.append(col)
            values.append(value)

    add("contact_id", contact_id)
    add("amount", amount)
    add("direction", direction)
    add("description", description)
    add("lender_contact_id", lender_contact_id)
    add("borrower_contact_id", borrower_contact_id)
    add("amount_cents", amount_cents)
    add("currency", currency)

    placeholders = [f"${idx}" for idx in range(1, len(values) + 1)]
    row = await pool.fetchrow(
        f"""
        INSERT INTO loans ({", ".join(insert_cols)})
        VALUES ({", ".join(placeholders)})
        RETURNING *
        """,
        *values,
    )
    result = dict(row)
    if "amount" not in result and result.get("amount_cents") is not None:
        result["amount"] = (Decimal(result["amount_cents"]) / Decimal(100)).quantize(
            Decimal("0.01")
        )
    if "amount_cents" not in result and result.get("amount") is not None:
        result["amount_cents"] = int((Decimal(result["amount"]) * 100).quantize(Decimal("1")))
    if "currency" not in result:
        result["currency"] = currency

    actor_contact = contact_id or lender_contact_id or borrower_contact_id
    if actor_contact is not None:
        await _log_activity(
            pool,
            actor_contact,
            "loan_created",
            (
                f"Created loan: {direction or 'tracked'} "
                f"{result.get('amount') or result.get('amount_cents')}"
            ),
            entity_type="loan",
            entity_id=result["id"],
        )
    return result


async def loan_settle(pool: asyncpg.Pool, loan_id: uuid.UUID) -> dict[str, Any]:
    """Settle a loan."""
    row = await pool.fetchrow(
        """
        UPDATE loans SET settled = true, settled_at = now()
        WHERE id = $1
        RETURNING *
        """,
        loan_id,
    )
    if row is None:
        raise ValueError(f"Loan {loan_id} not found")
    result = dict(row)
    actor_contact = result.get("contact_id") or result.get("lender_contact_id")
    if actor_contact is None:
        actor_contact = result.get("borrower_contact_id")
    if actor_contact is not None:
        await _log_activity(
            pool,
            actor_contact,
            "loan_settled",
            f"Settled loan: {row.get('direction')} {row.get('amount')}",
            entity_type="loan",
            entity_id=loan_id,
        )
    return result


async def loan_list(
    pool: asyncpg.Pool, contact_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    """List loans, optionally filtered by contact."""
    cols = await table_columns(pool, "loans")
    if contact_id is None:
        rows = await pool.fetch("SELECT * FROM loans ORDER BY created_at DESC")
    elif {"lender_contact_id", "borrower_contact_id"}.issubset(cols):
        rows = await pool.fetch(
            """
            SELECT * FROM loans
            WHERE contact_id = $1
               OR lender_contact_id = $1
               OR borrower_contact_id = $1
            ORDER BY created_at DESC
            """,
            contact_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM loans WHERE contact_id = $1 ORDER BY created_at DESC",
            contact_id,
        )
    results = [dict(row) for row in rows]
    for result in results:
        if "amount" not in result and result.get("amount_cents") is not None:
            result["amount"] = (Decimal(result["amount_cents"]) / Decimal(100)).quantize(
                Decimal("0.01")
            )
        if "amount_cents" not in result and result.get("amount") is not None:
            result["amount_cents"] = int((Decimal(result["amount"]) * 100).quantize(Decimal("1")))
        if "currency" not in result:
            result["currency"] = "USD"
    return results

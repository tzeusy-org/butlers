"""Loans — track money lent or borrowed backed by SPO facts.

Each loan is a temporal fact in the facts table (append-only coexistence):
  subject   = contact:{contact_id}:loan:{loan_uuid}
  predicate = 'loan'
  content   = description
  metadata  = {amount_cents, currency, direction, settled, settled_at,
               lender_contact_id, borrower_contact_id}
  valid_at  = created_at (temporal — each loan coexists independently)
  scope     = 'relationship'
  entity_id = contact's entity UUID (resolved via contacts.entity_id)

Status transitions (loan_settle) supersede the previous snapshot via a direct
UPDATE before inserting the new fact, bypassing store_fact's entity-keyed
supersession which would collapse all loans for the same entity into one.

The response shape is backward compatible with the legacy loans table.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

logger = logging.getLogger(__name__)

_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


def _fact_to_loan(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a facts row to the loans API shape."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)

    amount_cents = meta.get("amount_cents")
    currency = meta.get("currency")

    result: dict[str, Any] = {
        "id": row["id"],
        "description": row.get("content", ""),
        "amount_cents": amount_cents,
        "currency": currency,
        "direction": meta.get("direction"),
        "settled": meta.get("settled", False),
        "settled_at": meta.get("settled_at"),
        "lender_contact_id": _to_uuid(meta.get("lender_contact_id")),
        "borrower_contact_id": _to_uuid(meta.get("borrower_contact_id")),
        "contact_id": _to_uuid(meta.get("contact_id")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("created_at"),
    }
    if amount_cents is not None:
        result["amount"] = (Decimal(amount_cents) / Decimal(100)).quantize(Decimal("0.01"))
    return result


def _to_uuid(value: Any) -> uuid.UUID | None:
    """Convert a string or None to UUID."""
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return None


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
    currency: str,
) -> dict[str, Any]:
    """Create a loan record with legacy + spec-compatible fields."""
    from butlers.modules.memory.storage import store_fact

    effective_description = description or "Loan"
    currency = currency.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("currency is required and must be an ISO-4217 three-letter code")

    if amount_cents is None and amount is not None:
        amount_cents = int((amount * 100).quantize(Decimal("1")))
    if amount is None and amount_cents is not None:
        amount = (Decimal(amount_cents) / Decimal(100)).quantize(Decimal("0.01"))

    if lender_contact_id is None and borrower_contact_id is None and contact_id is not None:
        if direction == "lent":
            lender_contact_id = contact_id
        elif direction == "borrowed":
            borrower_contact_id = contact_id

    # Choose the "actor" contact for entity resolution and activity feed
    actor_contact = contact_id or lender_contact_id or borrower_contact_id

    # Resolve actor contact to entity_id — raises ValueError if contact has no entity
    contact_entity_id = (
        await resolve_contact_entity_id(pool, actor_contact) if actor_contact else None
    )

    embedding_engine = _get_embedding_engine()

    # Unique loan subject per creation — distinct UUID ensures each loan is independent
    loan_uuid = uuid.uuid4()
    subject = f"contact:{actor_contact}:loan:{loan_uuid}" if actor_contact else f"loan:{loan_uuid}"
    now = datetime.now(UTC)

    fact_metadata: dict[str, Any] = {
        "settled": False,
        "currency": currency,
    }
    if amount_cents is not None:
        fact_metadata["amount_cents"] = amount_cents
    if direction is not None:
        fact_metadata["direction"] = direction
    if lender_contact_id is not None:
        fact_metadata["lender_contact_id"] = str(lender_contact_id)
    if borrower_contact_id is not None:
        fact_metadata["borrower_contact_id"] = str(borrower_contact_id)
    if contact_id is not None:
        fact_metadata["contact_id"] = str(contact_id)

    # Temporal fact (valid_at=now) so multiple loans for the same entity coexist
    # independently without entity-keyed supersession collapsing them into one.
    fact_id = (
        await store_fact(
            pool,
            subject=subject,
            predicate="loan",
            content=effective_description,
            embedding_engine=embedding_engine,
            permanence="stable",
            scope="relationship",
            entity_id=contact_entity_id,
            valid_at=now,
            metadata=fact_metadata,
        )
    )["id"]

    # The fact is canonical. The shared claim is deliberately best-effort so
    # an unavailable projection can never erase or reject the Relationship
    # record; the idempotent backfill repairs it on the next sweep.
    if amount is not None and direction in {"lent", "borrowed"} and contact_entity_id is not None:
        try:
            from butlers.core.cost_claims import assert_claim

            entity_label = await pool.fetchval(
                "SELECT canonical_name FROM public.entities WHERE id = $1", contact_entity_id
            )
            await assert_claim(
                pool,
                claim_key=f"loan:{fact_id}",
                asserted_by="relationship",
                kind="receivable" if direction == "lent" else "payable",
                direction="inbound" if direction == "lent" else "outbound",
                amount=amount,
                currency=currency,
                counterparty_entity_id=contact_entity_id,
                counterparty_label=entity_label,
                expected_on=None,
                description=effective_description,
                evidence_kind="fact",
                evidence_ref=str(fact_id),
            )
        except Exception:
            logger.warning(
                "Loan claim projection failed; a later backfill will retry", exc_info=True
            )
    result: dict[str, Any] = {
        "id": fact_id,
        "description": effective_description,
        "amount_cents": amount_cents,
        "currency": currency,
        "direction": direction,
        "settled": False,
        "settled_at": None,
        "lender_contact_id": lender_contact_id,
        "borrower_contact_id": borrower_contact_id,
        "contact_id": contact_id,
        "created_at": now,
        "updated_at": now,
    }
    if amount_cents is not None:
        result["amount"] = amount
    else:
        result["amount"] = None

    return result


async def loan_settle(pool: asyncpg.Pool, loan_id: uuid.UUID) -> dict[str, Any]:
    """Settle a loan."""
    from butlers.modules.memory.storage import store_fact

    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT id, subject, content, metadata, entity_id FROM facts"
            " WHERE id = $1 AND scope = 'relationship' AND validity = 'active' FOR UPDATE",
            loan_id,
        )
        if row is None:
            raise ValueError(f"Loan {loan_id} not found")

        meta = row["metadata"] or {}
        if isinstance(meta, str):
            meta = json.loads(meta)

        now = datetime.now(UTC)
        new_metadata = dict(meta)
        new_metadata["settled"] = True
        new_metadata["settled_at"] = now.isoformat()
        embedding_engine = _get_embedding_engine()

        await conn.execute(
            "UPDATE facts SET validity = 'superseded', invalid_at = $2 WHERE id = $1",
            loan_id,
            now,
        )
        new_fact_id = (
            await store_fact(
                _BoundConnectionPool(conn),
                subject=row["subject"],
                predicate="loan",
                content=row["content"],
                embedding_engine=embedding_engine,
                permanence="stable",
                scope="relationship",
                entity_id=row["entity_id"],
                valid_at=now,
                metadata=new_metadata,
            )
        )["id"]

        try:
            from butlers.core.cost_claims import retract_claim

            # Savepoint isolates the optional projection from the canonical
            # fact transition. A missing pre-deploy table must not abort the
            # outer transaction after the successor fact was stored.
            async with conn.transaction():
                claim_id = await conn.fetchval(
                    "SELECT id FROM public.cost_claims WHERE asserted_by = 'relationship' "
                    "AND claim_key = $1 AND superseded_at IS NULL AND retracted_at IS NULL",
                    f"loan:{loan_id}",
                )
                if claim_id is not None:
                    await retract_claim(
                        _BoundConnectionPool(conn), claim_id, "asserter_marked_settled"
                    )
        except Exception:
            logger.warning("Cost-claim projection unavailable while settling loan", exc_info=True)

    result: dict[str, Any] = {
        "id": new_fact_id,
        "description": row["content"],
        "amount_cents": new_metadata.get("amount_cents"),
        "currency": new_metadata.get("currency"),
        "direction": new_metadata.get("direction"),
        "settled": True,
        "settled_at": now,
        "lender_contact_id": _to_uuid(new_metadata.get("lender_contact_id")),
        "borrower_contact_id": _to_uuid(new_metadata.get("borrower_contact_id")),
        "contact_id": _to_uuid(new_metadata.get("contact_id")),
        "created_at": now,
        "updated_at": now,
    }
    if result["amount_cents"] is not None:
        result["amount"] = (Decimal(result["amount_cents"]) / Decimal(100)).quantize(
            Decimal("0.01")
        )

    return result


async def loan_list(
    pool: asyncpg.Pool, contact_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    """List loans, optionally filtered by contact."""
    if contact_id is not None:
        rows = await pool.fetch(
            """
            SELECT id, content, created_at, metadata
            FROM facts
            WHERE subject LIKE $1
              AND predicate = 'loan'
              AND scope = 'relationship'
              AND validity = 'active'
              AND valid_at IS NOT NULL
            ORDER BY created_at DESC
            """,
            f"contact:{contact_id}:loan:%",
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, content, created_at, metadata
            FROM facts
            WHERE predicate = 'loan'
              AND scope = 'relationship'
              AND validity = 'active'
              AND valid_at IS NOT NULL
            ORDER BY created_at DESC
            """
        )
    results = [_fact_to_loan(dict(r)) for r in rows]
    # Ensure amount field is present
    for r in results:
        if "amount" not in r and r.get("amount_cents") is not None:
            r["amount"] = (Decimal(r["amount_cents"]) / Decimal(100)).quantize(Decimal("0.01"))
        if "amount_cents" not in r and r.get("amount") is not None:
            r["amount_cents"] = int((Decimal(r["amount"]) * 100).quantize(Decimal("1")))
    return results


class _BoundConnectionPool:
    """Pool-shaped adapter that keeps memory writes inside an outer transaction."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


async def backfill_loan_cost_claims(pool: asyncpg.Pool) -> dict[str, int]:
    """Idempotently project active loan facts, honestly skipping legacy unknown currency."""
    from butlers.core.cost_claims import assert_claim

    rows = await pool.fetch(
        """
        SELECT f.id, f.content, f.metadata, f.entity_id, e.canonical_name
        FROM facts f
        LEFT JOIN public.entities e ON e.id = f.entity_id
        WHERE f.predicate = 'loan' AND f.scope = 'relationship'
          AND f.validity = 'active' AND f.valid_at IS NOT NULL
        """
    )
    counts = {"asserted": 0, "skipped_missing_currency": 0, "skipped_invalid": 0}
    for row in rows:
        meta = row["metadata"] or {}
        currency = meta.get("currency")
        amount_cents = meta.get("amount_cents")
        direction = meta.get("direction")
        if not currency:
            counts["skipped_missing_currency"] += 1
            continue
        if (
            amount_cents is None
            or direction not in {"lent", "borrowed"}
            or row["entity_id"] is None
        ):
            counts["skipped_invalid"] += 1
            continue
        await assert_claim(
            pool,
            claim_key=f"loan:{row['id']}",
            asserted_by="relationship",
            kind="receivable" if direction == "lent" else "payable",
            direction="inbound" if direction == "lent" else "outbound",
            amount=(Decimal(amount_cents) / Decimal(100)),
            currency=currency,
            counterparty_entity_id=row["entity_id"],
            counterparty_label=row["canonical_name"],
            expected_on=None,
            description=row["content"],
            evidence_kind="fact",
            evidence_ref=str(row["id"]),
        )
        counts["asserted"] += 1
    return counts

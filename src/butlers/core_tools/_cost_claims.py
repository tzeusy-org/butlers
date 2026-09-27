"""MCP tools for the role-fenced shared cost-claim ledger."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from pydantic import Field

from butlers.core import cost_claims
from butlers.core_tools._base import ToolContext


def register_cost_claim_tools(ctx: ToolContext, mcp: Any, _core_tool: Any) -> None:
    @_core_tool("cost_claims")
    async def cost_claim_assert(
        claim_key: Annotated[str, Field(description="Stable natural key in the asserting domain.")],
        kind: Annotated[
            str,
            Field(
                description=(
                    "receivable, payable, shared_expense, committed_cost, or expected_refund."
                )
            ),
        ],
        direction: Annotated[str, Field(description="inbound or outbound.")],
        amount: Annotated[Decimal, Field(gt=0, description="Claim amount; never a float.")],
        currency: Annotated[
            str,
            Field(
                min_length=3, max_length=3, description="Required ISO-4217 code; never inferred."
            ),
        ],
        description: Annotated[str, Field(min_length=1)],
        counterparty_entity_id: str | None = None,
        counterparty_label: str | None = None,
        expected_on: date | None = None,
        evidence_kind: str | None = None,
        evidence_ref: str | None = None,
    ) -> dict[str, Any]:
        if ctx.pool is None:
            raise RuntimeError("Database pool is not available")
        return await cost_claims.assert_claim(
            ctx.pool,
            claim_key=claim_key,
            asserted_by=ctx.butler_name,
            kind=kind,
            direction=direction,
            amount=amount,
            currency=currency,
            counterparty_entity_id=(
                uuid.UUID(counterparty_entity_id) if counterparty_entity_id else None
            ),
            counterparty_label=counterparty_label,
            expected_on=expected_on,
            description=description,
            evidence_kind=evidence_kind,
            evidence_ref=evidence_ref,
        )

    @_core_tool("cost_claims")
    async def cost_claim_amend(
        claim_id: str,
        claim_key: str,
        kind: str,
        direction: str,
        amount: Decimal,
        currency: str,
        description: str,
        counterparty_entity_id: str | None = None,
        counterparty_label: str | None = None,
        expected_on: date | None = None,
        evidence_kind: str | None = None,
        evidence_ref: str | None = None,
    ) -> dict[str, Any]:
        if ctx.pool is None:
            raise RuntimeError("Database pool is not available")
        return await cost_claims.amend_claim(
            ctx.pool,
            uuid.UUID(claim_id),
            claim_key=claim_key,
            asserted_by=ctx.butler_name,
            kind=kind,
            direction=direction,
            amount=amount,
            currency=currency,
            counterparty_entity_id=(
                uuid.UUID(counterparty_entity_id) if counterparty_entity_id else None
            ),
            counterparty_label=counterparty_label,
            expected_on=expected_on,
            description=description,
            evidence_kind=evidence_kind,
            evidence_ref=evidence_ref,
        )

    @_core_tool("cost_claims")
    async def cost_claim_retract(claim_id: str, reason: str) -> dict[str, Any]:
        if ctx.pool is None:
            raise RuntimeError("Database pool is not available")
        return await cost_claims.retract_claim(ctx.pool, uuid.UUID(claim_id), reason)

    @_core_tool("cost_claims")
    async def cost_claim_list_mine() -> list[dict[str, Any]]:
        if ctx.pool is None:
            raise RuntimeError("Database pool is not available")
        return await cost_claims.list_claims(ctx.pool, asserted_by=ctx.butler_name)

    @_core_tool("cost_claims")
    async def cost_claim_resolve(
        claim_id: str,
        state: str,
        matched_amount: Decimal | None = None,
        matched_currency: str | None = None,
        match_refs: list[str] | None = None,
        unmatched_reason: str | None = None,
        unverifiable_reason: str | None = None,
    ) -> dict[str, Any]:
        if ctx.pool is None:
            raise RuntimeError("Database pool is not available")
        return await cost_claims.resolve_claim(
            ctx.pool,
            uuid.UUID(claim_id),
            state=state,
            matched_amount=matched_amount,
            matched_currency=matched_currency,
            match_refs=match_refs,
            unmatched_reason=unmatched_reason,
            unverifiable_reason=unverifiable_reason,
        )

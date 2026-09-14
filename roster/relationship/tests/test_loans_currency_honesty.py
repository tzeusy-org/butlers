"""Currency is asserted data on a loan; legacy absence remains unknown."""

from __future__ import annotations

from uuid import uuid4

import pytest

from butlers.tools.relationship.loans import _fact_to_loan, loan_create


@pytest.mark.asyncio
async def test_loan_create_requires_currency() -> None:
    with pytest.raises(TypeError, match="currency"):
        await loan_create(object(), contact_id=uuid4(), amount_cents=100)  # type: ignore[arg-type,call-arg]


def test_legacy_currency_less_fact_remains_unknown() -> None:
    loan = _fact_to_loan(
        {
            "id": uuid4(),
            "content": "legacy",
            "metadata": {"amount_cents": 100, "direction": "lent"},
        }
    )
    assert loan["currency"] is None

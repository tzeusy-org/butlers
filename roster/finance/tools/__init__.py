"""Finance butler tools — transactions, subscriptions, bills, and spending summaries.

Re-exports all public symbols from the finance tool sub-modules so that
``from butlers.tools.finance import X`` works as a stable public API.

Modules implemented by parallel branches are imported with try/except guards
so that this package remains importable during staged roll-out.  Once all
branches are merged the guards can be removed.
"""

from __future__ import annotations

# --- Always-available: implemented on this branch ---
from butlers.tools.finance.spending import (
    VALID_GROUP_BY_MODES,
    spending_summary,
)
from butlers.tools.finance.transactions import (
    list_transactions,
    record_transaction,
)

try:
    from butlers.tools.finance.subscriptions import (  # type: ignore[attr-defined]
        track_subscription,
    )
except (ImportError, AttributeError):
    track_subscription = None  # type: ignore[assignment]

try:
    from butlers.tools.finance.bills import (  # type: ignore[attr-defined]
        track_bill,
        upcoming_bills,
    )
except (ImportError, AttributeError):
    track_bill = None  # type: ignore[assignment]
    upcoming_bills = None  # type: ignore[assignment]

__all__ = [
    # spending
    "VALID_GROUP_BY_MODES",
    "spending_summary",
    # transactions (butlers-ee32.5)
    "record_transaction",
    "list_transactions",
    # subscriptions (butlers-ee32.6)
    "track_subscription",
    # bills (butlers-ee32.6)
    "track_bill",
    "upcoming_bills",
]

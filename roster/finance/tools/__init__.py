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

# --- SPO fact-layer (bu-ddb.4) ---
try:
    from butlers.tools.finance.facts import (  # type: ignore[attr-defined]
        list_transaction_facts,
        record_transaction_fact,
        spending_summary_facts,
        track_account_fact,
        track_bill_fact,
        track_subscription_fact,
    )
except (ImportError, AttributeError):
    list_transaction_facts = None  # type: ignore[assignment]
    record_transaction_fact = None  # type: ignore[assignment]
    spending_summary_facts = None  # type: ignore[assignment]
    track_account_fact = None  # type: ignore[assignment]
    track_bill_fact = None  # type: ignore[assignment]
    track_subscription_fact = None  # type: ignore[assignment]

# --- Bulk ingestion (bu-8c8c) ---
try:
    from butlers.tools.finance.facts import (  # type: ignore[attr-defined]
        bulk_record_transactions,
    )
except (ImportError, AttributeError):
    bulk_record_transactions = None  # type: ignore[assignment]

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
    # SPO fact-layer (bu-ddb.4)
    "record_transaction_fact",
    "list_transaction_facts",
    "track_account_fact",
    "track_subscription_fact",
    "track_bill_fact",
    "spending_summary_facts",
    # Bulk ingestion (bu-8c8c)
    "bulk_record_transactions",
]

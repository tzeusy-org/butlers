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

# --- Transaction CRUD extensions (finance-intelligence) ---
try:
    from butlers.tools.finance.transactions import (  # type: ignore[attr-defined]
        bulk_recategorize,
        delete_transaction,
        merge_duplicates,
        split_transaction,
        update_transaction,
    )
except (ImportError, AttributeError):
    update_transaction = None  # type: ignore[assignment]
    delete_transaction = None  # type: ignore[assignment]
    merge_duplicates = None  # type: ignore[assignment]
    split_transaction = None  # type: ignore[assignment]
    bulk_recategorize = None  # type: ignore[assignment]

# --- Historical data import (finance-intelligence) ---
try:
    from butlers.tools.finance.data_import import (  # type: ignore[attr-defined]
        import_transactions,
    )
except (ImportError, AttributeError):
    import_transactions = None  # type: ignore[assignment]

# --- Merchant pattern recognition (finance-intelligence) ---
try:
    from butlers.tools.finance.pattern_recognition import (  # type: ignore[attr-defined]
        detect_recurring,
        learn_merchant_categories,
        predict_bills,
        recall_merchant_mappings,
        suggest_categories,
    )
except (ImportError, AttributeError):
    learn_merchant_categories = None  # type: ignore[assignment]
    suggest_categories = None  # type: ignore[assignment]
    recall_merchant_mappings = None  # type: ignore[assignment]
    detect_recurring = None  # type: ignore[assignment]
    predict_bills = None  # type: ignore[assignment]

# --- Anomaly detection and baselines (finance-intelligence) ---
try:
    from butlers.tools.finance.anomaly_detection import (  # type: ignore[attr-defined]
        anomaly_scan,
        compute_baselines,
        detect_duplicates,
    )
except (ImportError, AttributeError):
    compute_baselines = None  # type: ignore[assignment]
    anomaly_scan = None  # type: ignore[assignment]
    detect_duplicates = None  # type: ignore[assignment]

# --- Budget management and spending analytics (finance-intelligence) ---
try:
    from butlers.tools.finance.budgets import (  # type: ignore[attr-defined]
        budget_list,
        budget_remove,
        budget_set,
        budget_status,
        spending_forecast,
        spending_trends,
    )
except (ImportError, AttributeError):
    budget_set = None  # type: ignore[assignment]
    budget_list = None  # type: ignore[assignment]
    budget_remove = None  # type: ignore[assignment]
    budget_status = None  # type: ignore[assignment]
    spending_trends = None  # type: ignore[assignment]
    spending_forecast = None  # type: ignore[assignment]

# --- Financial overview tools (finance-intelligence) ---
try:
    from butlers.tools.finance.overview import (  # type: ignore[attr-defined]
        cash_flow,
        flag_tax_deductible,
        net_worth_history,
        net_worth_snapshot,
        subscription_audit,
    )
except (ImportError, AttributeError):
    net_worth_snapshot = None  # type: ignore[assignment]
    net_worth_history = None  # type: ignore[assignment]
    cash_flow = None  # type: ignore[assignment]
    subscription_audit = None  # type: ignore[assignment]
    flag_tax_deductible = None  # type: ignore[assignment]

# --- Alert system (finance-intelligence) ---
try:
    from butlers.tools.finance.alerts import (  # type: ignore[attr-defined]
        alert_configure,
        alert_list,
        detect_price_changes,
    )
except (ImportError, AttributeError):
    alert_configure = None  # type: ignore[assignment]
    alert_list = None  # type: ignore[assignment]
    detect_price_changes = None  # type: ignore[assignment]

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
    # Transaction CRUD extensions (finance-intelligence)
    "update_transaction",
    "delete_transaction",
    "merge_duplicates",
    "split_transaction",
    "bulk_recategorize",
    # Historical data import (finance-intelligence)
    "import_transactions",
    # Merchant pattern recognition (finance-intelligence)
    "learn_merchant_categories",
    "suggest_categories",
    "recall_merchant_mappings",
    "detect_recurring",
    "predict_bills",
    # Anomaly detection and baselines (finance-intelligence)
    "compute_baselines",
    "anomaly_scan",
    "detect_duplicates",
    # Budget management and spending analytics (finance-intelligence)
    "budget_set",
    "budget_list",
    "budget_remove",
    "budget_status",
    "spending_trends",
    "spending_forecast",
    # Financial overview tools (finance-intelligence)
    "net_worth_snapshot",
    "net_worth_history",
    "cash_flow",
    "subscription_audit",
    "flag_tax_deductible",
    # Alert system (finance-intelligence)
    "alert_configure",
    "alert_list",
    "detect_price_changes",
]

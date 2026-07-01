"""Finance module — wires finance domain tools into the butler's MCP server.

Registers 6 MCP tools that delegate to the existing implementations in
``butlers.tools.finance``. The tool closures strip ``pool`` from the
MCP-visible signature and inject it from module state at call time.

Type conversions at the MCP boundary:
- ``posted_at``: accepted as ISO-8601 string, converted to ``datetime`` via
  ``fromisoformat()`` before passing to the implementation.
- Amount fields: accepted as ``float`` from MCP, implementations accept
  ``Decimal | float | int`` so no conversion needed.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module, ToolGroupMixin

logger = logging.getLogger(__name__)


class FinanceModuleConfig(ToolGroupMixin, BaseModel):
    """Configuration for the Finance module.

    Tool groups
    -----------
    core : record_transaction, list_transactions, update_transaction,
           delete_transaction, list_distinct_merchants
    facts : list_transaction_facts, track_account_fact,
            track_subscription_fact, spending_summary_facts
    bulk : bulk_record_transactions, bulk_update_transactions,
           bulk_recategorize, import_transactions, import_transactions_from_file,
           merge_duplicates, split_transaction
    subscriptions : track_subscription, subscription_audit, detect_recurring,
                    detect_price_changes
    bills : track_bill, upcoming_bills, predict_bills
    budgets : budget_set, budget_list, budget_remove, budget_status
    analytics : spending_summary, spending_trends, spending_forecast,
                net_worth_snapshot, net_worth_history, cash_flow,
                compute_baselines, anomaly_scan, detect_duplicates
    intelligence : learn_merchant_categories, suggest_categories,
                   recall_merchant_mappings, flag_tax_deductible,
                   alert_configure, alert_list
    """


class FinanceModule(Module):
    """Finance module providing 6 MCP tools for transactions, subscriptions,
    bills, and spending analysis.
    """

    def __init__(self) -> None:
        self._db: Any = None
        self.blob_store: Any = None
        # Optional async callable for progress notifications during large imports.
        # Signature matches the butler's notify() MCP tool:
        #   notify_fn(channel, message, intent) -> Any
        # Set by the daemon or tests; None disables progress reporting.
        self.notify_fn: Any = None

    @property
    def name(self) -> str:
        return "finance"

    @property
    def config_schema(self) -> type[BaseModel]:
        return FinanceModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None  # finance tables already exist via separate migrations

    async def on_startup(
        self,
        config: Any,
        db: Any,
        credential_store: Any = None,
        blob_store: Any = None,
    ) -> None:
        """Store the Database reference and blob store for later pool/storage access."""
        self._db = db
        self.blob_store = blob_store

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None
        self.blob_store = None

    def _get_pool(self):
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("FinanceModule not initialised -- no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        """Register all finance MCP tools."""
        self._db = db

        from .tools import register_tools

        register_tools(mcp, self, config)

"""Finance butler overview tools — net worth, cash flow, subscription audit, tax flagging.

Provides five high-level analytical functions:
- net_worth_snapshot: Record a point-in-time account balance into finance.balance_snapshots.
- net_worth_history: Return monthly net worth history with carry-forward for missing months.
- cash_flow: Aggregate credits vs debits by period with optional category breakdown.
- subscription_audit: Combine tracked subscriptions and detected recurring charges,
  computing annual cost projections and changes since last audit.
- flag_tax_deductible: Query transactions for a tax year and cross-reference
  finance.categories.is_tax_relevant to flag potential deductions.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.core.expected_signals import upsert_expected_signal
from butlers.tools.finance._helpers import _row_to_dict
from butlers.tools.finance.expected_signals import resolve_complete_signal_source

logger = logging.getLogger(__name__)

# Minimum number of characters a subscription service name must have to be
# considered for merchant matching.  Names shorter than this are skipped to
# avoid false positives (e.g. "TV" matching "DIRECT TV SPORTS", "FX Cable").
_MIN_MERCHANT_MATCH_LEN: int = 3

# Frequency-to-annual multiplier for annual cost projection.
_ANNUAL_MULTIPLIER: dict[str, int] = {
    "weekly": 52,
    "monthly": 12,
    "quarterly": 4,
    "yearly": 1,
    "custom": 12,  # fallback for unknown custom frequencies
}

_VALID_PERIODS = {"monthly", "weekly", "yearly"}

# Default tax-relevant categories if finance.categories table is absent/empty.
_DEFAULT_TAX_CATEGORIES: dict[str, str] = {
    "medical": "medical_expense",
    "charitable": "charitable_donation",
    "charity": "charitable_donation",
    "donation": "charitable_donation",
    "education": "education_expense",
    "home_office": "home_office",
    "business_expense": "business_expense",
    "business": "business_expense",
    "professional_services": "business_expense",
}

_TAX_DISCLAIMER = (
    "This list is generated automatically for informational purposes only. "
    "It does not constitute tax advice. Tax deductibility depends on your jurisdiction, "
    "filing status, and individual circumstances. Please review flagged transactions "
    "with a qualified tax professional before claiming any deductions."
)

# Pre-compiled patterns for _infer_account_type — module-level to avoid per-call overhead.
_CHECKING_RE = re.compile(r"\bchecking\b")
# Credit-card and payment-network markers; bare "credit" is intentionally excluded
# to avoid misclassifying "credit union" accounts.
_CREDIT_RE = re.compile(
    r"\bcard\b"  # "Credit Card", "Debit Card"
    r"|\bcc\b"  # common abbreviation for credit card
    r"|\bvisa\b"
    r"|\bmastercard\b"
    r"|\bamex\b"
    r"|\bcredit\s+card\b"  # explicit two-word phrase
)
_SAVINGS_RE = re.compile(r"\bsavings\b|\bsave\b|\bhsa\b")
_INVESTMENT_RE = re.compile(
    r"\binvest(?:ment|ing)?\b"  # "investment", "investing" — bounded to avoid "investigation"
    r"|\bira\b"
    r"|\b401k\b"
    r"|\bbrokerage\b"
    r"|\broth\b"
    r"|\bfidelity\b"
)


def _today() -> date:
    return date.today()


def _infer_account_type(name: str, default: str = "checking") -> str:
    """Infer the account type from an account name using word-boundary heuristics.

    Rules are applied in priority order (most-specific first) to avoid
    substring false-positives.  For example, "Credit Union Checking" contains
    the substring "credit" but should be classified as "checking", not "credit".

    Parameters
    ----------
    name:
        Account name string (e.g. "Credit Union Checking", "Chase Visa Card").
    default:
        Fallback type when no keyword matches (default ``"checking"``).

    Returns
    -------
    str
        One of: ``"checking"``, ``"credit"``, ``"savings"``, ``"investment"``,
        or *default*.
    """
    lower = name.lower()

    # 1. "checking" — checked first so that "Credit Union Checking" resolves
    #    to checking before the credit-card patterns fire.
    if _CHECKING_RE.search(lower):
        return "checking"

    # 2. Credit-card and payment-network markers.
    if _CREDIT_RE.search(lower):
        return "credit"

    # 3. Savings / HSA.
    if _SAVINGS_RE.search(lower):
        return "savings"

    # 4. Investment / brokerage.
    if _INVESTMENT_RE.search(lower):
        return "investment"

    return default


def _as_of_date_or_today(as_of_date: str | date | None) -> date:
    """Normalise as_of_date to a date object, defaulting to today."""
    if as_of_date is None:
        return _today()
    if isinstance(as_of_date, date):
        return as_of_date
    return date.fromisoformat(str(as_of_date))


def _month_label(year: int, month: int) -> str:
    """Return a YYYY-MM label for the given year and month."""
    return f"{year:04d}-{month:02d}"


def _months_ago(reference: date, n: int) -> date:
    """Return the first day of the month that is n months before reference's month."""
    month = reference.month - n
    year = reference.year
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


async def _get_or_create_account(
    pool: asyncpg.Pool,
    name: str,
    institution: str,
    account_type: str = "checking",
    currency: str = "USD",
) -> str:
    """Return the UUID string for an account, creating one if it doesn't exist.

    Matches on (institution, name).  Account type defaults to 'checking';
    credit accounts store negative balances representing debt.
    """
    row = await pool.fetchrow(
        "SELECT id FROM accounts WHERE institution = $1 AND name = $2 LIMIT 1",
        institution,
        name,
    )
    if row is not None:
        return str(row["id"])

    # Infer type from name when the caller did not supply an explicit type.
    # Delegates to _infer_account_type() which uses word-boundary regex to
    # avoid substring false-positives (e.g. "credit union" != credit card).
    inferred_type = _infer_account_type(name, default=account_type)

    new_row = await pool.fetchrow(
        """
        INSERT INTO accounts (institution, type, name, currency)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        institution,
        inferred_type,
        name,
        currency.upper(),
    )
    return str(new_row["id"])


# ---------------------------------------------------------------------------
# 8.1  net_worth_snapshot
# ---------------------------------------------------------------------------


async def net_worth_snapshot(
    pool: asyncpg.Pool,
    account: str,
    institution: str,
    balance: float,
    currency: str = "USD",
    as_of_date: str | date | None = None,
) -> dict[str, Any]:
    """Record a point-in-time account balance snapshot.

    Upserts into ``finance.balance_snapshots`` using the ``(account_id, as_of_date)``
    unique constraint.  If a snapshot for the same account and date already exists,
    the balance is updated.  Credit account balances should be passed as negative
    values to represent debt.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    account:
        Account name or identifier (e.g. ``"Checking"``, ``"Credit Card"``).
    institution:
        Financial institution name (e.g. ``"Chase"``, ``"Fidelity"``).
    balance:
        Account balance.  Positive = asset, negative = liability (debt).
    currency:
        ISO-4217 currency code (default ``"USD"``).
    as_of_date:
        Snapshot date (ISO string or ``date`` object).  Defaults to today.

    Returns
    -------
    dict
        Snapshot record with keys: ``id``, ``account_id``, ``account``,
        ``institution``, ``balance``, ``currency``, ``as_of_date``, ``source``.
    """
    snapshot_date = _as_of_date_or_today(as_of_date)
    account_id = await _get_or_create_account(pool, account, institution, currency=currency)
    stored_balance = Decimal(str(balance))

    row = await pool.fetchrow(
        """
        INSERT INTO balance_snapshots (account_id, balance, currency, as_of_date, source)
        VALUES ($1::uuid, $2, $3, $4, 'manual')
        ON CONFLICT ON CONSTRAINT uq_balance_snapshot_account_date
        DO UPDATE SET
            balance    = EXCLUDED.balance,
            currency   = EXCLUDED.currency,
            updated_at = now()
        RETURNING *
        """,
        account_id,
        stored_balance,
        currency.upper(),
        snapshot_date,
    )

    result = _row_to_dict(row)
    result["account"] = account
    result["institution"] = institution
    result["balance"] = str(row["balance"])
    return result


# ---------------------------------------------------------------------------
# 8.2  net_worth_history
# ---------------------------------------------------------------------------


async def net_worth_history(
    pool: asyncpg.Pool,
    months: int = 12,
) -> dict[str, Any]:
    """Retrieve monthly net worth history with carry-forward for missing months.

    Queries ``finance.balance_snapshots`` joined with ``finance.accounts``,
    returning the most recent snapshot per account per month over the
    requested period.  When an account has no snapshot for a month, the most
    recent prior snapshot is carried forward (marked ``carried_forward=True``).

    Time complexity: O(accounts*months) instead of O(accounts*months^2) by
    using a running balance tracker that carries forward per-account state
    as we iterate forward through months.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    months:
        Number of months of history to return (default 12, max 120).

    Returns
    -------
    dict
        ``{snapshots: [{period, accounts: [...], total_assets, total_liabilities,
        net_worth}], as_of}``

        Each account entry: ``{account, institution, balance, currency,
        carried_forward}``.
    """
    months = max(1, min(months, 120))
    today = _today()

    # Build the list of period labels (YYYY-MM) from oldest to newest.
    periods: list[str] = []
    for i in range(months - 1, -1, -1):
        ref = _months_ago(today, i)
        periods.append(_month_label(ref.year, ref.month))

    if not periods:
        return {"snapshots": [], "as_of": datetime.now(UTC).isoformat()}

    # Get first day of the oldest period and last day of current month.
    oldest = date.fromisoformat(periods[0] + "-01")
    # End of current month (inclusive): first day of next month minus one day.
    if today.month == 12:
        end_of_month = date(today.year + 1, 1, 1) - timedelta(days=1)
    else:
        end_of_month = date(today.year, today.month + 1, 1) - timedelta(days=1)

    # Fetch all snapshots in the date range, joined with accounts for names.
    rows = await pool.fetch(
        """
        SELECT
            bs.id,
            bs.account_id,
            bs.balance,
            bs.currency,
            bs.as_of_date,
            a.name     AS account_name,
            a.institution
        FROM balance_snapshots bs
        JOIN accounts a ON a.id = bs.account_id
        WHERE bs.as_of_date >= $1 AND bs.as_of_date <= $2
        ORDER BY bs.as_of_date ASC
        """,
        oldest,
        end_of_month,
    )

    # Also fetch snapshots BEFORE the range to seed carry-forward.
    seed_rows = await pool.fetch(
        """
        SELECT DISTINCT ON (account_id)
            bs.account_id,
            bs.balance,
            bs.currency,
            bs.as_of_date,
            a.name        AS account_name,
            a.institution
        FROM balance_snapshots bs
        JOIN accounts a ON a.id = bs.account_id
        WHERE bs.as_of_date < $1
        ORDER BY account_id, bs.as_of_date DESC
        """,
        oldest,
    )

    # Group actual snapshots by (account_id, period_label).
    # We take the LAST snapshot in each month for each account.
    snapshots_by_account_period: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        account_id = str(row["account_id"])
        period = _month_label(row["as_of_date"].year, row["as_of_date"].month)
        if account_id not in snapshots_by_account_period:
            snapshots_by_account_period[account_id] = {}
        # Later rows overwrite earlier ones in the same period (we get the latest).
        snapshots_by_account_period[account_id][period] = {
            "account": row["account_name"],
            "institution": row["institution"],
            "balance": Decimal(str(row["balance"])),
            "currency": row["currency"],
            "carried_forward": False,
        }

    # Discover all account IDs that appear in any snapshot.
    account_ids: set[str] = set(snapshots_by_account_period.keys())
    # Also include accounts from seed rows (they may have pre-range snapshots).
    seed_by_account: dict[str, dict[str, Any]] = {}
    for row in seed_rows:
        account_id = str(row["account_id"])
        account_ids.add(account_id)
        seed_by_account[account_id] = {
            "account": row["account_name"],
            "institution": row["institution"],
            "balance": Decimal(str(row["balance"])),
            "currency": row["currency"],
            "carried_forward": True,
        }

    # Apply carry-forward logic with O(accounts*months) complexity.
    # Track the last known balance for each account as we iterate forward.
    last_balance_by_account: dict[str, dict[str, Any]] = {}

    # Initialize with seed values (pre-range snapshots).
    for account_id, entry in seed_by_account.items():
        last_balance_by_account[account_id] = dict(entry)

    result_snapshots: list[dict[str, Any]] = []
    for period in periods:
        period_accounts: list[dict[str, Any]] = []
        total_assets = Decimal("0")
        total_liabilities = Decimal("0")
        currency_totals: dict[str, dict[str, Any]] = {}

        for account_id in sorted(account_ids):
            acct_periods = snapshots_by_account_period.get(account_id, {})

            # If we have a snapshot for this period, use it.
            if period in acct_periods:
                entry = dict(acct_periods[period])
                last_balance_by_account[account_id] = entry
            else:
                # No snapshot this period. Use the last known balance (carry forward).
                if account_id in last_balance_by_account:
                    entry = dict(last_balance_by_account[account_id])
                    entry["carried_forward"] = True
                else:
                    # No prior data for this account at all; skip.
                    continue

            balance = entry["balance"]
            currency = str(entry["currency"])
            currency_total = currency_totals.setdefault(
                currency,
                {
                    "currency": currency,
                    "total_assets": Decimal("0"),
                    "total_liabilities": Decimal("0"),
                },
            )
            if balance >= 0:
                total_assets += balance
                currency_total["total_assets"] += balance
            else:
                total_liabilities += abs(balance)
                currency_total["total_liabilities"] += abs(balance)

            period_accounts.append(
                {
                    "account": entry["account"],
                    "institution": entry["institution"],
                    "balance": str(balance),
                    "currency": entry["currency"],
                    "carried_forward": entry["carried_forward"],
                }
            )

        by_currency = []
        for currency in sorted(currency_totals):
            totals = currency_totals[currency]
            assets = totals["total_assets"]
            liabilities = totals["total_liabilities"]
            by_currency.append(
                {
                    "currency": currency,
                    "total_assets": str(assets),
                    "total_liabilities": str(liabilities),
                    "net_worth": str(assets - liabilities),
                }
            )
        degraded = len(by_currency) > 1
        result_snapshots.append(
            {
                "period": period,
                "accounts": period_accounts,
                "total_assets": str(total_assets),
                "total_liabilities": str(total_liabilities),
                "net_worth": str(total_assets - total_liabilities),
                "currency": by_currency[0]["currency"] if len(by_currency) == 1 else None,
                "by_currency": by_currency,
                "legacy_aggregate_degraded": degraded,
                "degraded_reason": "multiple_currencies_unconverted" if degraded else None,
            }
        )

    return {
        "snapshots": result_snapshots,
        "as_of": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# 8.3  cash_flow
# ---------------------------------------------------------------------------


async def cash_flow(
    pool: asyncpg.Pool,
    period: str = "monthly",
    months: int = 6,
    breakdown: bool = False,
) -> dict[str, Any]:
    """Aggregate income vs. expenses by period.

    Queries ``finance.transactions WHERE deleted_at IS NULL`` separating
    credits (income/refunds) from debits (expenses).  Computes net and
    savings_rate per period.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    period:
        Aggregation period.  One of: ``"monthly"``, ``"weekly"``, ``"yearly"``.
    months:
        Number of months of history to include (default 6, max 60).
    breakdown:
        If ``True``, include per-category income/expense breakdown per period.

    Returns
    -------
    dict
        ``{periods: [{period, income, expenses, net, savings_rate,
        categories (if breakdown)}], avg_net, avg_savings_rate, as_of}``
    """
    if period not in _VALID_PERIODS:
        raise ValueError(
            f"Unsupported period {period!r}. Must be one of: {', '.join(sorted(_VALID_PERIODS))}"
        )
    months = max(1, min(months, 60))

    today = _today()
    # Start from the first day of (months) months ago.
    start_date = _months_ago(today, months - 1)
    # End of current month.
    if today.month == 12:
        end_date = date(today.year + 1, 1, 1) - timedelta(days=1)
    else:
        end_date = date(today.year, today.month + 1, 1) - timedelta(days=1)

    # Check if transactions table has deleted_at column.
    has_deleted_at = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'transactions' AND column_name = 'deleted_at'
              AND table_schema = current_schema()
        )
        """
    )
    deleted_filter = "AND deleted_at IS NULL" if has_deleted_at else ""

    if period == "monthly":
        period_expr = "TO_CHAR(DATE_TRUNC('month', posted_at), 'YYYY-MM')"
    elif period == "weekly":
        period_expr = "TO_CHAR(DATE_TRUNC('week', posted_at), 'IYYY-\"W\"IW')"
    else:  # yearly
        period_expr = "TO_CHAR(DATE_TRUNC('year', posted_at), 'YYYY')"

    # Main aggregation: income and expenses per period.
    rows = await pool.fetch(
        f"""
        SELECT
            {period_expr}                                             AS period_key,
            currency,
            COALESCE(SUM(amount) FILTER (WHERE direction = 'credit'), 0) AS income,
            COALESCE(SUM(amount) FILTER (WHERE direction = 'debit'),  0) AS expenses
        FROM transactions
        WHERE posted_at::date >= $1
          AND posted_at::date <= $2
          AND COALESCE(metadata->>'inferred_category', category)
              NOT IN ('transfer', 'uncategorized')
          {deleted_filter}
        GROUP BY period_key, currency
        ORDER BY period_key ASC, currency ASC
        """,
        start_date,
        end_date,
    )

    period_data: dict[str, dict[str, Any]] = {}
    for row in rows:
        income = Decimal(str(row["income"]))
        expenses = Decimal(str(row["expenses"]))
        net = income - expenses
        savings_rate = None
        if income > 0:
            savings_rate = str(round((net / income) * 100, 2))
        period_entry = period_data.setdefault(
            row["period_key"],
            {
                "period": row["period_key"],
                "income": Decimal("0"),
                "expenses": Decimal("0"),
                "by_currency": [],
            },
        )
        period_entry["income"] += income
        period_entry["expenses"] += expenses
        period_entry["by_currency"].append(
            {
                "currency": str(row["currency"]),
                "income": str(income),
                "expenses": str(expenses),
                "net": str(net),
                "savings_rate": savings_rate,
            }
        )

    for data in period_data.values():
        income = data["income"]
        expenses = data["expenses"]
        net = income - expenses
        data["income"] = str(income)
        data["expenses"] = str(expenses)
        data["net"] = str(net)
        data["savings_rate"] = str(round((net / income) * 100, 2)) if income > 0 else None
        data["currency"] = (
            data["by_currency"][0]["currency"] if len(data["by_currency"]) == 1 else None
        )
        data["legacy_aggregate_degraded"] = len(data["by_currency"]) > 1
        data["degraded_reason"] = (
            "multiple_currencies_unconverted" if len(data["by_currency"]) > 1 else None
        )

    # Category breakdown (optional).
    if breakdown:
        cat_rows = await pool.fetch(
            f"""
            SELECT
                {period_expr}                                             AS period_key,
                currency,
                category,
                COALESCE(SUM(amount) FILTER (WHERE direction = 'credit'), 0) AS income,
                COALESCE(SUM(amount) FILTER (WHERE direction = 'debit'),  0) AS expenses
            FROM transactions
            WHERE posted_at::date >= $1
              AND posted_at::date <= $2
              AND COALESCE(metadata->>'inferred_category', category)
                  NOT IN ('transfer', 'uncategorized')
              {deleted_filter}
            GROUP BY period_key, currency, category
            ORDER BY period_key ASC, currency ASC, expenses DESC
            """,
            start_date,
            end_date,
        )
        cat_by_period: dict[str, dict[str, dict[str, Any]]] = {}
        for row in cat_rows:
            pk = row["period_key"]
            income = Decimal(str(row["income"]))
            expenses = Decimal(str(row["expenses"]))
            category_entry = cat_by_period.setdefault(pk, {}).setdefault(
                row["category"],
                {"category": row["category"], "income": Decimal("0"), "expenses": Decimal("0")},
            )
            category_entry["income"] += income
            category_entry["expenses"] += expenses
            currency_entry = next(
                item
                for item in period_data[pk]["by_currency"]
                if item["currency"] == str(row["currency"])
            )
            currency_entry.setdefault("categories", []).append(
                {
                    "category": row["category"],
                    "income": str(income),
                    "expenses": str(expenses),
                    "net": str(income - expenses),
                }
            )
        for pk, data in period_data.items():
            data["categories"] = [
                {
                    "category": item["category"],
                    "income": str(item["income"]),
                    "expenses": str(item["expenses"]),
                    "net": str(item["income"] - item["expenses"]),
                }
                for item in cat_by_period.get(pk, {}).values()
            ]

    # Compute averages.
    period_list = list(period_data.values())
    if period_list:
        total_net = sum(Decimal(p["net"]) for p in period_list)
        avg_net = str(round(total_net / len(period_list), 2))
        rates = [Decimal(p["savings_rate"]) for p in period_list if p["savings_rate"] is not None]
        avg_savings_rate = str(round(sum(rates) / len(rates), 2)) if rates else None
    else:
        avg_net = "0"
        avg_savings_rate = None

    currency_periods: dict[str, list[dict[str, Any]]] = {}
    for item in period_list:
        for currency_item in item["by_currency"]:
            currency_periods.setdefault(currency_item["currency"], []).append(
                {"period": item["period"], **currency_item}
            )
    by_currency = []
    for currency in sorted(currency_periods):
        entries = currency_periods[currency]
        total_net = sum((Decimal(entry["net"]) for entry in entries), Decimal("0"))
        rates = [
            Decimal(entry["savings_rate"]) for entry in entries if entry["savings_rate"] is not None
        ]
        by_currency.append(
            {
                "currency": currency,
                "periods": entries,
                "avg_net": str(round(total_net / len(entries), 2)),
                "avg_savings_rate": (str(round(sum(rates) / len(rates), 2)) if rates else None),
            }
        )
    degraded = len(by_currency) > 1

    return {
        "periods": period_list,
        "avg_net": avg_net,
        "avg_savings_rate": avg_savings_rate,
        "currency": by_currency[0]["currency"] if len(by_currency) == 1 else None,
        "by_currency": by_currency,
        "legacy_aggregate_degraded": degraded,
        "degraded_reason": "multiple_currencies_unconverted" if degraded else None,
        "as_of": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# 8.4  subscription_audit
# ---------------------------------------------------------------------------


async def subscription_audit(
    pool: asyncpg.Pool,
) -> dict[str, Any]:
    """Audit all subscriptions — tracked and auto-detected recurring charges.

    Combines:
    - All rows from ``finance.subscriptions`` (explicit tracking).
    - All patterns from ``finance.recurring_groups`` that are not yet explicitly
      tracked (detected but untracked).

    Computes annual cost projections and detects changes since the last audit.

    Returns
    -------
    dict
        ``{entries, total_annual_cost, changes_since_last_audit,
        last_audit_date, as_of}``

        Each entry: ``{service, amount, currency, frequency, annual_cost,
        status, last_charge_date, next_expected_date}``.
    """
    entries: list[dict[str, Any]] = []
    total_annual_cost = Decimal("0")
    annual_cost_by_currency: dict[str, Decimal] = {}

    # --- Tracked subscriptions ---
    has_subscriptions = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'subscriptions'
              AND table_schema = current_schema()
        )
        """
    )
    if has_subscriptions:
        # Fetch all active/paused subscriptions in one query.
        sub_rows = await pool.fetch(
            """
            SELECT id, service, amount, currency, frequency, status, next_renewal,
                   metadata, updated_at
            FROM subscriptions
            WHERE status IN ('active', 'paused')
            ORDER BY service ASC
            """
        )

        # Pre-fetch one row per unique lower-case merchant (the latest posted_at)
        # using a window function.  This avoids:
        #   1. The N+1 pattern (one query per subscription).
        #   2. A non-SARGable SQL LIKE with a leading wildcard that cannot use
        #      a btree index on the merchant column.
        #   3. False-positive matches from very short service names (e.g. "TV").
        # We also enforce _MIN_MERCHANT_MATCH_LEN: service names shorter than
        # this are not matched against transactions to reduce false positives.
        txn_rows = await pool.fetch(
            """
            SELECT merchant_lower, posted_at
            FROM (
                SELECT lower(merchant) AS merchant_lower, posted_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY lower(merchant)
                           ORDER BY posted_at DESC
                       ) AS rn
                FROM transactions
            ) t
            WHERE rn = 1
            """
        )
        # Build a dict mapping lower-case merchant name → latest posted_at.
        latest_txn_by_merchant: dict[str, Any] = {
            row["merchant_lower"]: row["posted_at"] for row in txn_rows
        }
        provenance_rows = await pool.fetch(
            "SELECT lower(merchant) AS merchant_lower, metadata "
            "FROM transactions WHERE deleted_at IS NULL"
        )

        for row in sub_rows:
            freq = row["frequency"]
            amount = Decimal(str(row["amount"]))
            annual_cost = amount * _ANNUAL_MULTIPLIER.get(freq, 12)
            status_label = "tracked_active" if row["status"] == "active" else "tracked_paused"

            # Merchant matching: find the latest transaction whose merchant
            # contains the service name as a substring (case-insensitive).
            # Skip matching for very short service names to avoid false positives.
            service_lower = row["service"].lower()
            last_charge = None
            if len(service_lower) >= _MIN_MERCHANT_MATCH_LEN:
                for merchant_lower, posted_at in latest_txn_by_merchant.items():
                    if service_lower in merchant_lower:
                        if last_charge is None or posted_at > last_charge:
                            last_charge = posted_at

            contributing_metadata = [row.get("metadata")]
            contributing_metadata.extend(
                provenance["metadata"]
                for provenance in provenance_rows
                if len(service_lower) >= _MIN_MERCHANT_MATCH_LEN
                and service_lower in provenance["merchant_lower"]
            )
            source = resolve_complete_signal_source(contributing_metadata)
            cadence_days = {
                "weekly": 7,
                "monthly": 30,
                "quarterly": 91,
                "yearly": 365,
                "custom": 30,
            }.get(freq, 30)
            expected_at = datetime.combine(row["next_renewal"], datetime.min.time(), tzinfo=UTC)
            signal = await upsert_expected_signal(
                pool,
                signal_key=f"finance:subscription-renewal:{row.get('id') or service_lower}",
                producer=source.producer if source is not None else "unknown",
                producer_endpoint_identity=(
                    source.producer_endpoint_identity if source is not None else None
                ),
                expected_cadence=timedelta(days=cadence_days),
                last_observed_at=expected_at - timedelta(days=cadence_days),
            )

            entry: dict[str, Any] = {
                "service": row["service"],
                "amount": str(amount),
                "currency": row["currency"],
                "frequency": freq,
                "annual_cost": str(annual_cost),
                "status": status_label,
                "last_charge_date": last_charge.isoformat() if last_charge else None,
                "next_expected_date": row["next_renewal"].isoformat()
                if row["next_renewal"]
                else None,
                "measurability": signal.state.value,
                "unmeasurable_reason": signal.unmeasurable_reason,
            }
            entries.append(entry)
            if row["status"] == "active":
                total_annual_cost += annual_cost
                currency = str(row["currency"])
                annual_cost_by_currency[currency] = (
                    annual_cost_by_currency.get(currency, Decimal("0")) + annual_cost
                )

    # --- Detected but untracked recurring charges ---
    has_recurring = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'recurring_groups'
              AND table_schema = current_schema()
        )
        """
    )
    if has_recurring:
        # Get tracked service names (normalised to lower-case) to exclude.
        tracked_names: set[str] = {e["service"].lower() for e in entries}

        rg_rows = await pool.fetch(
            """
            SELECT rg.id, rg.merchant, rg.estimated_frequency, rg.avg_amount, rg.currency,
                   rg.last_seen_date, rg.next_expected_date,
                   es.measurability, es.unmeasurable_reason
            FROM recurring_groups rg
            LEFT JOIN public.expected_signals es
              ON es.signal_key = 'finance:recurrence:' || rg.id::text
            WHERE is_active = true
            ORDER BY avg_amount DESC
            """
        )
        for row in rg_rows:
            merchant = row["merchant"]
            if merchant.lower() in tracked_names:
                continue  # Already tracked — skip.

            freq = row["estimated_frequency"] or "monthly"
            amount = Decimal(str(row["avg_amount"]))
            annual_cost = amount * _ANNUAL_MULTIPLIER.get(freq, 12)
            total_annual_cost += annual_cost
            currency = str(row["currency"] or "USD")
            annual_cost_by_currency[currency] = (
                annual_cost_by_currency.get(currency, Decimal("0")) + annual_cost
            )

            last_seen = row["last_seen_date"]
            next_exp = row["next_expected_date"]
            entry = {
                "service": merchant,
                "amount": str(amount),
                "currency": row["currency"] or "USD",
                "frequency": freq,
                "annual_cost": str(annual_cost),
                "status": "detected_untracked",
                "last_charge_date": last_seen.isoformat() if last_seen else None,
                "next_expected_date": next_exp.isoformat() if next_exp else None,
                "measurability": row["measurability"] or "unmeasurable",
                "unmeasurable_reason": row["unmeasurable_reason"] or "expected_signal_missing",
            }
            entries.append(entry)

    by_currency = [
        {"currency": currency, "total_annual_cost": str(annual_cost_by_currency[currency])}
        for currency in sorted(annual_cost_by_currency)
    ]
    degraded = len(by_currency) > 1
    return {
        "entries": entries,
        "total_annual_cost": str(total_annual_cost),
        "currency": by_currency[0]["currency"] if len(by_currency) == 1 else None,
        "by_currency": by_currency,
        "legacy_aggregate_degraded": degraded,
        "degraded_reason": "multiple_currencies_unconverted" if degraded else None,
        "changes_since_last_audit": [],  # Populated by the LLM runtime using memory facts.
        "last_audit_date": None,  # Stored as memory fact with predicate='subscription_audit_date'.
        "as_of": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# 8.5  flag_tax_deductible
# ---------------------------------------------------------------------------


async def flag_tax_deductible(
    pool: asyncpg.Pool,
    year: int | None = None,
) -> dict[str, Any]:
    """Identify potentially tax-deductible transactions for a given tax year.

    Queries ``finance.transactions`` for the specified year and
    cross-references against ``finance.categories WHERE is_tax_relevant = true``.
    Falls back to a built-in default set of tax-relevant categories when the
    ``finance.categories`` table is absent or empty.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    year:
        Tax year to query (defaults to current year).

    Returns
    -------
    dict
        ``{transactions, summary: {total_flagged_amount, flagged_count,
        by_tax_category}, year, disclaimer}``

        Each transaction: ``{transaction_id, merchant, amount, currency,
        category, tax_category, posted_at, confidence}``.
    """
    if year is None:
        year = datetime.now(UTC).year

    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)

    # Build tax-category mapping: spending_category -> tax_category.
    # First try finance.categories table.
    tax_category_map: dict[str, str] = {}

    has_categories_table = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'categories'
              AND table_schema = current_schema()
        )
        """
    )
    if has_categories_table:
        has_tax_relevant_col = await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'categories' AND column_name = 'is_tax_relevant'
                  AND table_schema = current_schema()
            )
            """
        )
        if has_tax_relevant_col:
            cat_rows = await pool.fetch(
                """
                SELECT name, tax_category
                FROM categories
                WHERE is_tax_relevant = true
                  AND tax_category IS NOT NULL
                """
            )
            for row in cat_rows:
                tax_category_map[row["name"].lower()] = row["tax_category"]

    # Merge in defaults for any categories not covered by the DB.
    for cat, tax_cat in _DEFAULT_TAX_CATEGORIES.items():
        if cat not in tax_category_map:
            tax_category_map[cat] = tax_cat

    # Check for deleted_at column.
    has_deleted_at = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'transactions' AND column_name = 'deleted_at'
              AND table_schema = current_schema()
        )
        """
    )
    deleted_filter = "AND deleted_at IS NULL" if has_deleted_at else ""

    # Query all debit transactions in the year.
    rows = await pool.fetch(
        f"""
        SELECT id, merchant, amount, currency, category, posted_at
        FROM transactions
        WHERE direction = 'debit'
          AND posted_at::date >= $1
          AND posted_at::date <= $2
          {deleted_filter}
        ORDER BY posted_at ASC
        """,
        start_date,
        end_date,
    )

    flagged: list[dict[str, Any]] = []
    by_tax_category: dict[str, Decimal] = {}
    tax_by_currency: dict[str, dict[str, Any]] = {}
    total_flagged = Decimal("0")

    for row in rows:
        category = (row["category"] or "").lower().strip()
        if category not in tax_category_map:
            continue

        tax_cat = tax_category_map[category]
        amount = Decimal(str(row["amount"]))
        total_flagged += amount
        by_tax_category[tax_cat] = by_tax_category.get(tax_cat, Decimal("0")) + amount
        currency = str(row["currency"])
        currency_summary = tax_by_currency.setdefault(
            currency,
            {"currency": currency, "total_flagged_amount": Decimal("0"), "by_tax_category": {}},
        )
        currency_summary["total_flagged_amount"] += amount
        currency_summary["by_tax_category"][tax_cat] = (
            currency_summary["by_tax_category"].get(tax_cat, Decimal("0")) + amount
        )

        flagged.append(
            {
                "transaction_id": str(row["id"]),
                "merchant": row["merchant"],
                "amount": str(amount),
                "currency": row["currency"],
                "category": row["category"],
                "tax_category": tax_cat,
                "posted_at": row["posted_at"].isoformat(),
                "confidence": "high" if has_categories_table and has_tax_relevant_col else "medium",
            }
        )

    by_currency = [
        {
            "currency": currency,
            "total_flagged_amount": str(tax_by_currency[currency]["total_flagged_amount"]),
            "by_tax_category": {
                key: str(value)
                for key, value in tax_by_currency[currency]["by_tax_category"].items()
            },
        }
        for currency in sorted(tax_by_currency)
    ]
    degraded = len(by_currency) > 1
    return {
        "transactions": flagged,
        "summary": {
            "total_flagged_amount": str(total_flagged),
            "flagged_count": len(flagged),
            "by_tax_category": {k: str(v) for k, v in by_tax_category.items()},
            "currency": by_currency[0]["currency"] if len(by_currency) == 1 else None,
            "by_currency": by_currency,
            "legacy_aggregate_degraded": degraded,
            "degraded_reason": "multiple_currencies_unconverted" if degraded else None,
        },
        "year": year,
        "disclaimer": _TAX_DISCLAIMER,
    }

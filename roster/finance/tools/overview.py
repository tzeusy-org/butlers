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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.tools.finance._helpers import _row_to_dict

logger = logging.getLogger(__name__)

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


def _today() -> date:
    return datetime.now(UTC).date()


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


async def _ensure_balance_snapshots_table(pool: asyncpg.Pool) -> None:
    """Create balance_snapshots and accounts tables if they don't exist.

    Used in tests and during staged roll-out before the finance-data-model-redesign
    migration has run.  The helper is idempotent (CREATE TABLE IF NOT EXISTS).
    """
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            institution TEXT NOT NULL,
            type        TEXT NOT NULL
                            CHECK (type IN ('checking', 'savings', 'credit', 'investment')),
            name        TEXT,
            last_four   CHAR(4),
            currency    CHAR(3) NOT NULL DEFAULT 'USD',
            metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS balance_snapshots (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            balance     NUMERIC(14, 2) NOT NULL,
            currency    CHAR(3) NOT NULL DEFAULT 'USD',
            as_of_date  DATE NOT NULL,
            source      TEXT NOT NULL DEFAULT 'manual',
            metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_balance_snapshot_account_date UNIQUE (account_id, as_of_date)
        )
    """)


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

    # Infer type from name heuristics if not provided by caller.
    lower_name = name.lower()
    inferred_type = account_type
    if any(kw in lower_name for kw in ("credit", "card", "cc", "visa", "mastercard", "amex")):
        inferred_type = "credit"
    elif any(kw in lower_name for kw in ("savings", "save", "hsa")):
        inferred_type = "savings"
    elif any(kw in lower_name for kw in ("invest", "ira", "401k", "brokerage", "roth", "fidelity")):
        inferred_type = "investment"

    new_row = await pool.fetchrow(
        """
        INSERT INTO accounts (institution, type, name, currency)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        institution,
        inferred_type,
        name,
        currency,
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
    result["balance"] = str(stored_balance)
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

    # Apply carry-forward logic per account, building the history.
    result_snapshots: list[dict[str, Any]] = []
    for period in periods:
        period_accounts: list[dict[str, Any]] = []
        total_assets = Decimal("0")
        total_liabilities = Decimal("0")

        for account_id in sorted(account_ids):
            acct_periods = snapshots_by_account_period.get(account_id, {})
            if period in acct_periods:
                entry = dict(acct_periods[period])
            else:
                # Carry forward the most recent prior snapshot in this period list.
                # Look backwards through earlier periods for this account.
                carried = None
                for earlier in reversed(periods[: periods.index(period)]):
                    if account_id in snapshots_by_account_period:
                        if earlier in snapshots_by_account_period[account_id]:
                            carried = dict(snapshots_by_account_period[account_id][earlier])
                            carried["carried_forward"] = True
                            break
                if carried is None:
                    # Use pre-range seed if available.
                    if account_id in seed_by_account:
                        carried = dict(seed_by_account[account_id])
                    else:
                        continue  # No data for this account yet; skip.
                entry = carried

            balance = entry["balance"]
            if balance >= 0:
                total_assets += balance
            else:
                total_liabilities += abs(balance)

            period_accounts.append(
                {
                    "account": entry["account"],
                    "institution": entry["institution"],
                    "balance": str(balance),
                    "currency": entry["currency"],
                    "carried_forward": entry["carried_forward"],
                }
            )

        result_snapshots.append(
            {
                "period": period,
                "accounts": period_accounts,
                "total_assets": str(total_assets),
                "total_liabilities": str(total_liabilities),
                "net_worth": str(total_assets - total_liabilities),
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
            COALESCE(SUM(amount) FILTER (WHERE direction = 'credit'), 0) AS income,
            COALESCE(SUM(amount) FILTER (WHERE direction = 'debit'),  0) AS expenses
        FROM transactions
        WHERE posted_at::date >= $1
          AND posted_at::date <= $2
          {deleted_filter}
        GROUP BY period_key
        ORDER BY period_key ASC
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
        period_data[row["period_key"]] = {
            "period": row["period_key"],
            "income": str(income),
            "expenses": str(expenses),
            "net": str(net),
            "savings_rate": savings_rate,
        }

    # Category breakdown (optional).
    if breakdown:
        cat_rows = await pool.fetch(
            f"""
            SELECT
                {period_expr}                                             AS period_key,
                category,
                COALESCE(SUM(amount) FILTER (WHERE direction = 'credit'), 0) AS income,
                COALESCE(SUM(amount) FILTER (WHERE direction = 'debit'),  0) AS expenses
            FROM transactions
            WHERE posted_at::date >= $1
              AND posted_at::date <= $2
              {deleted_filter}
            GROUP BY period_key, category
            ORDER BY period_key ASC, expenses DESC
            """,
            start_date,
            end_date,
        )
        cat_by_period: dict[str, list[dict[str, Any]]] = {}
        for row in cat_rows:
            pk = row["period_key"]
            if pk not in cat_by_period:
                cat_by_period[pk] = []
            income = Decimal(str(row["income"]))
            expenses = Decimal(str(row["expenses"]))
            cat_by_period[pk].append(
                {
                    "category": row["category"],
                    "income": str(income),
                    "expenses": str(expenses),
                    "net": str(income - expenses),
                }
            )
        for pk, data in period_data.items():
            data["categories"] = cat_by_period.get(pk, [])

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

    return {
        "periods": period_list,
        "avg_net": avg_net,
        "avg_savings_rate": avg_savings_rate,
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

    # --- Tracked subscriptions ---
    has_subscriptions = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'subscriptions'
        )
        """
    )
    if has_subscriptions:
        sub_rows = await pool.fetch(
            """
            SELECT service, amount, currency, frequency, status, next_renewal, updated_at
            FROM subscriptions
            WHERE status IN ('active', 'paused')
            ORDER BY service ASC
            """
        )
        for row in sub_rows:
            freq = row["frequency"]
            amount = Decimal(str(row["amount"]))
            annual_cost = amount * _ANNUAL_MULTIPLIER.get(freq, 12)
            status_label = "tracked_active" if row["status"] == "active" else "tracked_paused"

            # Determine last charge date from transactions.
            last_charge = await pool.fetchval(
                "SELECT MAX(posted_at) FROM transactions WHERE lower(merchant) LIKE lower($1)",
                f"%{row['service']}%",
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
            }
            entries.append(entry)
            if row["status"] == "active":
                total_annual_cost += annual_cost

    # --- Detected but untracked recurring charges ---
    has_recurring = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'recurring_groups'
        )
        """
    )
    if has_recurring:
        # Get tracked service names (normalised to lower-case) to exclude.
        tracked_names: set[str] = {e["service"].lower() for e in entries}

        rg_rows = await pool.fetch(
            """
            SELECT merchant, estimated_frequency, avg_amount, currency,
                   last_seen_date, next_expected_date
            FROM recurring_groups
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
            }
            entries.append(entry)

    return {
        "entries": entries,
        "total_annual_cost": str(total_annual_cost),
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
        )
        """
    )
    if has_categories_table:
        has_tax_relevant_col = await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'categories' AND column_name = 'is_tax_relevant'
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

    if not tax_category_map:
        # No categories at all — return empty result.
        return {
            "transactions": [],
            "summary": {
                "total_flagged_amount": "0",
                "flagged_count": 0,
                "by_tax_category": {},
            },
            "year": year,
            "disclaimer": _TAX_DISCLAIMER,
        }

    # Check for deleted_at column.
    has_deleted_at = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'transactions' AND column_name = 'deleted_at'
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
    total_flagged = Decimal("0")

    for row in rows:
        category = (row["category"] or "").lower().strip()
        if category not in tax_category_map:
            continue

        tax_cat = tax_category_map[category]
        amount = Decimal(str(row["amount"]))
        total_flagged += amount
        by_tax_category[tax_cat] = by_tax_category.get(tax_cat, Decimal("0")) + amount

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

    return {
        "transactions": flagged,
        "summary": {
            "total_flagged_amount": str(total_flagged),
            "flagged_count": len(flagged),
            "by_tax_category": {k: str(v) for k, v in by_tax_category.items()},
        },
        "year": year,
        "disclaimer": _TAX_DISCLAIMER,
    }

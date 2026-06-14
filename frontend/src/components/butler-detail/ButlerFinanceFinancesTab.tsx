// ---------------------------------------------------------------------------
// ButlerFinanceFinancesTab — bu-iuol4.29
//
// Finances bespoke tab for the Finance butler detail page.
// 4-col panel grid layout:
//
//   Row 1 — KPI strip (4 cells, full width):
//     monthly spend | active subscriptions | next bill | top category (30d)
//
//   Row 2:
//     Upcoming bills (span-2) | Spending by category · 30d (span-2)
//
//   Row 3:
//     Recent transactions (span-4, scrollable)
//
//   Row 4:
//     Subscriptions roster (span-2) | [empty] (span-2)
//
// Decision — 4th KPI cell: "Top category · 30d"
//   Largest spending category over the 30-day window from the existing
//   useFinanceSpendingSummary call. Shows category name + amount. Chosen
//   over 30-day delta (needs a second period reference) and a raw "spare"
//   placeholder because it reuses the already-fetched category data and
//   surfaces actionable spend concentration at a glance.
//
// No backend changes — all four hooks already exist:
//   useFinanceSpendingSummary, useFinanceSubscriptions,
//   useFinanceUpcomingBills, useFinanceTransactions
// ---------------------------------------------------------------------------

import { useCallback, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { formatInTimeZone } from "date-fns-tz";
import { toast } from "sonner";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { OWNER_TZ_DEFAULT } from "@/hooks/use-time-window";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Time } from "@/components/ui/time";
import {
  useBulkUpdateTransactionMetadata,
  useFinanceAccounts,
  useFinanceSpendingSummary,
  useFinanceSubscriptions,
  useFinanceTransactions,
  useFinanceUpcomingBills,
} from "@/hooks/use-finance";
import type {
  FinanceAccount,
  FinanceBulkUpdateOp,
  FinanceTransaction,
  FinanceSubscription,
  FinanceUpcomingBillItem,
  FinanceSpendingGroup,
} from "@/api/index.ts";
import { Panel, KpiCell } from "@/components/butler-detail/atoms";

// ---------------------------------------------------------------------------
// Format helpers
// ---------------------------------------------------------------------------

/**
 * Format a numeric string or number as a locale currency amount.
 * Uses Intl.NumberFormat to avoid inline template-literal formatting.
 */
function formatCurrency(amount: string | number, currency = "USD"): string {
  const n = typeof amount === "string" ? parseFloat(amount) : amount;
  if (isNaN(n)) return String(amount);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Math.abs(n));
}

/** Capitalise first letter of a category string. */
function titleCase(s: string): string {
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// ---------------------------------------------------------------------------
// Direction → class mapping (single source of truth for debit/credit colour)
// Token names only — never inline oklch/hex.
// ---------------------------------------------------------------------------

const DIRECTION_CLASS: Record<string, string> = {
  debit: "text-destructive",
  credit: "text-emerald-500",
};

// ---------------------------------------------------------------------------
// Urgency chip colours — preserved from original implementation
// ---------------------------------------------------------------------------

const URGENCY_VARIANT: Record<
  string,
  "destructive" | "outline" | "secondary" | "default"
> = {
  overdue: "destructive",
  due_today: "destructive",
  due_soon: "default",
  upcoming: "outline",
};

function UrgencyChip({ urgency }: { urgency: string }) {
  const variant = URGENCY_VARIANT[urgency] ?? "outline";
  const label = urgency.replace("_", " ");
  return (
    <Badge variant={variant} className="text-xs font-mono capitalize shrink-0">
      {label}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Empty and loading primitives
// ---------------------------------------------------------------------------

function EmptyLine({ children }: { children: ReactNode }) {
  return (
    <p
      className="text-sm text-muted-foreground italic font-[family-name:var(--font-serif,serif)]"
      data-testid="empty-state-line"
    >
      {children}
    </p>
  );
}

function LoadingLine() {
  return (
    <p className="text-sm text-muted-foreground" data-testid="loading-line">
      Loading...
    </p>
  );
}

// ---------------------------------------------------------------------------
// Row 2a: Upcoming bills urgency list
// ---------------------------------------------------------------------------

function UpcomingBillsPanel({
  items,
  isLoading,
}: {
  items: FinanceUpcomingBillItem[];
  isLoading: boolean;
}) {
  return (
    <Panel title="Upcoming bills" span={2} testId="finance-upcoming-bills-section">
      {isLoading ? (
        <LoadingLine />
      ) : items.length === 0 ? (
        <EmptyLine>No upcoming bills -- you are all clear!</EmptyLine>
      ) : (
        <ul className="divide-y" data-testid="upcoming-bills-list">
          {items.map((item) => (
            <li
              key={item.bill.id}
              className="flex items-center justify-between py-2 gap-2"
              data-testid="upcoming-bill-item"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium truncate">{item.bill.payee}</p>
                <p className="text-xs text-muted-foreground">
                  Due{" "}
                  <Time
                    value={item.bill.due_date}
                    mode="absolute"
                    precision="day"
                    compact
                  />
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-sm font-mono tnum">
                  {formatCurrency(item.bill.amount, item.bill.currency)}
                </span>
                <UrgencyChip urgency={item.urgency} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Row 2b: 30-day category spend chart
// ---------------------------------------------------------------------------

function CategorySpendPanel({
  groups,
  currency,
  isLoading,
}: {
  groups: FinanceSpendingGroup[];
  currency: string;
  isLoading: boolean;
}) {
  const chartData = useMemo(
    () =>
      groups
        .slice(0, 8)
        .map((g) => ({
          category: titleCase(g.key),
          amount: parseFloat(g.amount) || 0,
        })),
    [groups],
  );

  return (
    <Panel title="Spending by category" sub="30d" span={2} testId="finance-category-chart-section">
      {isLoading ? (
        <LoadingLine />
      ) : chartData.length === 0 ? (
        <EmptyLine>No spending data for the last 30 days.</EmptyLine>
      ) : (
        <div data-testid="category-spend-chart" style={{ height: 220 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={chartData}
              layout="vertical"
              margin={{ top: 0, right: 16, bottom: 0, left: 0 }}
            >
              <XAxis
                type="number"
                tickFormatter={(v: number) =>
                  new Intl.NumberFormat("en-US", {
                    style: "currency",
                    currency,
                    notation: "compact",
                    maximumFractionDigits: 0,
                  }).format(v)
                }
                tick={{ fontSize: 11 }}
              />
              <YAxis
                type="category"
                dataKey="category"
                width={90}
                tick={{ fontSize: 11 }}
              />
              {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
              <Tooltip formatter={(value: any) => [formatCurrency(String(value), currency), "Spend"]} />
              <Bar dataKey="amount" fill="hsl(var(--primary))" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Row 3: Recent transactions table
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Bulk-action bar (bu-v3a4x.3)
//
// Lets the owner select transaction rows and bulk-write to the facts OVERLAY
// via PATCH /transactions/bulk-metadata: set an inferred_category and/or a
// normalized_merchant. The endpoint matches facts by an ILIKE merchant_pattern,
// so the bar builds one op per distinct RAW merchant in the selection (the base
// merchant is the overlay key). Overlay-aware reads (bu-v3a4x.1) then surface
// these edits on the next /transactions refresh.
// ---------------------------------------------------------------------------

function BulkActionBar({
  selectedCount,
  onApply,
  isPending,
}: {
  selectedCount: number;
  onApply: (category: string, normalizedMerchant: string) => void;
  isPending: boolean;
}) {
  const [category, setCategory] = useState("");
  const [normalizedMerchant, setNormalizedMerchant] = useState("");

  const hasSelection = selectedCount > 0;
  const hasEdit = category.trim() !== "" || normalizedMerchant.trim() !== "";
  const disabled = !hasSelection || !hasEdit || isPending;

  return (
    <div
      className="flex flex-wrap items-end gap-3 border-b border-border/60 pb-3 mb-3"
      data-testid="finance-bulk-action-bar"
    >
      <div className="flex flex-col gap-1">
        <label
          htmlFor="bulk-category"
          className="text-xs text-muted-foreground font-medium"
        >
          Set category
        </label>
        <Input
          id="bulk-category"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          placeholder="e.g. groceries"
          className="h-8 w-44 text-sm"
          data-testid="bulk-category-input"
        />
      </div>
      <div className="flex flex-col gap-1">
        <label
          htmlFor="bulk-merchant"
          className="text-xs text-muted-foreground font-medium"
        >
          Normalize merchant
        </label>
        <Input
          id="bulk-merchant"
          value={normalizedMerchant}
          onChange={(e) => setNormalizedMerchant(e.target.value)}
          placeholder="e.g. Whole Foods Market"
          className="h-8 w-52 text-sm"
          data-testid="bulk-merchant-input"
        />
      </div>
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          disabled={disabled}
          onClick={() => onApply(category.trim(), normalizedMerchant.trim())}
          data-testid="bulk-apply-button"
        >
          {isPending ? "Applying..." : "Apply to selected"}
        </Button>
        <span
          className="text-xs text-muted-foreground tnum"
          data-testid="bulk-selection-count"
        >
          {selectedCount} selected
        </span>
      </div>
    </div>
  );
}

function TransactionsPanel({
  transactions,
  isLoading,
  selectedIds,
  onToggleRow,
  onToggleAll,
  onApplyBulk,
  isApplying,
}: {
  transactions: FinanceTransaction[];
  isLoading: boolean;
  selectedIds: Set<string>;
  onToggleRow: (id: string) => void;
  onToggleAll: (ids: string[], checked: boolean) => void;
  onApplyBulk: (category: string, normalizedMerchant: string) => void;
  isApplying: boolean;
}) {
  const rows = transactions.slice(0, 15);
  const rowIds = rows.map((tx) => tx.id);
  const allSelected = rowIds.length > 0 && rowIds.every((id) => selectedIds.has(id));

  return (
    <Panel title="Recent transactions" span={4} scroll height="380px" testId="finance-transactions-section">
      {isLoading ? (
        <LoadingLine />
      ) : rows.length === 0 ? (
        <EmptyLine>No transactions recorded yet.</EmptyLine>
      ) : (
        <>
          <BulkActionBar
            selectedCount={selectedIds.size}
            onApply={onApplyBulk}
            isPending={isApplying}
          />
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="transactions-table">
              <thead>
                <tr className="border-b text-xs text-muted-foreground">
                  <th className="py-1 pr-3 text-left font-medium w-8">
                    <Checkbox
                      checked={allSelected}
                      onCheckedChange={(c) => onToggleAll(rowIds, c === true)}
                      aria-label="Select all transactions"
                      data-testid="select-all-checkbox"
                    />
                  </th>
                  <th className="py-1 pr-3 text-left font-medium">Date</th>
                  <th className="py-1 pr-3 text-left font-medium">Merchant</th>
                  <th className="py-1 pr-3 text-left font-medium">Category</th>
                  <th className="py-1 text-right font-medium">Amount</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {rows.map((tx) => (
                  <tr key={tx.id} data-testid="transaction-row">
                    <td className="py-2 pr-3">
                      <Checkbox
                        checked={selectedIds.has(tx.id)}
                        onCheckedChange={() => onToggleRow(tx.id)}
                        aria-label={`Select transaction ${tx.merchant}`}
                        data-testid="transaction-checkbox"
                      />
                    </td>
                    <td className="py-2 pr-3 text-xs text-muted-foreground whitespace-nowrap">
                      <Time
                        value={tx.posted_at}
                        mode="absolute"
                        precision="day"
                        compact
                      />
                    </td>
                    <td className="py-2 pr-3 font-medium max-w-[160px] truncate">
                      {tx.normalized_merchant ?? tx.merchant}
                    </td>
                    <td className="py-2 pr-3 text-xs text-muted-foreground">
                      {titleCase(tx.inferred_category ?? tx.category)}
                    </td>
                    <td
                      className={`py-2 text-right font-mono tnum text-xs ${DIRECTION_CLASS[tx.direction] ?? ""}`}
                    >
                      {tx.direction === "debit" ? "−" : "+"}
                      {formatCurrency(tx.amount, tx.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Row 4a: Subscriptions roster
// ---------------------------------------------------------------------------

const STATUS_VARIANT: Record<string, "default" | "secondary" | "outline"> = {
  active: "default",
  paused: "secondary",
  cancelled: "outline",
};

function SubscriptionsPanel({
  subscriptions,
  isLoading,
}: {
  subscriptions: FinanceSubscription[];
  isLoading: boolean;
}) {
  return (
    <Panel title="Subscriptions" span={2} testId="finance-subscriptions-section">
      {isLoading ? (
        <LoadingLine />
      ) : subscriptions.length === 0 ? (
        <EmptyLine>No subscriptions tracked yet.</EmptyLine>
      ) : (
        <ul className="divide-y" data-testid="subscriptions-list">
          {subscriptions.map((sub) => (
            <li
              key={sub.id}
              className="flex items-center justify-between py-2 gap-2"
              data-testid="subscription-row"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium truncate">{sub.service}</p>
                <p className="text-xs text-muted-foreground capitalize">
                  {sub.frequency} · renews{" "}
                  <Time
                    value={sub.next_renewal}
                    mode="absolute"
                    precision="day"
                    compact
                  />
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-sm font-mono tnum">
                  {formatCurrency(sub.amount, sub.currency)}
                </span>
                <Badge
                  variant={STATUS_VARIANT[sub.status] ?? "outline"}
                  className="text-xs capitalize"
                >
                  {sub.status}
                </Badge>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Row 4b: Accounts panel (bu-alenp)
//
// Surfaces the existing GET /finance/accounts endpoint. The endpoint returns
// account registry rows (institution, type, last_four, currency) — there is no
// balance field on the response, so this is an honest account-context list, not
// a fabricated net-worth total. Empty state renders gracefully when the live
// endpoint returns total:0.
// ---------------------------------------------------------------------------

/** Title-case an account type token (e.g. "checking" → "Checking"). */
function formatAccountType(type: string): string {
  return type
    .split(/[_\s]+/)
    .filter(Boolean)
    .map(titleCase)
    .join(" ");
}

function AccountsPanel({
  accounts,
  isLoading,
}: {
  accounts: FinanceAccount[];
  isLoading: boolean;
}) {
  // Distinct currencies across tracked accounts — a light "context" summary
  // that does not invent balances the endpoint never returns.
  const currencies = useMemo(
    () => Array.from(new Set(accounts.map((a) => a.currency))).sort(),
    [accounts],
  );

  return (
    <Panel title="Accounts" span={2} testId="finance-accounts-section">
      {isLoading ? (
        <LoadingLine />
      ) : accounts.length === 0 ? (
        <EmptyLine>
          No accounts on file yet -- connect or add an account to see net-worth context.
        </EmptyLine>
      ) : (
        <>
          <p
            className="text-xs text-muted-foreground mb-2"
            data-testid="accounts-summary"
          >
            {accounts.length} {accounts.length === 1 ? "account" : "accounts"}
            {currencies.length > 0 ? ` · ${currencies.join(", ")}` : ""}
          </p>
          <ul className="divide-y" data-testid="accounts-list">
            {accounts.map((acct) => (
              <li
                key={acct.id}
                className="flex items-center justify-between py-2 gap-2"
                data-testid="account-row"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate">
                    {acct.name ?? acct.institution}
                  </p>
                  <p className="text-xs text-muted-foreground truncate">
                    {acct.name ? `${acct.institution} · ` : ""}
                    {formatAccountType(acct.type)}
                    {acct.last_four ? ` ····${acct.last_four}` : ""}
                  </p>
                </div>
                <Badge variant="outline" className="text-xs font-mono shrink-0">
                  {acct.currency}
                </Badge>
              </li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// ButlerFinanceFinancesTab — composed entry point
// ---------------------------------------------------------------------------

export default function ButlerFinanceFinancesTab() {
  const today = new Date();
  const tz = OWNER_TZ_DEFAULT;

  // Current month window for monthly spend KPI
  const monthStart = formatInTimeZone(
    new Date(today.getFullYear(), today.getMonth(), 1),
    tz,
    "yyyy-MM-dd",
  );
  const monthEnd = formatInTimeZone(today, tz, "yyyy-MM-dd");

  // 30-day window for category chart and top-category KPI
  const thirtyDaysAgo = new Date(today);
  thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
  const thirtyDaysAgoStr = formatInTimeZone(thirtyDaysAgo, tz, "yyyy-MM-dd");

  const { data: txResp, isLoading: txLoading } = useFinanceTransactions({ limit: 15 });
  const { data: subResp, isLoading: subLoading } = useFinanceSubscriptions();
  const { data: upcomingResp, isLoading: upcomingLoading } = useFinanceUpcomingBills({
    days_ahead: 30,
  });
  const { data: monthlySummary, isLoading: monthlyLoading } = useFinanceSpendingSummary({
    start_date: monthStart,
    end_date: monthEnd,
    group_by: "category",
  });
  const { data: categorySummary, isLoading: categoryLoading } = useFinanceSpendingSummary({
    start_date: thirtyDaysAgoStr,
    end_date: monthEnd,
    group_by: "category",
  });
  const { data: accountsResp, isLoading: accountsLoading } = useFinanceAccounts();

  // Memoized so the applyBulk useCallback below has a stable transactions dep.
  const transactions = useMemo(() => txResp?.data ?? [], [txResp]);

  // ---- Bulk edit state (bu-v3a4x.3) --------------------------------------
  const bulkUpdate = useBulkUpdateTransactionMetadata();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const toggleRow = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAll = useCallback((ids: string[], checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }, []);

  const applyBulk = useCallback(
    (category: string, normalizedMerchant: string) => {
      if (selectedIds.size === 0) return;
      if (category === "" && normalizedMerchant === "") return;

      // Build the overlay set once; both fields are optional.
      const set: { inferred_category?: string; normalized_merchant?: string } = {};
      if (category !== "") set.inferred_category = category;
      if (normalizedMerchant !== "") set.normalized_merchant = normalizedMerchant;

      // The overlay endpoint keys on the RAW merchant (ILIKE merchant_pattern),
      // so collapse the selection to one op per distinct base merchant. Use an
      // exact ILIKE on the literal merchant string (no wildcards) to avoid
      // accidentally matching look-alike merchants.
      const selected = transactions.filter((tx) => selectedIds.has(tx.id));
      const merchants = Array.from(new Set(selected.map((tx) => tx.merchant)));
      const ops: FinanceBulkUpdateOp[] = merchants.map((merchant) => ({
        match: { merchant_pattern: merchant },
        set,
      }));

      if (ops.length === 0) return;

      const summary =
        `Apply ${[
          set.inferred_category ? `category "${set.inferred_category}"` : null,
          set.normalized_merchant ? `merchant "${set.normalized_merchant}"` : null,
        ]
          .filter(Boolean)
          .join(" and ")} to ${selectedIds.size} ` +
        `transaction${selectedIds.size === 1 ? "" : "s"} ` +
        `(${ops.length} merchant${ops.length === 1 ? "" : "s"})?`;

      // Honest confirmation before a write that fans out across the overlay.
      if (typeof window !== "undefined" && !window.confirm(summary)) return;

      bulkUpdate.mutate(
        { ops },
        {
          onSuccess: (resp) => {
            toast.success(
              `Updated ${resp.updated_total} transaction fact${
                resp.updated_total === 1 ? "" : "s"
              }.`,
            );
            setSelectedIds(new Set());
          },
          onError: (err) => {
            toast.error(
              err instanceof Error ? err.message : "Bulk update failed.",
            );
          },
        },
      );
    },
    [bulkUpdate, selectedIds, transactions],
  );
  const subscriptions = subResp?.data ?? [];
  const upcomingBills = upcomingResp?.items ?? [];
  const accounts = accountsResp?.data ?? [];

  // Active subscriptions KPI: count only real, billable active subs. Drop the
  // literal service:'dummy' test record and any $0 placeholder — neither
  // represents a genuine recurring charge, so counting them overstates the KPI.
  const activeSubCount = subscriptions.filter(
    (s) => s.status === "active" && s.service !== "dummy" && parseFloat(s.amount) > 0,
  ).length;

  // Next bill KPI: pick the soonest upcoming bill with a real, known amount.
  // Bills can carry a $0 / amount_known:false placeholder (e.g. a "statement is
  // ready" signal with no extracted balance); surfacing those as "$0.00" is
  // misleading. upcomingBills is already ordered by due_date ASC, so the first
  // bill that clears both checks is the next actionable one.
  const nextBill =
    upcomingBills.find((item) => {
      const known = item.bill.metadata?.amount_known;
      if (known === false) return false;
      return parseFloat(item.bill.amount) > 0;
    }) ?? null;
  const totalSpend = monthlySummary?.total_spend ?? "0";
  const currency = monthlySummary?.currency ?? "USD";
  // Sort once at the parent so children receive a stable, ordered reference.
  const categoryGroups = useMemo(() => {
    const groups = categorySummary?.groups ?? [];
    return [...groups].sort((a, b) => parseFloat(b.amount) - parseFloat(a.amount));
  }, [categorySummary]);
  const chartCurrency = categorySummary?.currency ?? "USD";

  // Top category (4th KPI cell): first element of the pre-sorted array.
  const topCategory = categoryGroups[0] ?? null;

  const kpiLoading = txLoading || subLoading || upcomingLoading || monthlyLoading || categoryLoading;

  // KPI cell values
  const monthlySpendValue = kpiLoading ? "..." : formatCurrency(totalSpend, currency);

  const nextBillValue = kpiLoading
    ? "..."
    : nextBill
      ? formatCurrency(nextBill.bill.amount, nextBill.bill.currency)
      : "—";
  const nextBillSub = nextBill ? nextBill.bill.payee : undefined;

  const topCategoryValue = kpiLoading
    ? "..."
    : topCategory
      ? formatCurrency(topCategory.amount, chartCurrency)
      : "—";
  const topCategorySub = topCategory ? titleCase(topCategory.key) : undefined;

  return (
    <div
      className="grid grid-cols-1 lg:grid-cols-4 border-t border-l border-border/60"
      data-testid="finance-finances-tab"
    >
      {/* Row 1: KPI strip — 4 cells */}
      <div
        className="col-span-1 lg:col-span-4 grid grid-cols-2 lg:grid-cols-4"
        data-testid="finance-kpi-strip"
      >
        <Panel>
          <div data-testid="kpi-value">
            <KpiCell
              label="Monthly spend"
              value={monthlySpendValue}
            />
          </div>
        </Panel>
        <Panel>
          <div data-testid="kpi-value">
            <KpiCell
              label="Active subscriptions"
              value={kpiLoading ? "..." : String(activeSubCount)}
            />
          </div>
        </Panel>
        <Panel>
          <div data-testid="kpi-value">
            <KpiCell
              label="Next bill"
              value={nextBillValue}
              sub={nextBillSub}
            />
          </div>
        </Panel>
        <Panel>
          <div data-testid="kpi-value">
            <KpiCell
              label="Top category · 30d"
              value={topCategoryValue}
              sub={topCategorySub}
            />
          </div>
        </Panel>
      </div>

      {/* Row 2: Upcoming bills (span-2) + Category chart (span-2) */}
      <UpcomingBillsPanel items={upcomingBills} isLoading={upcomingLoading} />
      <CategorySpendPanel
        groups={categoryGroups}
        currency={chartCurrency}
        isLoading={categoryLoading}
      />

      {/* Row 3: Recent transactions (span-4, scrollable) */}
      <TransactionsPanel
        transactions={transactions}
        isLoading={txLoading}
        selectedIds={selectedIds}
        onToggleRow={toggleRow}
        onToggleAll={toggleAll}
        onApplyBulk={applyBulk}
        isApplying={bulkUpdate.isPending}
      />

      {/* Row 4: Subscriptions (span-2) + Accounts (span-2) */}
      <SubscriptionsPanel subscriptions={subscriptions} isLoading={subLoading} />
      <AccountsPanel accounts={accounts} isLoading={accountsLoading} />
    </div>
  );
}

/**
 * ButlerFinanceFinancesTab
 *
 * Wires finance API endpoints to the Finances bespoke tab on the finance butler
 * detail page. Consumes hooks from use-finance.ts — no new HTTP routes added.
 *
 * Five sections (4-col grid):
 *  1. KPI strip      — monthly spend / active subscription count / next bill
 *  2. Recent transactions table — paginated, date/merchant/category/amount
 *  3. Upcoming bills urgency list — overdue / due_today / due_soon / upcoming
 *  4. Subscriptions roster — service / amount / frequency / next renewal / status
 *  5. 30-day category spend chart — horizontal bar chart via recharts
 *
 * bead: bu-nqepq
 */

import type { ReactNode } from "react";
import { useMemo } from "react";
import { formatInTimeZone } from "date-fns-tz";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { OWNER_TZ_DEFAULT } from "@/hooks/use-time-window";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  useFinanceSpendingSummary,
  useFinanceSubscriptions,
  useFinanceTransactions,
  useFinanceUpcomingBills,
} from "@/hooks/use-finance";
import type {
  FinanceTransaction,
  FinanceSubscription,
  FinanceUpcomingBillItem,
  FinanceSpendingGroup,
} from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Shared UI primitives
// ---------------------------------------------------------------------------

/** Empty-state placeholder: serif italic per Dispatch typography guidelines. */
function EmptyStateLine({ children }: { children: ReactNode }) {
  return (
    <p
      className="text-sm text-muted-foreground italic font-[family-name:var(--font-serif,serif)]"
      data-testid="empty-state-line"
    >
      {children}
    </p>
  );
}

/** Non-spinner loading placeholder. */
function LoadingLine() {
  return (
    <p className="text-sm text-muted-foreground" data-testid="loading-line">
      Loading…
    </p>
  );
}

// ---------------------------------------------------------------------------
// Format helpers
// ---------------------------------------------------------------------------

/** Format a numeric string as a locale currency amount (e.g. "1,234.56"). */
function formatAmount(amount: string, currency = "USD"): string {
  const n = parseFloat(amount);
  if (isNaN(n)) return amount;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Math.abs(n));
}

/** Format an ISO date or datetime string as a short date (e.g. "May 10").
 *
 * Date-only strings (YYYY-MM-DD) are parsed in the owner timezone so that
 * due dates and renewal dates do not shift by a day for negative UTC offsets.
 */
function formatShortDate(dateStr: string, tz: string = OWNER_TZ_DEFAULT): string {
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return dateStr.slice(0, 10);
  return formatInTimeZone(d, tz, "MMM d");
}

/** Capitalise first letter of a category string. */
function titleCase(s: string): string {
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// ---------------------------------------------------------------------------
// Urgency chip colours
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
// Section 1: KPI strip
// ---------------------------------------------------------------------------

interface KpiStripProps {
  totalSpend: string;
  currency: string;
  activeSubCount: number;
  nextBill: FinanceUpcomingBillItem | null;
  isLoading: boolean;
}

function FinanceKpiStrip({
  totalSpend,
  currency,
  activeSubCount,
  nextBill,
  isLoading,
}: KpiStripProps) {
  const nextBillLabel = useMemo(() => {
    if (!nextBill) return "—";
    return `${nextBill.bill.payee} ${formatAmount(nextBill.bill.amount, nextBill.bill.currency)}`;
  }, [nextBill]);

  const kpis = [
    {
      label: "Monthly spend",
      value: isLoading ? "…" : formatAmount(totalSpend, currency),
    },
    {
      label: "Active subscriptions",
      value: isLoading ? "…" : activeSubCount,
    },
    {
      label: "Next bill",
      value: isLoading ? "…" : nextBillLabel,
    },
  ];

  return (
    <div
      className="grid grid-cols-1 gap-3 sm:grid-cols-3"
      data-testid="finance-kpi-strip"
    >
      {kpis.map((kpi) => (
        <Card key={kpi.label}>
          <CardContent className="pt-4">
            <p className="text-xs text-muted-foreground">{kpi.label}</p>
            <p
              className="text-2xl font-bold font-mono truncate"
              data-testid="kpi-value"
            >
              {kpi.value}
            </p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section 2: Recent transactions table
// ---------------------------------------------------------------------------

function TransactionsSection({
  transactions,
  isLoading,
}: {
  transactions: FinanceTransaction[];
  isLoading: boolean;
}) {
  const rows = transactions.slice(0, 15);

  return (
    <Card data-testid="finance-transactions-section">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Recent transactions</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingLine />
        ) : rows.length === 0 ? (
          <EmptyStateLine>No transactions recorded yet.</EmptyStateLine>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="transactions-table">
              <thead>
                <tr className="border-b text-xs text-muted-foreground">
                  <th className="py-1 pr-3 text-left font-medium">Date</th>
                  <th className="py-1 pr-3 text-left font-medium">Merchant</th>
                  <th className="py-1 pr-3 text-left font-medium">Category</th>
                  <th className="py-1 text-right font-medium">Amount</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {rows.map((tx) => (
                  <tr key={tx.id} data-testid="transaction-row">
                    <td className="py-2 pr-3 text-xs text-muted-foreground whitespace-nowrap">
                      {formatShortDate(tx.posted_at)}
                    </td>
                    <td className="py-2 pr-3 font-medium max-w-[160px] truncate">
                      {tx.normalized_merchant ?? tx.merchant}
                    </td>
                    <td className="py-2 pr-3 text-xs text-muted-foreground">
                      {titleCase(tx.inferred_category ?? tx.category)}
                    </td>
                    <td
                      className={`py-2 text-right font-mono text-xs ${
                        tx.direction === "debit" ? "text-destructive" : "text-green-600"
                      }`}
                    >
                      {tx.direction === "debit" ? "−" : "+"}
                      {formatAmount(tx.amount, tx.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section 3: Upcoming bills urgency list
// ---------------------------------------------------------------------------

function UpcomingBillsSection({
  items,
  isLoading,
}: {
  items: FinanceUpcomingBillItem[];
  isLoading: boolean;
}) {
  return (
    <Card data-testid="finance-upcoming-bills-section">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Upcoming bills</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingLine />
        ) : items.length === 0 ? (
          <EmptyStateLine>No upcoming bills — you're all clear!</EmptyStateLine>
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
                    Due {formatShortDate(item.bill.due_date)}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-sm font-mono">
                    {formatAmount(item.bill.amount, item.bill.currency)}
                  </span>
                  <UrgencyChip urgency={item.urgency} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section 4: Subscriptions roster
// ---------------------------------------------------------------------------

const STATUS_VARIANT: Record<string, "default" | "secondary" | "outline"> = {
  active: "default",
  paused: "secondary",
  cancelled: "outline",
};

function SubscriptionsSection({
  subscriptions,
  isLoading,
}: {
  subscriptions: FinanceSubscription[];
  isLoading: boolean;
}) {
  return (
    <Card data-testid="finance-subscriptions-section">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Subscriptions</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingLine />
        ) : subscriptions.length === 0 ? (
          <EmptyStateLine>No subscriptions tracked yet.</EmptyStateLine>
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
                    {sub.frequency} · renews {formatShortDate(sub.next_renewal)}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-sm font-mono">
                    {formatAmount(sub.amount, sub.currency)}
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
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section 5: 30-day category spend chart
// ---------------------------------------------------------------------------

function CategorySpendChart({
  groups,
  currency,
  isLoading,
}: {
  groups: FinanceSpendingGroup[];
  currency: string;
  isLoading: boolean;
}) {
  // Top 8 categories by amount descending
  const chartData = useMemo(
    () =>
      [...groups]
        .sort((a, b) => parseFloat(b.amount) - parseFloat(a.amount))
        .slice(0, 8)
        .map((g) => ({
          category: titleCase(g.key),
          amount: parseFloat(g.amount) || 0,
        })),
    [groups],
  );

  return (
    <Card data-testid="finance-category-chart-section">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Spending by category · 30d</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingLine />
        ) : chartData.length === 0 ? (
          <EmptyStateLine>No spending data for the last 30 days.</EmptyStateLine>
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
                <Tooltip formatter={(value: any) => [formatAmount(String(value), currency), "Spend"]} />
                <Bar dataKey="amount" fill="hsl(var(--primary))" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// ButlerFinanceFinancesTab — composed entry point
// ---------------------------------------------------------------------------

export default function ButlerFinanceFinancesTab() {
  // Compute date windows in owner timezone so month and 30d boundaries align
  // with the owner's locale rather than UTC.
  const today = new Date();
  const tz = OWNER_TZ_DEFAULT;
  const monthStart = formatInTimeZone(
    new Date(today.getFullYear(), today.getMonth(), 1),
    tz,
    "yyyy-MM-dd",
  );
  const monthEnd = formatInTimeZone(today, tz, "yyyy-MM-dd");

  // Compute 30-day window for category chart
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

  const transactions = txResp?.data ?? [];
  const subscriptions = subResp?.data ?? [];
  const upcomingBills = upcomingResp?.items ?? [];

  // Active sub count for KPI strip
  const activeSubCount = subscriptions.filter((s) => s.status === "active").length;

  // Next bill (first upcoming-bills item, soonest due)
  const nextBill = upcomingBills[0] ?? null;

  // Monthly spend and currency from spending summary
  const totalSpend = monthlySummary?.total_spend ?? "0";
  const currency = monthlySummary?.currency ?? "USD";

  const categoryGroups = categorySummary?.groups ?? [];
  const chartCurrency = categorySummary?.currency ?? "USD";

  return (
    <div className="space-y-4 pt-4" data-testid="finance-finances-tab">
      <FinanceKpiStrip
        totalSpend={totalSpend}
        currency={currency}
        activeSubCount={activeSubCount}
        nextBill={nextBill}
        isLoading={txLoading || subLoading || upcomingLoading || monthlyLoading}
      />
      <TransactionsSection transactions={transactions} isLoading={txLoading} />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <UpcomingBillsSection items={upcomingBills} isLoading={upcomingLoading} />
        <CategorySpendChart
          groups={categoryGroups}
          currency={chartCurrency}
          isLoading={categoryLoading}
        />
      </div>
      <SubscriptionsSection subscriptions={subscriptions} isLoading={subLoading} />
    </div>
  );
}

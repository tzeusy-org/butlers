// ---------------------------------------------------------------------------
// ButlerSpendTab — bu-iuol4.19, updated bu-wyami
//
// Spend & token usage bespoke tab for per-butler detail pages.
//
// Layout (4-col panel grid, 3 rows):
//   Row 1: KPI strip (4 cells, full width):
//     spend today | spend 30d | cost/session (30d) | tokens today (in / out)
//   Row 2: full-width bar trend — DayBars per RangeToggle (24h / 7d / 30d)
//   Row 3: model breakdown KV list — model name, $X · Y% of total cost
//
// ?butler= filter status:
//   - /api/costs/summary: supports ?butler= since bu-iuol4.12. All summary
//     calls (today + 30d) pass butlerName for per-butler scoping.
//   - /api/costs/daily: does NOT yet support ?butler= (tracked in bu-lryu6).
//     The butler param is wired through getDailyCosts/useDailyCosts for
//     forward compatibility; trend data is still all-butler until bu-lryu6
//     lands. The trend panel carries an "all butlers" subtitle to make clear
//     the chart is not yet butler-scoped. Remove it when bu-lryu6 ships.
//   - tokens today, cost/session: derived from butler-scoped summary.
//
// Currency formatter:
//   Uses Intl.NumberFormat for locale-aware USD display — same approach as
//   ButlerFinanceFinancesTab. No inline template literals.
// ---------------------------------------------------------------------------

import { useState, useMemo } from "react";
import type { ReactNode } from "react";
import { subDays } from "date-fns";

import { useCostSummary, useDailyCosts } from "@/hooks/use-costs";
import { startOfDayInTz, endOfDayInTz } from "@/components/chronicles/tz-format";
import { OWNER_TZ_DEFAULT } from "@/hooks/use-time-window";
import { DayBars } from "@/components/butlers/DayBars";
import { RangeToggle } from "@/components/ui/range-toggle";
import type { RangeValue } from "@/components/ui/range-toggle";
import { Panel, KpiCell, ErrorLine } from "@/components/butler-detail/atoms";

// ---------------------------------------------------------------------------
// Format helpers
// ---------------------------------------------------------------------------

/**
 * Format a number as a locale-aware USD currency string.
 * Uses Intl.NumberFormat — no inline "$" template literals.
 */
function formatCurrency(amount: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(amount);
}

/**
 * Compact token count: "1.2M", "45K", "123" etc.
 */
function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

// ---------------------------------------------------------------------------
// Shared primitives
// ---------------------------------------------------------------------------

function LoadingLine() {
  return (
    <p className="text-sm text-muted-foreground" data-testid="loading-line">
      Loading...
    </p>
  );
}

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

// ---------------------------------------------------------------------------
// Row 2: Bar trend panel
// ---------------------------------------------------------------------------

interface TrendPanelProps {
  range: RangeValue;
  onRangeChange: (r: RangeValue) => void;
  data: number[];
  isLoading: boolean;
  isError: boolean;
}

function TrendPanel({ range, onRangeChange, data, isLoading, isError }: TrendPanelProps) {
  return (
    <Panel
      title="Daily spend trend"
      sub="all butlers"
      span={4}
      testId="spend-trend-section"
    >
      <div className="flex items-center justify-between mb-3">
        <RangeToggle value={range} onChange={onRangeChange} disabled={isLoading} />
      </div>
      {isError ? (
        <ErrorLine>Could not load spend trend.</ErrorLine>
      ) : isLoading ? (
        <LoadingLine />
      ) : data.length === 0 ? (
        <EmptyLine>No spend data for this period.</EmptyLine>
      ) : (
        <DayBars
          data={data}
          height={48}
          color="bg-primary"
          className="w-full"
        />
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Row 3: Model breakdown panel
// ---------------------------------------------------------------------------

interface ModelBreakdownPanelProps {
  byModel: Record<string, number>;
  totalCost: number;
  isLoading: boolean;
  isError: boolean;
}

function ModelBreakdownPanel({ byModel, totalCost, isLoading, isError }: ModelBreakdownPanelProps) {
  // Sort models descending by cost
  const rows = useMemo(() => {
    const entries = Object.entries(byModel);
    entries.sort(([, a], [, b]) => b - a);
    return entries;
  }, [byModel]);

  return (
    <Panel
      title="Model breakdown"
      span={4}
      testId="spend-model-breakdown-section"
    >
      {isError ? (
        <ErrorLine>Could not load model breakdown.</ErrorLine>
      ) : isLoading ? (
        <LoadingLine />
      ) : rows.length === 0 ? (
        <EmptyLine>No model usage data available.</EmptyLine>
      ) : (
        <ul className="divide-y" data-testid="model-breakdown-list">
          {rows.map(([model, cost]) => {
            const pct =
              totalCost > 0 ? ((cost / totalCost) * 100).toFixed(1) : "0.0";
            return (
              <li
                key={model}
                className="flex items-center justify-between py-2 gap-4 min-w-0"
                data-testid="model-breakdown-row"
              >
                <span className="text-sm font-mono truncate min-w-0">{model}</span>
                <span className="text-sm font-mono tnum shrink-0 text-muted-foreground">
                  <span className="text-foreground">{formatCurrency(cost)}</span>
                  {" · "}
                  {pct}%
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// ButlerSpendTab — composed entry point
// ---------------------------------------------------------------------------

interface ButlerSpendTabProps {
  butlerName: string;
}

export default function ButlerSpendTab({ butlerName }: ButlerSpendTabProps) {
  const [range, setRange] = useState<RangeValue>("7d");

  // Stable "today" end-of-day in owner TZ — avoids both render-time drift and
  // the need to suppress react-hooks/exhaustive-deps on the trendFrom memo.
  const todayEnd = useMemo(() => endOfDayInTz(new Date(), OWNER_TZ_DEFAULT), []);

  // "today" period for KPI cell 1 — scoped to this butler via ?butler=
  const {
    data: todaySummary,
    isLoading: todayLoading,
    isError: todayError,
  } = useCostSummary("today", undefined, undefined, butlerName);

  // "30d" period for KPI cell 2 + model breakdown — scoped to this butler
  const {
    data: summary30d,
    isLoading: loading30d,
    isError: error30d,
  } = useCostSummary("30d", undefined, undefined, butlerName);

  // Date window for the bar trend — owner-TZ day boundaries
  const trendFrom = useMemo(() => {
    const days = range === "24h" ? 1 : range === "7d" ? 6 : 29;
    return startOfDayInTz(subDays(todayEnd, days), OWNER_TZ_DEFAULT);
  }, [range, todayEnd]);

  // Trend — butler param wired for forward compat; /daily filter lands in bu-lryu6
  const {
    data: dailyCostsResp,
    isLoading: dailyLoading,
    isError: dailyError,
  } = useDailyCosts(trendFrom, todayEnd, undefined, butlerName);

  // ---------------------------------------------------------------------------
  // Derived KPI values — all per-butler (summary queries pass ?butler=)
  // ---------------------------------------------------------------------------

  // KPI 1: Spend today — total_cost_usd from butler-scoped summary
  const spendToday = todaySummary
    ? (todaySummary.data?.total_cost_usd ?? 0)
    : null;
  const spendTodayValue = todayLoading
    ? "..."
    : todayError
      ? "—"
      : spendToday != null
        ? formatCurrency(spendToday)
        : "—";

  // KPI 2: Spend 30d — total_cost_usd from butler-scoped summary
  const spend30d = summary30d
    ? (summary30d.data?.total_cost_usd ?? 0)
    : null;
  const spend30dValue = loading30d
    ? "..."
    : error30d
      ? "—"
      : spend30d != null
        ? formatCurrency(spend30d)
        : "—";

  // KPI 3: Cost per session — per-butler 30d cost / per-butler session count
  const total30dCost = summary30d?.data?.total_cost_usd ?? 0;
  const total30dSessions = summary30d?.data?.total_sessions ?? 0;
  const costPerSession =
    total30dSessions > 0 ? total30dCost / total30dSessions : null;
  const costPerSessionValue = loading30d
    ? "..."
    : error30d
      ? "—"
      : costPerSession != null
        ? formatCurrency(costPerSession)
        : "—";

  // KPI 4: Tokens today in / out — per-butler via butler-scoped today summary
  const inputTokens = todaySummary?.data?.total_input_tokens ?? 0;
  const outputTokens = todaySummary?.data?.total_output_tokens ?? 0;
  const tokenValue = todayLoading
    ? "..."
    : todayError
      ? "—"
      : `${formatTokenCount(inputTokens)} in / ${formatTokenCount(outputTokens)} out`;

  // Bar trend data: cost_usd per day
  const trendData = useMemo(() => {
    const days = dailyCostsResp?.data ?? [];
    return days.map((d) => d.cost_usd);
  }, [dailyCostsResp]);

  // Model breakdown from butler-scoped 30d summary
  const byModel = summary30d?.data?.by_model ?? {};
  const modelBreakdownLoading = loading30d;
  const modelBreakdownError = error30d;

  return (
    <div
      className="grid grid-cols-1 lg:grid-cols-4 border-t border-l border-border/60"
      data-testid="spend-tab"
    >
      {/* Row 1: KPI strip — 4 cells */}
      <div
        className="col-span-1 lg:col-span-4 grid grid-cols-2 sm:grid-cols-4"
        data-testid="spend-kpi-strip"
      >
        <Panel>
          <div data-testid="kpi-value">
            <KpiCell
              label="Spend today"
              value={spendTodayValue}
              tone={spendToday != null && spendToday > 0 ? "fg" : "dim"}
            />
          </div>
        </Panel>
        <Panel>
          <div data-testid="kpi-value">
            <KpiCell
              label="Spend 30d"
              value={spend30dValue}
              tone={spend30d != null && spend30d > 0 ? "fg" : "dim"}
            />
          </div>
        </Panel>
        <Panel>
          <div data-testid="kpi-value">
            <KpiCell
              label="Cost / session · 30d"
              value={costPerSessionValue}
            />
          </div>
        </Panel>
        <Panel>
          <div data-testid="kpi-value">
            <KpiCell
              label="Tokens today"
              value={tokenValue}
            />
          </div>
        </Panel>
      </div>

      {/* Row 2: Bar trend full-width */}
      <TrendPanel
        range={range}
        onRangeChange={setRange}
        data={trendData}
        isLoading={dailyLoading}
        isError={dailyError}
      />

      {/* Row 3: Model breakdown full-width */}
      <ModelBreakdownPanel
        byModel={byModel}
        totalCost={total30dCost}
        isLoading={modelBreakdownLoading}
        isError={modelBreakdownError}
      />
    </div>
  );
}

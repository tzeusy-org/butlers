// ---------------------------------------------------------------------------
// ButlerSpendTab — bu-iuol4.19
//
// Spend & token usage bespoke tab for per-butler detail pages.
//
// Layout (4-col panel grid, 3 rows):
//   Row 1: KPI strip (4 cells, full width):
//     spend today | spend 30d | cost/session | tokens 24h (in / out)
//   Row 2: full-width bar trend — DayBars per RangeToggle (24h / 7d / 30d)
//   Row 3: model breakdown KV list — model name, $X · Y% of calls
//
// ?butler= filter status:
//   The backend /api/costs/summary and /api/costs/daily endpoints do NOT
//   yet support a ?butler= scoping parameter (tracked in bu-iuol4.12).
//   Until that lands:
//   - KPI cells: today/30d costs are derived from `by_butler[butlerName]`
//     so they are already per-butler scoped.
//   - tokens 24h, cost/session: derived from the global summary filtered
//     by butlerName — only cost is scoped; token counts are global estimates.
//   - Bar trend: uses /api/costs/daily which returns merged all-butler
//     data. Values are degraded (all butlers).
//   - Model breakdown: from summary `by_model` — all-butler data, degraded.
//   Degraded panels carry an "(all butlers)" note in their panel subtitle.
//
// Currency formatter:
//   Uses Intl.NumberFormat for locale-aware USD display — same approach as
//   ButlerFinanceFinancesTab. No inline template literals.
// ---------------------------------------------------------------------------

import { useState, useMemo } from "react";
import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

import { useCostSummary, useDailyCosts } from "@/hooks/use-costs";
import { DayBars } from "@/components/butlers/DayBars";
import { RangeToggle } from "@/components/ui/range-toggle";
import type { RangeValue } from "@/components/ui/range-toggle";
import { Panel, KpiCell } from "@/components/butler-detail/atoms";

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

function ErrorLine({ children }: { children: ReactNode }) {
  return (
    <p
      className="flex items-center gap-1.5 text-sm text-destructive min-w-0"
      data-testid="error-state-line"
    >
      <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span className="truncate">{children}</span>
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
      sub="all butlers"
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
                className="flex items-center justify-between py-2 gap-4"
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

  const today = new Date();

  // "today" period for KPI cell 1
  const {
    data: todaySummary,
    isLoading: todayLoading,
    isError: todayError,
  } = useCostSummary("today");

  // "30d" period for KPI cell 2 + model breakdown
  const {
    data: summary30d,
    isLoading: loading30d,
    isError: error30d,
  } = useCostSummary("30d");

  // Date window for the bar trend
  const trendFrom = useMemo(() => {
    const d = new Date(today);
    if (range === "24h") {
      d.setDate(d.getDate() - 1);
    } else if (range === "7d") {
      d.setDate(d.getDate() - 6);
    } else {
      d.setDate(d.getDate() - 29);
    }
    return d;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);

  const {
    data: dailyCostsResp,
    isLoading: dailyLoading,
    isError: dailyError,
  } = useDailyCosts(trendFrom, today);

  // ---------------------------------------------------------------------------
  // Derived KPI values — per-butler where available, all-butler fallback
  // ---------------------------------------------------------------------------

  // KPI 1: Spend today (per-butler via by_butler)
  const spendToday =
    todaySummary?.data?.by_butler?.[butlerName] ?? null;
  const spendTodayValue = todayLoading
    ? "..."
    : todayError
      ? "—"
      : spendToday != null
        ? formatCurrency(spendToday)
        : "—";

  // KPI 2: Spend 30d (per-butler via by_butler)
  const spend30d =
    summary30d?.data?.by_butler?.[butlerName] ?? null;
  const spend30dValue = loading30d
    ? "..."
    : error30d
      ? "—"
      : spend30d != null
        ? formatCurrency(spend30d)
        : "—";

  // KPI 3: Cost per session (global 30d — no per-butler session count available yet)
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

  // KPI 4: Tokens 24h in / out (global — no per-butler breakdown until bu-iuol4.12)
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

  // Model breakdown from 30d summary (all-butler data)
  const byModel = summary30d?.data?.by_model ?? {};
  const modelBreakdownLoading = loading30d;
  const modelBreakdownError = error30d;

  const kpiLoading = todayLoading || loading30d;

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
              sub={kpiLoading ? undefined : "all butlers"}
            />
          </div>
        </Panel>
        <Panel>
          <div data-testid="kpi-value">
            <KpiCell
              label="Tokens 24h"
              value={tokenValue}
              sub={kpiLoading ? undefined : "all butlers"}
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

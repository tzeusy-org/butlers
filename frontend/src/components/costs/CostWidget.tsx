import { Link } from "react-router";

import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { formatCostUsd } from "@/lib/format-cost";
import type { DailySpend, UnpricedModelUsage } from "@/api/types";

interface CostWidgetProps {
  totalCostUsd: number;
  topButler: string | null;
  topButlerCost: number;
  /**
   * Executed models omitted from the priced subtotal. When present, the
   * widget must not promote that subtotal as a truthful fleet total.
   */
  unpricedModels?: UnpricedModelUsage[];
  /** Compatibility summary envelope has placeholder totals rather than evidence. */
  sourceError?: boolean;
  isLoading?: boolean;
  /**
   * Real daily cost series for the trailing 7 days (from GET /api/spend/daily).
   * Renders the trend sparkline; when absent or empty, the sparkline is
   * replaced by an honest "trend unavailable" note rather than fabricated
   * bars.
   */
  dailyCosts?: DailySpend[];
  /** True when the daily-cost source failed to load. */
  dailyCostsError?: boolean;
  /** Compatibility daily envelope has an empty series rather than evidence. */
  dailySourceError?: boolean;
  /** Unpriced coverage in the daily series, independent from today's summary. */
  dailyUnpricedModels?: UnpricedModelUsage[];
}

export default function CostWidget({
  totalCostUsd,
  topButler,
  topButlerCost,
  unpricedModels = [],
  sourceError = false,
  isLoading,
  dailyCosts,
  dailyCostsError = false,
  dailySourceError = false,
  dailyUnpricedModels = [],
}: CostWidgetProps) {
  const unpricedCalls = unpricedModels.reduce((total, model) => total + model.calls, 0);
  const dailyUnpricedCalls = dailyUnpricedModels.reduce((total, model) => total + model.calls, 0);
  const hasUnpricedModels = unpricedModels.length > 0;

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Cost Today</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-16 rounded bg-muted" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium">Cost Today</CardTitle>
        <Button variant="ghost" size="sm" asChild>
          <Link to="/spend">View all</Link>
        </Button>
      </CardHeader>
      <CardContent>
        {sourceError ? (
          <SourceDegradedNote
            label="Cost source unavailable"
            detail="compatibility total hidden"
            testId="cost-widget-source-unavailable"
          />
        ) : hasUnpricedModels ? (
          <p className="text-2xl font-bold" data-testid="cost-widget-unpriced" aria-label="unpriced">
            {"—"}/unpriced
          </p>
        ) : (
          <div className="text-2xl font-bold">{formatCostUsd(totalCostUsd)}</div>
        )}
        {!sourceError && hasUnpricedModels ? (
          <p className="mt-1 text-xs text-muted-foreground">
            {unpricedCalls.toLocaleString()} unpriced {unpricedCalls === 1 ? "call" : "calls"} excluded
          </p>
        ) : !sourceError && topButler ? (
          <p className="mt-1 text-xs text-muted-foreground">
            Top: {topButler} ({formatCostUsd(topButlerCost)})
          </p>
        ) : null}
        {dailySourceError ? (
          <SourceDegradedNote
            className="mt-3"
            label="7-day trend"
            detail="cost source unavailable"
            testId="cost-widget-trend-source-unavailable"
          />
        ) : dailyCostsError ? (
          <p className="mt-3 text-xs text-muted-foreground" data-testid="cost-widget-trend-unavailable">
            7-day trend unavailable
          </p>
        ) : dailyCosts && dailyCosts.length > 0 ? (
          <>
            <div className="mt-3 flex h-8 items-end gap-0.5" data-testid="cost-widget-sparkline">
              {(() => {
                const max = Math.max(...dailyCosts.map((d) => d.cost_usd), 0);
                return dailyCosts.map((d) => (
                  <div
                    key={d.date}
                    className="flex-1 rounded-sm bg-primary/60"
                    title={`${d.date}: ${formatCostUsd(d.cost_usd)}`}
                    style={{ height: max > 0 ? `${Math.max(4, (d.cost_usd / max) * 100)}%` : "4%" }}
                  />
                ));
              })()}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">7-day trend</p>
          </>
        ) : (
          <p className="mt-3 text-xs text-muted-foreground" data-testid="cost-widget-trend-unavailable">
            7-day trend unavailable
          </p>
        )}
        {!dailySourceError && !dailyCostsError && dailyCosts && dailyCosts.length > 0 && dailyUnpricedCalls > 0 ? (
          <p className="mt-1 text-xs text-muted-foreground" data-testid="cost-widget-trend-unpriced">
            7-day trend excludes {dailyUnpricedCalls.toLocaleString()} unpriced {dailyUnpricedCalls === 1 ? "call" : "calls"}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

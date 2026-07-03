import { Link } from "react-router";

import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { formatCostUsd } from "@/lib/format-cost";
import type { DailySpend } from "@/api/types";

interface CostWidgetProps {
  totalCostUsd: number;
  topButler: string | null;
  topButlerCost: number;
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
}

export default function CostWidget({
  totalCostUsd,
  topButler,
  topButlerCost,
  isLoading,
  dailyCosts,
  dailyCostsError = false,
}: CostWidgetProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Cost Today</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-16 animate-pulse rounded bg-muted" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium">Cost Today</CardTitle>
        <Button variant="ghost" size="sm" asChild>
          <Link to="/costs">View all</Link>
        </Button>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{formatCostUsd(totalCostUsd)}</div>
        {topButler && (
          <p className="mt-1 text-xs text-muted-foreground">
            Top: {topButler} ({formatCostUsd(topButlerCost)})
          </p>
        )}
        {dailyCostsError ? (
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
      </CardContent>
    </Card>
  );
}

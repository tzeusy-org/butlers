import type { ApprovalMetrics } from "@/api/types";
import { Card, CardContent } from "@/components/ui/card";

interface ApprovalMetricsBarProps {
  metrics: ApprovalMetrics;
  /** Number of pending autonomy suggestions to display as a badge metric. */
  activeSuggestionsCount?: number;
}

export function ApprovalMetricsBar({ metrics, activeSuggestionsCount }: ApprovalMetricsBarProps) {
  const cols =
    activeSuggestionsCount !== undefined
      ? "grid gap-4 md:grid-cols-2 lg:grid-cols-6"
      : "grid gap-4 md:grid-cols-2 lg:grid-cols-5";

  return (
    <div className={cols}>
      <Card>
        <CardContent className="p-6">
          <div className="text-2xl font-bold">{metrics.total_pending}</div>
          <p className="text-xs text-muted-foreground">Pending</p>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="p-6">
          <div className="text-2xl font-bold text-green-600">
            {metrics.total_approved_today}
          </div>
          <p className="text-xs text-muted-foreground">Approved Today</p>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="p-6">
          <div className="text-2xl font-bold text-red-600">
            {metrics.total_rejected_today}
          </div>
          <p className="text-xs text-muted-foreground">Rejected Today</p>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="p-6">
          <div className="text-2xl font-bold">{metrics.active_rules_count}</div>
          <p className="text-xs text-muted-foreground">Active Rules</p>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="p-6">
          <div className="text-2xl font-bold">
            {metrics.auto_approval_rate.toFixed(1)}%
          </div>
          <p className="text-xs text-muted-foreground">Auto-Approval Rate</p>
        </CardContent>
      </Card>
      {activeSuggestionsCount !== undefined && (
        <Card>
          <CardContent className="p-6">
            <div
              className={`text-2xl font-bold ${activeSuggestionsCount > 0 ? "text-blue-600 dark:text-blue-400" : ""}`}
            >
              {activeSuggestionsCount}
            </div>
            <p className="text-xs text-muted-foreground">Active Suggestions</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

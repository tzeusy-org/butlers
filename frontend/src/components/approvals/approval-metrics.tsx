import type { ApprovalMetrics } from "@/api/types";
import { Card, CardContent } from "@/components/ui/card";

interface ApprovalMetricsBarProps {
  metrics: ApprovalMetrics;
}

export function ApprovalMetricsBar({ metrics }: ApprovalMetricsBarProps) {
  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
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
    </div>
  );
}

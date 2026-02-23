/**
 * Cross-connector summary statistics bar for the Connectors tab.
 */

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { CrossConnectorSummary } from "@/api/index.ts";

interface ConnectorSummaryBarProps {
  summary: CrossConnectorSummary | undefined;
  isLoading: boolean;
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 text-sm">
      <span className="text-muted-foreground">{label}:</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}

export function ConnectorSummaryBar({
  summary,
  isLoading,
}: ConnectorSummaryBarProps) {
  if (isLoading) {
    return <Skeleton className="h-6 w-full" />;
  }

  if (!summary) return null;

  return (
    <div className="flex flex-wrap gap-4 rounded-lg border p-3">
      <Stat label="Total" value={summary.total_connectors} />
      <Stat
        label="Online"
        value={
          <Badge variant="default" className="text-xs">
            {summary.connectors_online}
          </Badge>
        }
      />
      <Stat
        label="Stale"
        value={
          <Badge variant="outline" className="text-xs">
            {summary.connectors_stale}
          </Badge>
        }
      />
      <Stat
        label="Offline"
        value={
          <Badge variant="destructive" className="text-xs">
            {summary.connectors_offline}
          </Badge>
        }
      />
      <Stat
        label="Ingested"
        value={summary.total_messages_ingested.toLocaleString()}
      />
      <Stat
        label="Failed"
        value={summary.total_messages_failed.toLocaleString()}
      />
      <Stat
        label="Error rate"
        value={`${summary.overall_error_rate_pct.toFixed(1)}%`}
      />
    </div>
  );
}

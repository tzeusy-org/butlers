/**
 * Aggregate stat row for the Overview tab.
 *
 * Shows: total ingested 24h, skipped, metadata-only, LLM calls saved,
 * active connectors.
 */

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { CrossConnectorSummary } from "@/api/index.ts";

function StatCard({
  title,
  value,
  isLoading,
}: {
  title: string;
  value: string | number;
  isLoading?: boolean;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-8 w-20" />
        ) : (
          <div className="text-2xl font-bold">{value}</div>
        )}
      </CardContent>
    </Card>
  );
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

interface IngestionStatRowProps {
  summary: CrossConnectorSummary | undefined;
  isLoading: boolean;
}

export function IngestionStatRow({ summary, isLoading }: IngestionStatRowProps) {
  const ingested = summary?.total_messages_ingested ?? 0;
  const failed = summary?.total_messages_failed ?? 0;
  const total = ingested + failed;
  // Approximate: messages_failed treated as skipped; metadata-only not in summary
  // so show failed as "failed/skipped" and use error rate as a proxy
  const skipped = failed;
  const active = summary?.connectors_online ?? 0;

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
      <StatCard
        title="Ingested (period)"
        value={isLoading ? "" : formatNumber(ingested)}
        isLoading={isLoading}
      />
      <StatCard
        title="Failed / Skipped"
        value={isLoading ? "" : formatNumber(skipped)}
        isLoading={isLoading}
      />
      <StatCard
        title="Total Processed"
        value={isLoading ? "" : formatNumber(total)}
        isLoading={isLoading}
      />
      <StatCard
        title="Error Rate"
        value={
          isLoading ? "" : `${(summary?.overall_error_rate_pct ?? 0).toFixed(1)}%`
        }
        isLoading={isLoading}
      />
      <StatCard
        title="Active Connectors"
        value={isLoading ? "" : active}
        isLoading={isLoading}
      />
    </div>
  );
}

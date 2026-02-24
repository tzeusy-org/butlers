/**
 * Aggregate stat row for the Overview tab.
 *
 * Shows: total ingested (period), failed/skipped (tier3), total processed,
 * error rate, and active connectors.
 *
 * Data is sourced from /ingestion/overview which queries message_inbox for
 * period-scoped counts (not cumulative connector_registry counters).
 */

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { IngestionOverviewStats } from "@/api/index.ts";

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
  overview: IngestionOverviewStats | undefined;
  isLoading: boolean;
}

export function IngestionStatRow({ overview, isLoading }: IngestionStatRowProps) {
  // period-scoped ingested count from message_inbox
  const ingested = overview?.total_ingested ?? 0;
  // tier3 = skipped messages (per acceptance criteria)
  const skipped = overview?.tier3_skip_count ?? 0;
  // total processed = all tier counts combined
  const tier1 = overview?.tier1_full_count ?? 0;
  const tier2 = overview?.tier2_metadata_count ?? 0;
  const tier3 = overview?.tier3_skip_count ?? 0;
  const total = tier1 + tier2 + tier3;
  const active = overview?.active_connectors ?? 0;

  // Error rate: skipped / total, expressed as percentage
  const errorRate = total > 0 ? (skipped / total) * 100 : 0;

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
        value={isLoading ? "" : `${errorRate.toFixed(1)}%`}
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

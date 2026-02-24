/**
 * Tier breakdown donut chart for the Overview tab.
 *
 * Prefers real tier counts from IngestionOverviewStats (tier1_full_count,
 * tier2_metadata_count, tier3_skip_count) when available.
 *
 * Falls back to approximating tiers from CrossConnectorSummary:
 *   T1 (full ingested): messages_ingested - messages_failed
 *   T3 (skip/failed):   messages_failed
 *   T2 (metadata-only): not exposed by summary; placeholder 0
 *
 * This component renders cleanly when real tier data is unavailable.
 */

import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { CrossConnectorSummary, IngestionOverviewStats } from "@/api/index.ts";

const TIER_COLORS = [
  "hsl(var(--primary))",
  "hsl(var(--muted-foreground))",
  "hsl(var(--destructive))",
];

interface TierEntry {
  name: string;
  value: number;
}

interface TierBreakdownDonutProps {
  summary: CrossConnectorSummary | undefined;
  overview?: IngestionOverviewStats | undefined;
  isLoading: boolean;
}

export function TierBreakdownDonut({ summary, overview, isLoading }: TierBreakdownDonutProps) {
  // Prefer real tier counts from the ingestion overview endpoint when available
  let t1: number;
  let t2: number;
  let t3: number;

  if (overview) {
    t1 = overview.tier1_full_count;
    t2 = overview.tier2_metadata_count;
    t3 = overview.tier3_skip_count;
  } else {
    // Fallback: approximate from cross-connector summary
    const ingested = summary?.total_messages_ingested ?? 0;
    const failed = summary?.total_messages_failed ?? 0;
    t1 = Math.max(0, ingested - failed);
    t2 = 0;
    t3 = failed;
  }

  const data: TierEntry[] = [
    { name: "T1 Full", value: t1 },
    { name: "T2 Metadata", value: t2 },
    { name: "T3 Skip", value: t3 },
  ].filter((d) => d.value > 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Tier Breakdown</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-48 w-full" />
        ) : data.length === 0 ? (
          <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
            No tier data available
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={192}>
            <PieChart>
              <Pie
                data={data}
                cx="50%"
                cy="50%"
                innerRadius={50}
                outerRadius={75}
                dataKey="value"
                paddingAngle={2}
              >
                {data.map((entry, index) => (
                  <Cell
                    key={entry.name}
                    fill={TIER_COLORS[index % TIER_COLORS.length]}
                  />
                ))}
              </Pie>
              <Tooltip formatter={(value: number | undefined) => [(value ?? 0).toLocaleString(), ""]} />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

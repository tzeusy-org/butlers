/**
 * Volume trend chart: line/area chart of messages_ingested over time.
 * Used by both Overview and Connectors tabs.
 */

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PeriodSelector } from "./PeriodSelector";
import type { ConnectorStatsBucket, IngestionPeriod } from "@/api/index.ts";

function formatBucket(bucket: string, period: IngestionPeriod): string {
  const d = new Date(bucket);
  if (period === "30d") {
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function formatCount(value: number): string {
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

interface VolumeTrendChartProps {
  data: ConnectorStatsBucket[];
  period: IngestionPeriod;
  onPeriodChange: (period: IngestionPeriod) => void;
  isLoading: boolean;
  title?: string;
}

export function VolumeTrendChart({
  data,
  period,
  onPeriodChange,
  isLoading,
  title = "Volume Trend",
}: VolumeTrendChartProps) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>{title}</CardTitle>
        <PeriodSelector value={period} onChange={onPeriodChange} />
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : data.length === 0 ? (
          <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
            No data available
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={256}>
            <AreaChart data={data} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
              <defs>
                <linearGradient id="ingestedGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="failedGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="hsl(var(--destructive))" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="hsl(var(--destructive))" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="bucket"
                tickFormatter={(v: string) => formatBucket(v, period)}
                tick={{ fontSize: 11 }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                tickFormatter={formatCount}
                tick={{ fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                width={50}
              />
              <Tooltip
                formatter={(value: number, name: string) => [
                  formatCount(value),
                  name === "messages_ingested" ? "Ingested" : "Failed",
                ]}
                labelFormatter={(label: string) => formatBucket(label, period)}
              />
              <Legend
                formatter={(value: string) =>
                  value === "messages_ingested" ? "Ingested" : "Failed"
                }
              />
              <Area
                type="monotone"
                dataKey="messages_ingested"
                stroke="hsl(var(--primary))"
                fill="url(#ingestedGradient)"
                strokeWidth={2}
              />
              <Area
                type="monotone"
                dataKey="messages_failed"
                stroke="hsl(var(--destructive))"
                fill="url(#failedGradient)"
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

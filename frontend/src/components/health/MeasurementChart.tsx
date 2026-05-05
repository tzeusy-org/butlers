import { useMemo, useState } from "react";
import { format } from "date-fns";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { Measurement, MeasurementParams } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useMeasurements } from "@/hooks/use-health";
import { Time } from "@/components/ui/time";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MEASUREMENT_TYPES = [
  "weight",
  "blood_pressure",
  "heart_rate",
  "glucose",
  "temperature",
  "sleep",
  "oxygen",
] as const;

const CHART_COLORS = {
  primary: "#3b82f6",
  secondary: "#f43f5e",
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface MeasurementChartProps {
  /** Override the default type filter. */
  initialType?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Extract a numeric value from a measurement for charting. */
function extractValue(m: Measurement, key: string): number | null {
  const v = m.value[key];
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isNaN(n) ? null : n;
  }
  return null;
}

/** Format a measurement's value object for display. */
function formatValue(m: Measurement): string {
  const entries = Object.entries(m.value);
  if (entries.length === 0) return "\u2014";
  return entries.map(([k, v]) => `${k}: ${v}`).join(", ");
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ChartSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-64 w-full" />
      <div className="flex gap-2">
        <Skeleton className="h-8 w-20" />
        <Skeleton className="h-8 w-20" />
        <Skeleton className="h-8 w-20" />
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      title="No measurements found."
      description="No data available for this type and date range."
    />
  );
}

// ---------------------------------------------------------------------------
// MeasurementChart
// ---------------------------------------------------------------------------

export default function MeasurementChart({ initialType }: MeasurementChartProps) {
  const [activeType, setActiveType] = useState(initialType ?? "weight");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [showTable, setShowTable] = useState(false);

  const params: MeasurementParams = {
    type: activeType,
    since: since || undefined,
    until: until || undefined,
    limit: 500,
  };

  const { data, isLoading } = useMeasurements(params);
  const measurements = data?.data ?? [];

  // Build chart data
  const chartData = useMemo(() => {
    if (!measurements.length) return [];

    const sorted = [...measurements].sort(
      (a, b) => new Date(a.measured_at).getTime() - new Date(b.measured_at).getTime(),
    );

    if (activeType === "blood_pressure") {
      return sorted.map((m) => ({
        date: format(new Date(m.measured_at), "MMM d"),
        systolic: extractValue(m, "systolic"),
        diastolic: extractValue(m, "diastolic"),
      }));
    }

    // For single-value types, try common keys
    return sorted.map((m) => {
      const keys = Object.keys(m.value);
      const key = keys.includes("value") ? "value" : keys[0] ?? "value";
      return {
        date: format(new Date(m.measured_at), "MMM d"),
        value: extractValue(m, key),
      };
    });
  }, [measurements, activeType]);

  const isBP = activeType === "blood_pressure";

  return (
    <div className="space-y-4">
      {/* Type tabs */}
      <div className="flex flex-wrap items-center gap-2">
        {MEASUREMENT_TYPES.map((t) => (
          <Badge
            key={t}
            variant={activeType === t ? "default" : "outline"}
            className="cursor-pointer"
            onClick={() => setActiveType(t)}
          >
            {t.replace(/_/g, " ")}
          </Badge>
        ))}
      </div>

      {/* Date range filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <label className="text-muted-foreground text-sm">From</label>
          <Input
            type="date"
            value={since}
            onChange={(e) => setSince(e.target.value)}
            className="w-40"
          />
        </div>
        <div className="flex items-center gap-2">
          <label className="text-muted-foreground text-sm">To</label>
          <Input
            type="date"
            value={until}
            onChange={(e) => setUntil(e.target.value)}
            className="w-40"
          />
        </div>
        {(since || until) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSince("");
              setUntil("");
            }}
          >
            Clear
          </Button>
        )}
      </div>

      {/* Chart */}
      {isLoading ? (
        <ChartSkeleton />
      ) : chartData.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="h-72 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
              <XAxis dataKey="date" className="text-xs" />
              <YAxis className="text-xs" />
              <Tooltip />
              {isBP ? (
                <>
                  <Line
                    type="monotone"
                    dataKey="systolic"
                    stroke={CHART_COLORS.primary}
                    strokeWidth={2}
                    dot={false}
                    name="Systolic"
                  />
                  <Line
                    type="monotone"
                    dataKey="diastolic"
                    stroke={CHART_COLORS.secondary}
                    strokeWidth={2}
                    dot={false}
                    name="Diastolic"
                  />
                </>
              ) : (
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke={CHART_COLORS.primary}
                  strokeWidth={2}
                  dot={false}
                  name={activeType.replace(/_/g, " ")}
                />
              )}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Raw data table toggle */}
      {measurements.length > 0 && (
        <div className="space-y-2">
          <Button variant="outline" size="sm" onClick={() => setShowTable((v) => !v)}>
            {showTable ? "Hide" : "Show"} raw data
          </Button>
          {showTable && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Value</TableHead>
                  <TableHead>Notes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {measurements.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell className="text-sm">
                      <Time value={m.measured_at} mode="absolute" precision="minute" compact />
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">
                        {m.type}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {formatValue(m)}
                    </TableCell>
                    <TableCell className="text-muted-foreground max-w-xs truncate text-sm">
                      {m.notes ?? "\u2014"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      )}
    </div>
  );
}

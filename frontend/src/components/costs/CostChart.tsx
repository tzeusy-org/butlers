import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Button } from "../ui/button";
import { chartColor } from "@/lib/chart-colors";

interface DailyCostData {
  date: string;
  cost_usd: number;
  sessions: number;
}

type Period = "7d" | "30d" | "90d";

interface CostChartProps {
  data: DailyCostData[];
  isLoading?: boolean;
  period: Period;
  onPeriodChange: (period: Period) => void;
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function formatCost(value: number): string {
  return `$${value.toFixed(2)}`;
}

const periods: Array<{ value: Period; label: string }> = [
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
];

export default function CostChart({
  data,
  isLoading,
  period,
  onPeriodChange,
}: CostChartProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Spending Over Time</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-64 animate-pulse rounded bg-muted" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Spending Over Time</CardTitle>
        <div className="flex gap-1">
          {periods.map((p) => (
            <Button
              key={p.value}
              variant={period === p.value ? "secondary" : "ghost"}
              size="sm"
              onClick={() => onPeriodChange(p.value)}
            >
              {p.label}
            </Button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
            No cost data available
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={256}>
            <AreaChart
              data={data}
              margin={{ top: 5, right: 5, bottom: 5, left: 0 }}
            >
              <defs>
                <linearGradient id="costGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop
                    offset="5%"
                    stopColor={chartColor()}
                    stopOpacity={0.3}
                  />
                  <stop
                    offset="95%"
                    stopColor={chartColor()}
                    stopOpacity={0}
                  />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="date"
                tickFormatter={formatDate}
                tick={{ fontSize: 12 }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                tickFormatter={formatCost}
                tick={{ fontSize: 12 }}
                tickLine={false}
                axisLine={false}
                width={60}
              />
              <Tooltip
                formatter={(value: number | undefined) => [formatCost(value ?? 0), "Cost"]}
                labelFormatter={(label: unknown) => formatDate(String(label))}
              />
              <Area
                type="monotone"
                dataKey="cost_usd"
                stroke={chartColor()}
                fill="url(#costGradient)"
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

export type { DailyCostData, Period, CostChartProps };

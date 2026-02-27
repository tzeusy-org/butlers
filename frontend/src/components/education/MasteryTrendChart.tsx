import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useMindMapAnalytics } from "@/hooks/use-education";

interface MasteryTrendChartProps {
  mindMapId: string | null;
}

export default function MasteryTrendChart({ mindMapId }: MasteryTrendChartProps) {
  const { data: analytics } = useMindMapAnalytics(mindMapId, 30);

  const trendData = (analytics?.trend ?? []).map((entry) => ({
    date: entry.snapshot_date,
    mastery: Math.round(((entry.metrics?.mastery_pct as number) ?? 0) * 100),
  }));

  if (!mindMapId) return null;

  if (trendData.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Mastery Trend</CardTitle>
        </CardHeader>
        <CardContent className="flex h-72 items-center justify-center text-muted-foreground">
          Analytics will appear after the butler computes its first daily snapshot
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Mastery Trend (30 days)</CardTitle>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={288}>
          <AreaChart data={trendData}>
            <XAxis dataKey="date" tick={{ fontSize: 12 }} />
            <YAxis domain={[0, 100]} tick={{ fontSize: 12 }} unit="%" />
            <Tooltip
              formatter={(value: number) => [`${value}%`, "Mastery"]}
            />
            <Area
              type="monotone"
              dataKey="mastery"
              stroke="#3b82f6"
              fill="#3b82f6"
              fillOpacity={0.2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useCrossTopicAnalytics } from "@/hooks/use-education";

export default function CrossTopicChart() {
  const { data: analytics } = useCrossTopicAnalytics();

  if (!analytics || analytics.topics.length === 0) {
    return null;
  }

  const chartData = analytics.topics.map((t) => ({
    name: t.title,
    mastery: Math.round(t.mastery_pct * 100),
  }));

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Cross-Topic Portfolio</CardTitle>
          <span className="text-sm text-muted-foreground">
            Overall: {Math.round(analytics.portfolio_mastery * 100)}%
          </span>
        </div>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={chartData}>
            <XAxis dataKey="name" tick={{ fontSize: 12 }} />
            <YAxis domain={[0, 100]} tick={{ fontSize: 12 }} unit="%" />
            <Tooltip
              formatter={(value: number) => [`${value}%`, "Mastery"]}
            />
            <Bar dataKey="mastery" fill="#3b82f6" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

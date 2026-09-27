import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Section, SectionContent, SectionHeader, SectionTitle } from "@/components/ui/Section";
import { useCrossTopicAnalytics } from "@/hooks/use-education";
import { chartColor } from "@/lib/chart-colors";

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
    <Section>
      <SectionHeader>
        <div className="flex items-center justify-between">
          <SectionTitle>Cross-Topic Portfolio</SectionTitle>
          <span className="text-sm text-muted-foreground">
            Overall: {Math.round(analytics.portfolio_mastery * 100)}%
          </span>
        </div>
      </SectionHeader>
      <SectionContent>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={chartData}>
            <XAxis dataKey="name" tick={{ fontSize: 12 }} />
            <YAxis domain={[0, 100]} tick={{ fontSize: 12 }} unit="%" />
            <Tooltip
              formatter={(value: number | undefined) => [`${value ?? 0}%`, "Mastery"]}
            />
            <Bar dataKey="mastery" fill={chartColor()} radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </SectionContent>
    </Section>
  );
}

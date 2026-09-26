import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Section, SectionContent, SectionHeader, SectionTitle } from "@/components/ui/Section";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { useMindMapAnalytics } from "@/hooks/use-education";
import { chartColor } from "@/lib/chart-colors";

interface MasteryTrendChartProps {
  mindMapId: string | null;
}

export default function MasteryTrendChart({ mindMapId }: MasteryTrendChartProps) {
  const { data: analytics, isError, refetch } = useMindMapAnalytics(mindMapId, 30);

  const trendData = (analytics?.trend ?? []).map((entry) => ({
    date: entry.snapshot_date,
    mastery: Math.round(((entry.metrics?.mastery_pct as number) ?? 0) * 100),
  }));

  if (!mindMapId) return null;

  if (isError) {
    return (
      <Section>
        <SectionHeader>
          <SectionTitle>Mastery Trend</SectionTitle>
        </SectionHeader>
        <SectionContent className="flex h-72 items-center justify-center">
          <SourceDegradedNote
            label="Mastery trend"
            detail="could not be reached"
            onRetry={() => void refetch()}
            testId="mastery-trend-chart-degraded"
          />
        </SectionContent>
      </Section>
    );
  }

  if (trendData.length === 0) {
    return (
      <Section>
        <SectionHeader>
          <SectionTitle>Mastery Trend</SectionTitle>
        </SectionHeader>
        <SectionContent className="flex h-72 items-center justify-center text-muted-foreground">
          Analytics will appear after the butler computes its first daily snapshot
        </SectionContent>
      </Section>
    );
  }

  return (
    <Section>
      <SectionHeader>
        <SectionTitle>Mastery Trend (30 days)</SectionTitle>
      </SectionHeader>
      <SectionContent>
        <ResponsiveContainer width="100%" height={288}>
          <AreaChart data={trendData}>
            <XAxis dataKey="date" tick={{ fontSize: 12 }} />
            <YAxis domain={[0, 100]} tick={{ fontSize: 12 }} unit="%" />
            <Tooltip
              formatter={(value: number | undefined) => [`${value ?? 0}%`, "Mastery"]}
            />
            <Area
              type="monotone"
              dataKey="mastery"
              stroke={chartColor()}
              fill={chartColor()}
              fillOpacity={0.2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </SectionContent>
    </Section>
  );
}

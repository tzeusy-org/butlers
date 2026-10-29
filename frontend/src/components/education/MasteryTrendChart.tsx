import { useMemo } from "react";
import { Section, SectionContent, SectionHeader, SectionTitle } from "@/components/ui/Section";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { useMindMapAnalytics } from "@/hooks/use-education";
import { TimeSeriesChart } from "@/components/ui/TimeSeriesChart";
import { useTickingNow } from "@/hooks/use-ticking-now";
import { chartColor } from "@/lib/chart-colors";
import { DAY_MS, buildTimeSeries } from "@/lib/time-series";

const TREND_DAYS = 30;
interface MasteryTrendChartProps {
  mindMapId: string | null;
}

export default function MasteryTrendChart({ mindMapId }: MasteryTrendChartProps) {
  const { data: analytics, isError, refetch } = useMindMapAnalytics(mindMapId, TREND_DAYS);

  const windowEnd = useTickingNow(60_000);
  const series = useMemo(() => {
    return buildTimeSeries(
      (analytics?.trend ?? []).flatMap((entry) => {
        const at = entry.snapshot_date;
        if (typeof at !== "string" && typeof at !== "number" && !(at instanceof Date)) return [];
        const metrics = entry.metrics;
        const mastery = metrics !== null && typeof metrics === "object" && "mastery_pct" in metrics
          ? metrics.mastery_pct : undefined;
        return [{ at, values: { mastery: typeof mastery === "number" ? Math.round(mastery * 100) : null } }];
      }),
      ["mastery"],
      { windowStart: windowEnd - TREND_DAYS * DAY_MS, windowEnd, maxGapMs: 3 * DAY_MS },
    );
  }, [analytics, windowEnd]);

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

  if (series.observationCount === 0) {
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
        <TimeSeriesChart
          variant="area"
          series={series}
          lines={[{ key: "mastery", name: "Mastery", stroke: chartColor() }]}
          height={288}
          yDomain={[0, 100]}
          yUnit="%"
        />
      </SectionContent>
    </Section>
  );
}

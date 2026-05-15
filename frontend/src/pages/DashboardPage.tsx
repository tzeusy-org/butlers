/**
 * DashboardPage -- editorial archetype landing for the Overview page.
 *
 * Replaces the vertical-D overview layout with the two-column editorial
 * archetype: left column carries the narrative (date eyebrow, Display
 * headline, Voice paragraph, attention list, KPI strip) and right column
 * carries the index (ButlerIndex, NextList).
 *
 * Layout: two columns 1.4fr / 1fr, gap 56px.
 * Frame: <Page archetype="editorial"> (max-width 1280px, padding 48px 56px).
 *
 * Data:
 *   useBriefing()           -- DateEyebrow, BriefingStatus, Headline, Elaboration
 *   useIssues()             -- AttentionList
 *   useButlers()            -- ButlerIndex, RuntimeSummaryKpi
 *   useCostSummary("today") -- ButlerIndex per-butler cost
 *   useApprovalMetrics()    -- RuntimeSummaryKpi "approvals" cell, NextList pending approvals
 *
 * bu-1fpvp.2 -- Frontend: replace DashboardPage with editorial layout.
 * bu-bm58r.1 -- Runtime summary KPI card from existing hooks.
 */

import { Page } from "@/components/ui/page";
import { useBriefing } from "@/hooks/use-briefing";
import { useButlers } from "@/hooks/use-butlers";
import { useCostSummary } from "@/hooks/use-costs";
import { useIssues } from "@/hooks/use-issues";
import { useApprovalMetrics } from "@/hooks/use-approvals";
import { useButlerHeartbeats } from "@/hooks/use-system";
import { useNotificationStats } from "@/hooks/use-notifications";
import { useQaSummary } from "@/hooks/use-qa";
import { useTimeline } from "@/hooks/use-timeline";

import { AttentionList } from "@/components/overview/AttentionList";
import { BriefingStatus } from "@/components/overview/BriefingStatus";
import { ButlerIndex } from "@/components/overview/ButlerIndex";
import { DateEyebrow } from "@/components/overview/DateEyebrow";
import { Elaboration } from "@/components/overview/Elaboration";
import { Headline } from "@/components/overview/Headline";
import { NextList } from "@/components/overview/NextList";
import { RuntimeSummaryKpi } from "@/components/overview/RuntimeSummaryKpi";
import { Section } from "@/components/overview/Section";
import { deriveOverviewTriageModel } from "@/components/overview/model";

export default function DashboardPage() {
  // Briefing
  const {
    data: briefing,
    isFetching: briefingFetching,
    refetch: refetchBriefing,
  } = useBriefing();

  // Supporting data
  const butlersQuery = useButlers();
  const costQuery = useCostSummary("today");
  const issuesQuery = useIssues();
  const approvalMetricsQuery = useApprovalMetrics();
  const heartbeatQuery = useButlerHeartbeats();
  const notificationStatsQuery = useNotificationStats();
  const qaSummaryQuery = useQaSummary();
  const timelineQuery = useTimeline({ limit: 5 });

  // Derived values
  const model = deriveOverviewTriageModel({
    butlers: butlersQuery.data?.data ?? [],
    costs: costQuery.isError ? null : costQuery.data?.data,
    issues: issuesQuery.data?.data ?? [],
    heartbeats: heartbeatQuery.isError ? null : heartbeatQuery.data?.data,
    approvalMetrics: approvalMetricsQuery.isError ? null : approvalMetricsQuery.data?.data,
    notificationStats: notificationStatsQuery.isError ? null : notificationStatsQuery.data?.data,
    qaSummary: qaSummaryQuery.isError ? null : qaSummaryQuery.data?.data,
    timeline: timelineQuery.data?.data ?? [],
  });

  const nextItems = model.nowRows.map((row) => ({
    time: "now",
    label: row.label,
    kind: row.kind,
  }));

  // Briefing headline and greet with safe fallbacks
  const greet = briefing?.greet ?? "Good morning.";
  const headline = briefing?.headline ?? "Checking in.";
  const elaboration =
    briefing?.elaboration ??
    "Butlers are running. Check back in a moment for a fresh briefing.";

  return (
    <Page archetype="editorial" title="Overview">
      {/* Two-column editorial grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1.4fr 1fr",
          gap: "56px",
          alignItems: "start",
        }}
      >
        {/* Left column: narrative */}
        <div
          style={{ display: "flex", flexDirection: "column", gap: "28px" }}
          aria-label="Briefing"
        >
          {/* Date eyebrow with briefing status pill */}
          <DateEyebrow
            statusSlot={
              <BriefingStatus
                source={briefing?.source}
                generatedAt={briefing?.generated_at}
                isFetching={briefingFetching}
                onRefetch={() => { void refetchBriefing(); }}
              />
            }
          />

          {/* Display headline */}
          <Headline greet={greet} body={headline} />

          {/* Voice elaboration paragraph */}
          <Elaboration text={elaboration} isFetching={briefingFetching} />

          <Section eyebrow="Needs attention">
            <AttentionList items={model.attentionRows} />
          </Section>

          <RuntimeSummaryKpi
            kpis={model.kpis}
            isLoading={butlersQuery.isLoading}
            pendingApprovalsAvailable={!approvalMetricsQuery.isError && approvalMetricsQuery.data != null}
          />
        </div>

        {/* Right column: index */}
        <div
          style={{ display: "flex", flexDirection: "column", gap: "32px" }}
          aria-label="Operations and now"
        >
          <ButlerIndex butlers={model.operationsRows} />
          <NextList items={nextItems} />
        </div>
      </div>
    </Page>
  );
}

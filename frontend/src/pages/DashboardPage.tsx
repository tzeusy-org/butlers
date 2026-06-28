/**
 * DashboardPage -- operational triage cockpit for the Overview page.
 *
 * Composes the full triage cockpit using the editorial archetype:
 *   - Left column (narrative): date eyebrow + briefing status, Display headline,
 *     Voice elaboration paragraph, Needs-attention list, KPI strip.
 *   - Right column (index): enriched Butler index (Operations), operations-now
 *     signal list (Now).
 *
 * Responsive layout:
 *   - < lg  (< 1024px): single column, narrative on top, index below.
 *   - ≥ lg  (≥ 1024px): two columns at 1.4fr / 1fr, gap 56px.
 *   Frame: <Page archetype="editorial"> (max-width 1280px, responsive padding).
 *
 * Data sources (no backend aggregation endpoint required):
 *   useBriefing()           -- DateEyebrow, BriefingStatus, Headline, Elaboration
 *   useIssues()             -- AttentionList (client-side stale/severity ordering)
 *   useButlers()            -- ButlerIndex, RuntimeSummaryKpi
 *   useSpendSummary("today") -- ButlerIndex per-butler cost
 *   useApprovalMetrics()    -- KPI "approvals" cell, OperationsNowList approvals row
 *   useButlerHeartbeats()   -- RuntimeSummaryKpi runtime state, stale detection
 *   useNotificationStats()  -- OperationsNowList notification pressure row
 *   useQaSummary()          -- OperationsNowList QA state row
 *   useTimeline()           -- OperationsNowList recent activity rows
 *
 * bu-1fpvp.2   -- Frontend: replace DashboardPage with editorial layout.
 * bu-bm58r.1   -- Runtime summary KPI card from existing hooks.
 * bu-tn1po.3   -- Needs-attention list (AttentionList).
 * bu-tn1po.4   -- Promoted KPI strip + enriched butler index (ButlerIndex).
 * bu-tn1po.5   -- Operations-now signal list (OperationsNowList).
 * bu-tn1po.6   -- Compose all surfaces into this triage cockpit page.
 */

import { Page } from "@/components/ui/page";
import { useBriefing } from "@/hooks/use-briefing";
import { useButlers } from "@/hooks/use-butlers";
import { useSpendSummary, useTopSessions } from "@/hooks/use-spend";
import { useIssues } from "@/hooks/use-issues";
import { useApprovalMetrics } from "@/hooks/use-approvals";
import { useButlerHeartbeats } from "@/hooks/use-system";
import { useNotificationStats } from "@/hooks/use-notifications";
import { useQaSummary } from "@/hooks/use-qa";
import { useTimeline } from "@/hooks/use-timeline";

import CostWidget from "@/components/costs/CostWidget";
import TopSessionsTable from "@/components/costs/TopSessionsTable";

import { AttentionList } from "@/components/overview/AttentionList";
import { BriefingStatus } from "@/components/overview/BriefingStatus";
import { ButlerIndex } from "@/components/overview/ButlerIndex";
import { DateEyebrow } from "@/components/overview/DateEyebrow";
import { Elaboration } from "@/components/overview/Elaboration";
import { Headline } from "@/components/overview/Headline";
import { OperationsNowList } from "@/components/overview/OperationsNowList";
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
  const costQuery = useSpendSummary("today");
  const issuesQuery = useIssues();
  const approvalMetricsQuery = useApprovalMetrics();
  const heartbeatQuery = useButlerHeartbeats();
  const notificationStatsQuery = useNotificationStats();
  const qaSummaryQuery = useQaSummary();
  const timelineQuery = useTimeline({ limit: 5 });
  const topSessionsQuery = useTopSessions();

  // Derived values
  const model = deriveOverviewTriageModel({
    butlers: butlersQuery.isError ? [] : (butlersQuery.data?.data ?? []),
    butlersError: butlersQuery.isError,
    costs: costQuery.isError ? null : costQuery.data?.data,
    issues: issuesQuery.data?.data ?? [],
    heartbeats: heartbeatQuery.isError ? null : heartbeatQuery.data?.data,
    approvalMetrics: approvalMetricsQuery.isError ? null : approvalMetricsQuery.data?.data,
    notificationStats: notificationStatsQuery.isError ? null : notificationStatsQuery.data?.data,
    notificationStatsError: notificationStatsQuery.isError,
    qaSummary: qaSummaryQuery.isError ? null : qaSummaryQuery.data?.data,
    qaSummaryError: qaSummaryQuery.isError,
    timeline: timelineQuery.isError ? [] : (timelineQuery.data?.data ?? []),
    timelineError: timelineQuery.isError,
  });

  // Cost surface (spec: dashboard-domain-pages — CostWidget + TopSessionsTable).
  // Reuse the same useSpendSummary("today") query already fetched for the
  // ButlerIndex per-butler annotations (same query key — cached, no extra
  // fetch). CostWidget shows the aggregate "Cost Today" total + the single
  // most-expensive butler, derived from the by_butler breakdown; this is a
  // distinct surface from the per-butler subtitles in ButlerIndex, so no
  // aggregate cost figure is double-rendered.
  const costData = costQuery.isError ? null : costQuery.data?.data;
  const [topButler, topButlerCost] = Object.entries(costData?.by_butler ?? {}).reduce<
    [string | null, number]
  >((best, [name, cost]) => (cost > best[1] ? [name, cost] : best), [null, 0]);
  const topSessions = topSessionsQuery.isError ? [] : (topSessionsQuery.data?.data ?? []);

  // Briefing headline and greet with safe fallbacks
  const greet = briefing?.greet ?? "Good morning.";
  const headline = briefing?.headline ?? "Checking in.";
  const elaboration =
    briefing?.elaboration ??
    "Butlers are running. Check back in a moment for a fresh briefing.";

  return (
    <Page archetype="editorial" title="Overview">
      {/*
       * Responsive two-column editorial grid.
       * Narrow (< 1024px / lg): single column, narrative stacked above index.
       * Wide (>= 1024px / lg): 1.4fr / 1fr, gap 56px (gap-14).
       * The lg breakpoint aligns with the sidebar transition so the combined
       * content width stays within the 1280px Page frame.
       */}
      <div
        className="grid gap-8 items-start lg:gap-14 lg:grid-cols-[1.4fr_1fr]"
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
            isError={model.butlersError}
            pendingApprovalsAvailable={!approvalMetricsQuery.isError && approvalMetricsQuery.data != null}
          />
        </div>

        {/* Right column: index */}
        <div
          style={{ display: "flex", flexDirection: "column", gap: "32px" }}
          aria-label="Operations and now"
        >
          <ButlerIndex butlers={model.operationsRows} butlersError={model.butlersError} />
          <OperationsNowList rows={model.nowRows} />
        </div>
      </div>

      {/*
       * Cost surface (spec: dashboard-domain-pages — "Cost widget for dashboard
       * overview" + "Top sessions table"). Full-width band below the editorial
       * grid: the aggregate CostWidget (constrained to a half-width column) over
       * the most-expensive-sessions table.
       */}
      <div
        style={{ marginTop: "40px", display: "flex", flexDirection: "column", gap: "24px" }}
        aria-label="Cost"
      >
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <CostWidget
            totalCostUsd={costData?.total_cost_usd ?? 0}
            topButler={topButler}
            topButlerCost={topButlerCost}
            isLoading={costQuery.isLoading}
          />
        </div>
        <TopSessionsTable sessions={topSessions} isLoading={topSessionsQuery.isLoading} />
      </div>
    </Page>
  );
}

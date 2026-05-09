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
 *   useButlers()            -- ButlerIndex, KPI "butlers" cell
 *   useCostSummary("today") -- KPI "cost" cell, ButlerIndex per-butler cost
 *   useSessions(today)      -- KPI "sessions" cell
 *   useApprovalMetrics()    -- KPI "approvals" cell, NextList pending approvals
 *
 * bu-1fpvp.2 -- Frontend: replace DashboardPage with editorial layout.
 */

import { Page } from "@/components/ui/page";
import { useBriefing } from "@/hooks/use-briefing";
import { useButlers } from "@/hooks/use-butlers";
import { useCostSummary } from "@/hooks/use-costs";
import { useIssues } from "@/hooks/use-issues";
import { useSessions } from "@/hooks/use-sessions";
import { useApprovalMetrics } from "@/hooks/use-approvals";

import { AttentionList } from "@/components/overview/AttentionList";
import { BriefingStatus } from "@/components/overview/BriefingStatus";
import { ButlerIndex } from "@/components/overview/ButlerIndex";
import { DateEyebrow } from "@/components/overview/DateEyebrow";
import { Elaboration } from "@/components/overview/Elaboration";
import { Headline } from "@/components/overview/Headline";
import { KpiStrip } from "@/components/overview/KpiStrip";
import { NextList } from "@/components/overview/NextList";

export default function DashboardPage() {
  // Briefing
  const {
    data: briefing,
    isFetching: briefingFetching,
    refetch: refetchBriefing,
  } = useBriefing();

  // Supporting data
  const { data: butlersResponse, isLoading: butlersLoading } = useButlers();
  const { data: costSummaryResponse, isLoading: costLoading } = useCostSummary("today");
  const { data: sessionsTodayResponse, isLoading: sessionsLoading } = useSessions(
    {
      limit: 1,
      offset: 0,
      since: new Date(new Date().setHours(0, 0, 0, 0)).toISOString(),
    },
    { refetchInterval: 60_000 },
  );
  const { data: issuesResponse } = useIssues();
  const { data: approvalMetricsResponse, isLoading: approvalsLoading } = useApprovalMetrics();

  // Derived values
  const butlers = butlersResponse?.data ?? [];
  const totalButlers = butlers.length;
  const healthyButlers = butlers.filter((b) => b.status === "ok").length;
  const issues = issuesResponse?.data ?? [];
  const sessionsToday = sessionsTodayResponse?.meta.total ?? 0;
  const costToday = costSummaryResponse?.data.total_cost_usd ?? 0;
  const byButler = costSummaryResponse?.data.by_butler ?? {};
  const pendingApprovals = approvalMetricsResponse?.data.total_pending ?? 0;

  // Butler index rows: join butlers with cost data and 24h session counts
  const butlerIndexEntries = butlers
    .filter((b) => b.type === "butler")
    .map((b) => ({
      name: b.name,
      sessions: b.sessions_24h ?? 0,
      costUsd: byButler[b.name] ?? 0,
    }));

  // KPI cells
  const kpiCells: [
    { eyebrow: string; value: string | number; delta?: string },
    { eyebrow: string; value: string | number; delta?: string },
    { eyebrow: string; value: string | number; delta?: string },
    { eyebrow: string; value: string | number; delta?: string },
  ] = [
    {
      eyebrow: "Butlers",
      value: butlersLoading ? "--" : `${healthyButlers}/${totalButlers}`,
    },
    {
      eyebrow: "Sessions",
      value: sessionsLoading ? "--" : sessionsToday,
    },
    {
      eyebrow: "Cost",
      value: costLoading ? "--" : `$${costToday.toFixed(2)}`,
    },
    {
      eyebrow: "Approvals",
      value: approvalsLoading ? "--" : pendingApprovals,
    },
  ];

  // NextList: show pending approvals as upcoming items when available
  const nextItems =
    pendingApprovals > 0
      ? [
          {
            time: "now",
            label: `${pendingApprovals} pending approval${pendingApprovals === 1 ? "" : "s"}`,
            kind: "approval",
          },
        ]
      : [];

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

          {/* Attention list */}
          <AttentionList items={issues} />

          {/* KPI strip */}
          <KpiStrip cells={kpiCells} />
        </div>

        {/* Right column: index */}
        <div
          style={{ display: "flex", flexDirection: "column", gap: "32px" }}
          aria-label="Butler index"
        >
          <ButlerIndex butlers={butlerIndexEntries} />
          <NextList items={nextItems} />
        </div>
      </div>
    </Page>
  );
}

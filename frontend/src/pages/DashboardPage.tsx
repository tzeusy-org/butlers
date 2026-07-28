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
 *   useIssues({window:"all"}) -- AttentionList (client-side stale/severity ordering);
 *                              explicitly opts out of the Issues page's default
 *                              7d server-side window (bu-qvnce.13) since this
 *                              page derives its own current/recent/old buckets
 *                              from the full history.
 *   GET /api/butlers/board  -- ButlerIndex, RuntimeSummaryKpi, runtime attention
 *                              rows -- the SAME canonical, cadence-aware
 *                              liveness verdict the /butlers status board
 *                              renders (bu-qvnce.4 -- one liveness model,
 *                              not two independently-maintained ones).
 *   useSpendSummary("today") -- CostWidget aggregate + top-butler breakdown
 *   useApprovalMetrics()    -- KPI "approvals" cell, OperationsNowList approvals row
 *   usePendingApprovalsFlat() -- individual pending approvals for the inline
 *                                approve/deny/defer rows below (falls back to
 *                                the useApprovalMetrics aggregate row on error)
 *   useNotificationStats({ since: now - 24h, until: now }) -- closed, time-bounded notification pressure row
 *   useQaSummary()          -- OperationsNowList QA state row
 *   useTimeline()           -- OperationsNowList recent activity rows
 *
 * bu-1fpvp.2   -- Frontend: replace DashboardPage with editorial layout.
 * bu-bm58r.1   -- Runtime summary KPI card from existing hooks.
 * bu-tn1po.3   -- Needs-attention list (AttentionList).
 * bu-tn1po.4   -- Promoted KPI strip + enriched butler index (ButlerIndex).
 * bu-tn1po.5   -- Operations-now signal list (OperationsNowList).
 * bu-tn1po.6   -- Compose all surfaces into this triage cockpit page.
 * bu-86c4c.14  -- Act loop / hot queue: inline approve/deny/defer on the
 *                 Needs-attention list's actionable approval rows.
 * bu-qvnce.4   -- Dashboard coherence: the KPI strip and the attention list
 *                 now derive from the identical board verdict, so they can
 *                 never contradict each other; dashboard approve/deny/defer
 *                 rows share the same undo-window grace contract as /approvals.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { Page } from "@/components/ui/page";
import { useBriefing } from "@/hooks/use-briefing";
import { useButlersBoard } from "@/hooks/use-butlers";
import {
  useSpendSummary,
  useTopSessions,
  useDailySpend,
} from "@/hooks/use-spend";
import { useIssues } from "@/hooks/use-issues";
import {
  useApprovalMetrics,
  usePendingApprovalsFlat,
} from "@/hooks/use-approvals";
import {
  useApprovalDecisionMutations,
  UNDO_WINDOW_MS,
  type DecisionVerb,
} from "@/hooks/use-approval-decisions.ts";
import { useNotificationStats } from "@/hooks/use-notifications";
import { useQaSummary } from "@/hooks/use-qa";
import { useTickingNow } from "@/hooks/use-ticking-now";
import { useTimeline } from "@/hooks/use-timeline";
import { useFleetHaltStatus } from "@/hooks/use-fleet-halt";
import { useStuckDelegations } from "@/hooks/use-delegation";
import { useListTriage, type ListTriageVerb } from "@/hooks/use-list-triage";

import CostWidget from "@/components/costs/CostWidget";
import TopSessionsTable from "@/components/costs/TopSessionsTable";

import { ListTriageFooterHint } from "@/components/ui/list-triage-footer";
import {
  AttentionList,
  type AttentionListItem,
} from "@/components/overview/AttentionList";
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
  const [searchParams, setSearchParams] = useSearchParams();
  const includeInternal = searchParams.get("internal") === "1";

  // Briefing
  const {
    data: briefing,
    isFetching: briefingFetching,
    isError: briefingError,
    refetch: refetchBriefing,
  } = useBriefing();

  // Supporting data
  //
  // Board rows are the SAME canonical liveness verdict the /butlers status
  // board renders (GET /api/butlers/board, bu-qvnce.4) -- the Overview no
  // longer maintains its own butlers-list + heartbeats-facts pair with its
  // own stale-threshold classification. useButlersBoard() shares its
  // queryKey with the board page's useButlerStatusBoard(), so react-query
  // dedupes the request AND the event bus's session-patch
  // (event-cache-registry.ts's sessionPatch, which already invalidates
  // ["butlers","board"]) live-refreshes this page too, not just /butlers.
  const boardQuery = useButlersBoard();
  const costQuery = useSpendSummary("today");
  // window:"all" preserves this page's existing full-history behavior — its
  // own bucketing (current/recent/old) already manages what's shown, so the
  // Issues page's new 7d default (bu-qvnce.13) must not silently truncate it.
  const issuesQuery = useIssues({ window: "all" });
  const approvalMetricsQuery = useApprovalMetrics();
  // Individual pending approvals for the inline approve/deny/defer rows below
  // (bu-86c4c.14). Small cap -- this is a "what needs a look" preview, not
  // the full triage queue (that's /approvals).
  const pendingApprovalsQuery = usePendingApprovalsFlat(3);
  // undoWindow: true opts these rows into the SAME grace-window contract
  // /approvals' keyboard triage uses (bu-qvnce.4) -- a decision made from the
  // dashboard's one-click attention list is just as undoable as one made on
  // the full Trust Console, instead of firing irreversibly on click.
  const {
    approveMut,
    denyMut,
    deferMut,
    scheduledDecisions,
    scheduleDecision,
    cancelDecision,
  } = useApprovalDecisionMutations({ undoWindow: true });
  const approve = approveMut.mutate;
  const deny = denyMut.mutate;
  const defer = deferMut.mutate;
  // The dashboard's notification count is operational pressure, not an
  // all-time incident ledger. Recompute the boundary on a modest wall-clock
  // tick so a stale failure ages out without waiting for another page event.
  const overviewNowMs = useTickingNow(60_000);
  // The Notifications destination deliberately exposes minute-resolution
  // datetime-local controls. Capture this closed window at that same
  // precision so the stats query, predicate-carrying link, and visible
  // destination filters all describe one interval.
  const notificationWindowEndMs = Math.floor(overviewNowMs / 60_000) * 60_000;
  const notificationSince = new Date(
    notificationWindowEndMs - 24 * 60 * 60 * 1000,
  ).toISOString();
  const notificationUntil = new Date(notificationWindowEndMs).toISOString();
  const notificationStatsQuery = useNotificationStats({
    since: notificationSince,
    until: notificationUntil,
  });
  // The Sessions KPI door (bu-27dxl.8.3) reuses this SAME captured 24-hour
  // instant rather than deriving its own -- one window, not a fresh
  // Date.now() recomputed between this render and the eventual click.
  const sessionsSince = notificationSince;
  const sessionsUntil = notificationUntil;
  const qaSummaryQuery = useQaSummary();
  const timelineQuery = useTimeline({ limit: 5 });
  // Monthly spend-ceiling fleet-halt state (bu-7o89u.3): the drawer itself
  // lives on /spend -- Overview only needs the summary shape for the
  // critical attention row, so the default drawer-row limit is unused here.
  const fleetHalt = useFleetHaltStatus();
  // Delegation wake-protocol failures (bu-ep4ks.3) -- callback_failed/
  // task_conflict rows are otherwise invisible outside this fetch.
  const stuckDelegations = useStuckDelegations();
  const topSessionsQuery = useTopSessions();
  // Real 7-day daily cost series for the CostWidget sparkline (bu-86c4c.1 —
  // the sparkline previously fabricated bar heights from a pseudo-random
  // formula; this is the same real series CostsPage's chart uses).
  const dailySpendQuery = useDailySpend();

  // Derived values. Keep the source references stable when their underlying
  // queries did not change so the list-triage shortcut registration does not
  // churn on an unrelated DashboardPage render.
  const boardData = boardQuery.data?.data;
  const issuesData = issuesQuery.data?.data;
  const approvalMetrics = approvalMetricsQuery.data?.data;
  const approvals = pendingApprovalsQuery.data?.data;
  const notificationStats = notificationStatsQuery.data?.data;
  const qaSummary = qaSummaryQuery.data?.data;
  const timeline = timelineQuery.data?.data;
  const toggleInternal = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (next.get("internal") === "1") next.delete("internal");
      else next.set("internal", "1");
      return next;
    });
  }, [setSearchParams]);
  const model = useMemo(
    () =>
      deriveOverviewTriageModel(
        {
          boardRows: boardQuery.isError ? [] : (boardData?.rows ?? []),
          butlersError: boardQuery.isError,
          issues: issuesQuery.isError ? [] : (issuesData ?? []),
          issuesError: issuesQuery.isError,
          approvalMetrics: approvalMetricsQuery.isError
            ? null
            : approvalMetrics,
          approvals: pendingApprovalsQuery.isError ? null : approvals,
          notificationStats: notificationStatsQuery.isError
            ? null
            : notificationStats,
          notificationSince,
          notificationUntil,
          notificationStatsError: notificationStatsQuery.isError,
          qaSummary: qaSummaryQuery.isError ? null : qaSummary,
          qaSummaryError: qaSummaryQuery.isError,
          timeline: timelineQuery.isError ? [] : (timeline ?? []),
          timelineError: timelineQuery.isError,
          fleetHalt: {
            active: fleetHalt.active,
            deniedToday: fleetHalt.deniedToday,
            deniedTotal: fleetHalt.deniedTotal,
            since: fleetHalt.since,
            isSourceError: fleetHalt.isError,
          },
          stuckDelegations: stuckDelegations.isError ? null : stuckDelegations.rows,
          stuckDelegationsError: stuckDelegations.isError,
        },
        { now: new Date(overviewNowMs), includeInternal },
      ),
    [
      approvals,
      approvalMetrics,
      approvalMetricsQuery.isError,
      boardData,
      boardQuery.isError,
      fleetHalt.active,
      fleetHalt.deniedToday,
      fleetHalt.deniedTotal,
      fleetHalt.isError,
      fleetHalt.since,
      includeInternal,
      issuesData,
      issuesQuery.isError,
      notificationStats,
      notificationStatsQuery.isError,
      notificationSince,
      notificationUntil,
      overviewNowMs,
      pendingApprovalsQuery.isError,
      qaSummary,
      qaSummaryQuery.isError,
      stuckDelegations.isError,
      stuckDelegations.rows,
      timeline,
      timelineQuery.isError,
    ],
  );

  // Cost surface (spec: dashboard-domain-pages — CostWidget + TopSessionsTable).
  // Reuse the same useSpendSummary("today") query already fetched for the
  // ButlerIndex per-butler annotations (same query key — cached, no extra
  // fetch). CostWidget shows the aggregate "Cost Today" total + the single
  // most-expensive butler, derived from the by_butler breakdown; this is a
  // distinct surface from the per-butler subtitles in ButlerIndex, so no
  // aggregate cost figure is double-rendered.
  const costData = costQuery.data?.data;
  const costSourceError = costData?.source_error === true;
  const dailyCostSourceError = dailySpendQuery.data?.meta?.source_error === true;
  const [topButler, topButlerCost] = Object.entries(
    costData?.by_butler ?? {},
  ).reduce<[string | null, number]>(
    (best, [name, cost]) => (cost > best[1] ? [name, cost] : best),
    [null, 0],
  );
  const topSessions = topSessionsQuery.data?.data ?? [];

  // Wire live approve/deny/defer handlers onto the individually-actionable
  // approval rows model.ts produced (rows carrying `approvalId`) -- the
  // model itself stays a pure function, so the mutations are attached here
  // (bu-86c4c.14: approve/deny/defer executable from the dashboard's
  // attention list without leaving the pane).
  //
  // Every verb click goes through scheduleDecision rather than calling
  // .mutate() directly (bu-qvnce.4): the decision is undoable for
  // UNDO_WINDOW_MS, same as /approvals' keyboard triage, instead of firing
  // irreversibly the instant the row is clicked. While scheduled, the row
  // shows the inline "Approving in 5s · Undo" state instead of its verb
  // buttons (see AttentionList's pendingDecisionLabel).
  const approveAttention = useCallback(
    (id: string) => scheduleDecision(id, "approve", () => approve(id)),
    [approve, scheduleDecision],
  );
  const denyAttention = useCallback(
    (id: string) => scheduleDecision(id, "deny", () => deny({ id })),
    [deny, scheduleDecision],
  );
  const deferAttention = useCallback(
    (id: string) =>
      scheduleDecision(id, "defer", () => defer({ id, hours: 24 })),
    [defer, scheduleDecision],
  );
  const undoAttentionDecision = useCallback(
    (id: string) => cancelDecision(id),
    [cancelDecision],
  );

  const attentionRows: AttentionListItem[] = useMemo(
    () =>
      model.attentionRows.map((row) => {
        if (!row.approvalId) return row;
        const id = row.approvalId;
        const scheduled = scheduledDecisions.get(id);
        if (scheduled) {
          return {
            ...row,
            pendingDecisionLabel: `${verbGerund(scheduled.verb)} in ${Math.round(UNDO_WINDOW_MS / 1000)}s`,
            onUndoDecision: () => undoAttentionDecision(id),
          };
        }
        return {
          ...row,
          onApprove: () => approveAttention(id),
          onDeny: () => denyAttention(id),
          onDefer: () => deferAttention(id),
          approvePending: approveMut.isPending && approveMut.variables === id,
          denyPending: denyMut.isPending && denyMut.variables?.id === id,
          deferPending: deferMut.isPending && deferMut.variables?.id === id,
        };
      }),
    [
      model.attentionRows,
      scheduledDecisions,
      approveAttention,
      approveMut.isPending,
      approveMut.variables,
      deferAttention,
      deferMut.isPending,
      deferMut.variables,
      denyAttention,
      denyMut.isPending,
      denyMut.variables,
      undoAttentionDecision,
    ],
  );

  // j/k roving selection + a/d/x/u act keys over the Needs-attention list
  // (bu-qvnce.11 slice 4 -- useListTriage, extracted from ApprovalsPage's
  // own former hand-rolled version of this exact pattern). Selection is
  // ephemeral component state, not URL-backed -- unlike /approvals there is
  // no per-row URL here to select via routing, and this list is a "what
  // needs a look" preview rather than the full triage queue.
  const [selectedAttentionId, setSelectedAttentionId] = useState<string | null>(
    null,
  );
  const attentionIds = useMemo(
    () => attentionRows.map((row) => row.id),
    [attentionRows],
  );
  const attentionVerbs = useMemo<ListTriageVerb[]>(() => {
    const row = attentionRows.find((r) => r.id === selectedAttentionId);
    if (!row) return [];
    if (row.onUndoDecision) {
      return [
        {
          key: "u",
          description: "Undo scheduled decision",
          handler: row.onUndoDecision,
          command: {
            id: "undo-attention-decision",
            label: "Undo selected scheduled decision",
            keywords: ["undo", "attention"],
          },
        },
      ];
    }
    const verbs: ListTriageVerb[] = [];
    if (row.onApprove)
      verbs.push({
        key: "a",
        description: "Approve selected",
        handler: row.onApprove,
        command: {
          id: "approve-attention-item",
          label: "Approve selected attention item",
          keywords: ["approval", "attention"],
        },
      });
    if (row.onDeny)
      verbs.push({
        key: "d",
        description: "Deny selected",
        handler: row.onDeny,
        command: {
          id: "deny-attention-item",
          label: "Deny selected attention item",
          keywords: ["deny", "attention"],
        },
      });
    if (row.onDefer)
      verbs.push({
        key: "x",
        description: "Defer selected",
        handler: row.onDefer,
        command: {
          id: "defer-attention-item",
          label: "Defer selected attention item",
          keywords: ["defer", "attention"],
        },
      });
    return verbs;
  }, [attentionRows, selectedAttentionId]);
  const { hints: attentionHints } = useListTriage({
    ids: attentionIds,
    selectedId: selectedAttentionId,
    onSelect: setSelectedAttentionId,
    verbs: attentionVerbs,
  });

  // Keep DOM focus in sync with the current selection, mirroring
  // ApprovalsPage's identical rail-focus effect -- the browser's native
  // focus-visible ring visibly tracks j/k roving focus.
  useEffect(() => {
    if (!selectedAttentionId) return;
    const nodes = document.querySelectorAll<HTMLElement>(
      '[data-testid="attention-item"]',
    );
    for (const node of nodes) {
      if (node.getAttribute("data-item-id") === selectedAttentionId) {
        node.focus({ preventScroll: true });
        break;
      }
    }
  }, [selectedAttentionId]);

  // Briefing headline and greet with safe fallbacks. A failed briefing fetch
  // must never render the indefinite "Checking in." / "check back in a
  // moment" copy forever -- that reads as still-loading when it is actually
  // down (bu-86c4c.2, JARVIS audit move 1b).
  const greet = briefing?.greet ?? "Good morning.";
  const headline = briefingError
    ? "Briefing unavailable."
    : (briefing?.headline ?? "Checking in.");
  const elaboration = briefingError
    ? "Could not reach the briefing service. Retry from the status pill above."
    : (briefing?.elaboration ??
      "Butlers are running. Check back in a moment for a fresh briefing.");

  return (
    <Page archetype="editorial" title="Overview">
      {/*
       * Responsive two-column editorial grid.
       * Narrow (< 1024px / lg): single column, narrative stacked above index.
       * Wide (>= 1024px / lg): 1.4fr / 1fr, gap 56px (gap-14).
       * The lg breakpoint aligns with the sidebar transition so the combined
       * content width stays within the 1280px Page frame.
       */}
      <div className="grid gap-8 items-start lg:gap-14 lg:grid-cols-[1.4fr_1fr]">
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
                isError={briefingError}
                onRefetch={() => {
                  void refetchBriefing();
                }}
              />
            }
          />

          {/* Display headline */}
          <Headline greet={greet} body={headline} />

          {/* Voice elaboration paragraph */}
          <Elaboration text={elaboration} isFetching={briefingFetching} />

          <Section eyebrow="Needs attention">
            <AttentionList
              items={attentionRows}
              selectedId={selectedAttentionId}
            />
            {/* Shared footer hint strip (bu-qvnce.11 slice 4) -- advertises the
                EXACT j/k/a/d/x/u bindings useListTriage just registered. */}
            <ListTriageFooterHint bindings={attentionHints} />
          </Section>

          <RuntimeSummaryKpi
            kpis={model.kpis}
            isLoading={boardQuery.isLoading}
            isError={model.butlersError}
            pendingApprovalsAvailable={
              !approvalMetricsQuery.isError && approvalMetricsQuery.data != null
            }
            sessionsAvailable={boardData?.aggregates?.sessions_source_error !== true}
            sessionsSince={sessionsSince}
            sessionsUntil={sessionsUntil}
          />
        </div>

        {/* Right column: index */}
        <div
          style={{ display: "flex", flexDirection: "column", gap: "32px" }}
          aria-label="Operations and now"
        >
          <ButlerIndex
            butlers={model.operationsRows}
            butlersError={model.butlersError}
          />
          <OperationsNowList
            rows={model.nowRows}
            includeInternal={includeInternal}
            onToggleInternal={toggleInternal}
          />
        </div>
      </div>

      {/*
       * Cost surface (spec: dashboard-domain-pages — "Cost widget for dashboard
       * overview" + "Top sessions table"). Full-width band below the editorial
       * grid: the aggregate CostWidget (constrained to a half-width column) over
       * the most-expensive-sessions table.
       */}
      <div
        style={{
          marginTop: "40px",
          display: "flex",
          flexDirection: "column",
          gap: "24px",
        }}
        aria-label="Cost"
      >
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <CostWidget
            totalCostUsd={costData?.total_cost_usd ?? 0}
            topButler={topButler}
            topButlerCost={topButlerCost}
            unpricedModels={costData?.unpriced_models}
            sourceError={costSourceError}
            isUnavailable={costQuery.isError}
            isLoading={costQuery.isLoading}
            dailyCosts={
              dailySpendQuery.isError || dailyCostSourceError ? undefined : dailySpendQuery.data?.data
            }
            dailyCostsError={dailySpendQuery.isError}
            dailySourceError={dailyCostSourceError}
            dailyUnpricedModels={dailySpendQuery.isError ? undefined : dailySpendQuery.data?.meta?.unpriced_models}
          />
        </div>
        <TopSessionsTable
          sessions={topSessions}
          isLoading={topSessionsQuery.isLoading}
          isUnavailable={topSessionsQuery.isError}
        />
      </div>
    </Page>
  );
}

/** Present-tense verb for the inline "Verb in Ns · Undo" scheduled-decision state. */
function verbGerund(verb: DecisionVerb): string {
  switch (verb) {
    case "approve":
      return "Approving";
    case "deny":
      return "Denying";
    case "defer":
      return "Deferring";
  }
}

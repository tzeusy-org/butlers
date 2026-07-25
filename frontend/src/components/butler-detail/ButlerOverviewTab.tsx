// ---------------------------------------------------------------------------
// ButlerOverviewTab
//
// Operational overview grid for /butlers/:name. This follows the
// (butler-detail redesign, graduated) target shape:
//   status | sessions | spend | awaiting
//   activity | recent
//   awaiting your action | config
//
// The live API does not expose process pid, so the production grid preserves
// the prototype rhythm while using container-boundary-safe process facts.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate } from "react-router"

import {
  ButlerPanelGrid,
  ErrorLine,
  KpiCell,
  KV,
  MonoLabel,
  Panel,
} from "@/components/butler-detail/atoms"
import { ButlerDelegationsPanel } from "@/components/butler-detail/ButlerDelegationsPanel"
import { ActivityStripe } from "@/components/butlers/ActivityStripe"
import { SessionDetailDrawer } from "@/components/sessions/SessionDetailDrawer"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Time } from "@/components/ui/time"
import { useApprovalDecisionMutations } from "@/hooks/use-approval-decisions"
import { useApprovalActions } from "@/hooks/use-approvals"
import { useButler } from "@/hooks/use-butlers"
import { useButlerActivityFeed } from "@/hooks/use-butler-analytics"
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board"
import { useSpendSummary } from "@/hooks/use-spend"
import { useSessionAggregate } from "@/hooks/use-sessions"
import { formatCostUsd } from "@/lib/format-cost"
import type {
  ActivityEventType,
  ApprovalAction,
  ButlerActivityEvent,
  UnpricedModelUsage,
} from "@/api/types"
import { ButlerVerdictOpener } from "@/components/butler-detail/ButlerVerdictOpener"

interface ButlerOverviewTabProps {
  butlerName: string
}

// Delegates to the shared formatter [bu-sd0l7.3] — this used to clamp any
// nonzero sub-cent spend to "$0.00" (the exact bug documented at the top of
// lib/format-cost.ts), before formatCostUsd existed.
function formatCurrency(amount: number | null | undefined): string {
  return amount == null ? "--" : formatCostUsd(amount)
}

function statusTone(status: string | undefined): "green" | "amber" | "red" | "dim" {
  if (status === "ok" || status === "healthy") return "green"
  if (status === "degraded" || status === "waiting") return "amber"
  if (status === "error" || status === "down") return "red"
  return "dim"
}

function statusLabel(status: string | undefined): string {
  if (status === "ok" || status === "healthy") return "online"
  return status ?? "unknown"
}

function activityLabel(eventType: ActivityEventType): string {
  switch (eventType) {
    case "session_completed":
      return "session"
    case "approval_raised":
      return "approval"
    case "memory_write":
      return "memory"
    default:
      return eventType
  }
}

/**
 * Computes the [since, until) ISO window for stripe slot `index` (0 = oldest
 * of the last 24h, 23 = the current hour), matching the bucketing convention
 * in useButlerStatusBoard (hourlyStripe slot 23 - bucket.hour_index).
 */
function stripeSlotWindow(index: number): { since: string; until: string } {
  const hoursAgoUntil = 23 - index
  const hoursAgoSince = hoursAgoUntil + 1
  const now = Date.now()
  return {
    since: new Date(now - hoursAgoSince * 3_600_000).toISOString(),
    until: new Date(now - hoursAgoUntil * 3_600_000).toISOString(),
  }
}

function last24HoursSince(): string {
  return new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()
}

function HourAxis() {
  return (
    <div className="mt-2 flex justify-between font-mono text-[9px] text-muted-foreground">
      {["-24h", "-12h", "now"].map((label) => (
        <span key={label}>{label}</span>
      ))}
    </div>
  )
}

function EventKind({ eventType }: { eventType: ActivityEventType }) {
  return (
    <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
      {activityLabel(eventType)}
    </span>
  )
}

/**
 * Recent-activity event row. Every row is a door (bu-86c4c.18):
 *   - session_completed -- opens the session transcript in place (drawer)
 *   - approval_raised    -- jumps to the Approvals tab
 *   - memory_write       -- jumps to the Memory tab
 * Falls back to plain (non-interactive) text when the event carries no
 * entity_id to act on.
 */
function EventRow({
  event,
  onOpenSession,
}: {
  event: ButlerActivityEvent
  onOpenSession: (sessionId: string) => void
}) {
  const rowBody = (
    <>
      <span className="font-mono text-[11px] text-muted-foreground">
        <Time value={event.ts} mode="relative" compact />
      </span>
      <span className="min-w-0 truncate text-xs">{event.summary}</span>
      <EventKind eventType={event.event_type} />
    </>
  )
  const rowClassName =
    "grid grid-cols-[50px_minmax(0,1fr)_auto] items-baseline gap-3 border-b border-border/40 py-1.5 last:border-b-0"

  if (event.event_type === "session_completed" && event.entity_id) {
    return (
      <button
        type="button"
        onClick={() => onOpenSession(event.entity_id as string)}
        className={`${rowClassName} w-full text-left hover:bg-muted/30`}
        data-testid="activity-feed-row"
      >
        {rowBody}
      </button>
    )
  }

  if (event.event_type === "approval_raised") {
    return (
      <Link to="?tab=approvals" className={`${rowClassName} hover:bg-muted/30`} data-testid="activity-feed-row">
        {rowBody}
      </Link>
    )
  }

  if (event.event_type === "memory_write") {
    return (
      <Link to="?tab=memory" className={`${rowClassName} hover:bg-muted/30`} data-testid="activity-feed-row">
        {rowBody}
      </Link>
    )
  }

  return (
    <div className={rowClassName} data-testid="activity-feed-row">
      {rowBody}
    </div>
  )
}

type PendingDecision = "approve" | "reject"

function ActionRow({
  action,
  butlerName,
  onApprove,
  onReject,
  pendingDecision,
}: {
  action: ApprovalAction
  butlerName: string
  onApprove: (id: string) => void
  onReject: (id: string) => void
  pendingDecision: PendingDecision | undefined
}) {
  const actionLabel = action.agent_summary || action.tool_name
  const approving = pendingDecision === "approve"
  const rejecting = pendingDecision === "reject"
  const isDeciding = approving || rejecting

  return (
    <div className="grid grid-cols-[8px_minmax(0,1fr)_auto] items-baseline gap-3 border-b border-border/40 py-2 last:border-b-0">
      <span className="mt-1.5 h-1.5 w-1.5 rounded-[1px] bg-[var(--amber)]" aria-hidden="true" />
      <span className="min-w-0 truncate text-xs">
        {actionLabel}
        <span className="text-muted-foreground"> · </span>
        <Time value={action.requested_at} mode="relative" />
      </span>
      <div className="flex items-center gap-1">
        <Button
          type="button"
          size="xs"
          className="h-6 rounded-[3px] px-2 font-mono text-[10px] uppercase tracking-[0.06em]"
          aria-label={`Approve ${actionLabel}`}
          onClick={() => onApprove(action.id)}
          disabled={isDeciding}
        >
          {approving ? "Approving…" : "Approve"}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="xs"
          className="h-6 rounded-[3px] px-2 font-mono text-[10px] uppercase tracking-[0.06em] text-[var(--red-text)] hover:text-[var(--red-text)]"
          aria-label={`Reject ${actionLabel}`}
          onClick={() => onReject(action.id)}
          disabled={isDeciding}
        >
          {rejecting ? "Rejecting…" : "Reject"}
        </Button>
        {/* Deep link (bu-86c4c.18): scopes the global approvals page to this
            butler and this action, instead of dropping the operator on an
            unfiltered global list they have to re-find the item in. */}
        <Link
          to={`/approvals?butler=${encodeURIComponent(butlerName)}&id=${encodeURIComponent(action.id)}`}
          className="text-xs text-foreground underline decoration-border underline-offset-4"
        >
          review
        </Link>
      </div>
    </div>
  )
}

function OverviewSkeleton() {
  return (
    <ButlerPanelGrid className="sm:grid-cols-2 md:grid-cols-4" data-testid="overview-skeleton">
      {Array.from({ length: 4 }).map((_, index) => (
        <Panel key={index} title="loading">
          <Skeleton className="h-8 w-24 rounded-sm" />
        </Panel>
      ))}
      <Panel title="activity" span={2} className="sm:col-span-2">
        <Skeleton className="h-[100px] w-full rounded-sm" />
      </Panel>
      <Panel title="recent" span={2} className="sm:col-span-2">
        <Skeleton className="h-[100px] w-full rounded-sm" />
      </Panel>
      <Panel title="awaiting your action" span={2} className="sm:col-span-2">
        <Skeleton className="h-24 w-full rounded-sm" />
      </Panel>
      <Panel title="config" span={2} className="sm:col-span-2">
        <Skeleton className="h-24 w-full rounded-sm" />
      </Panel>
    </ButlerPanelGrid>
  )
}

export default function ButlerOverviewTab({ butlerName }: ButlerOverviewTabProps) {
  const navigate = useNavigate()
  const { data: butlerResponse, isLoading: butlerLoading, isError: butlerError } = useButler(butlerName)
  const { rows, aggregates } = useButlerStatusBoard()
  const costQuery = useSpendSummary("today", undefined, undefined, butlerName)
  const approvalsQuery = useApprovalActions({ status: "pending", butler: butlerName, limit: 5 })
  const failedSessionsSince = useMemo(last24HoursSince, [])
  const failedSessionsQuery = useSessionAggregate({
    butler: butlerName,
    since: failedSessionsSince,
  })
  const {
    data: activityFeedData,
    isLoading: activityFeedLoading,
    isError: activityFeedError,
  } = useButlerActivityFeed(butlerName, 5)
  const pendingActions = approvalsQuery.data?.data

  // Session drawer state for the "recent events" door (bu-86c4c.18): clicking
  // a session_completed row opens its transcript in place instead of leaving
  // the operator on a dead-end line of text.
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null)
  const [pendingDecisions, setPendingDecisions] = useState<Map<string, PendingDecision>>(
    () => new Map(),
  )
  const pendingDecisionIds = useRef<Set<string>>(new Set())
  const { approveMut, denyMut } = useApprovalDecisionMutations()

  // Keep an approved/rejected row disabled until the action-preview query has
  // reconciled it away. mutateAsync resolves once the endpoint responds, but
  // this preview remains stale until its invalidation refetch returns; clearing
  // earlier would expose a duplicate decision button for that interval.
  useEffect(() => {
    if (!pendingActions) return

    const previewActionIds = new Set(pendingActions.map((action) => action.id))
    setPendingDecisions((current) => {
      let next: Map<string, PendingDecision> | undefined
      for (const id of current.keys()) {
        if (previewActionIds.has(id)) continue
        pendingDecisionIds.current.delete(id)
        next ??= new Map(current)
        next.delete(id)
      }
      return next ?? current
    })
  }, [pendingActions])

  const runDecision = useCallback(
    (id: string, decision: PendingDecision, mutate: () => Promise<unknown>) => {
      if (pendingDecisionIds.current.has(id)) return

      pendingDecisionIds.current.add(id)
      setPendingDecisions((current) => new Map(current).set(id, decision))

      // The shared mutation hook owns success/error toasts. Keep a successful
      // row pending until the effect above sees its refetched preview disappear;
      // a failed mutation restores only this row's controls.
      void mutate()
        .catch(() => {
          pendingDecisionIds.current.delete(id)
          setPendingDecisions((current) => {
            if (!current.has(id)) return current
            const next = new Map(current)
            next.delete(id)
            return next
          })
        })
    },
    [],
  )
  const handleApprove = useCallback(
    (id: string) => runDecision(id, "approve", () => approveMut.mutateAsync(id)),
    [approveMut, runDecision],
  )
  const handleReject = useCallback(
    (id: string) => runDecision(id, "reject", () => denyMut.mutateAsync({ id })),
    [denyMut, runDecision],
  )
  const handleActivityStripeClick = useCallback((index: number) => {
    const { since, until } = stripeSlotWindow(index)
    navigate(
      `?tab=activity&section=sessions&since=${encodeURIComponent(since)}&until=${encodeURIComponent(until)}`,
    )
  }, [navigate])

  if (butlerLoading) {
    return <OverviewSkeleton />
  }

  const butler = butlerResponse?.data
  const row = rows.find((item) => item.name === butlerName)
  const processFacts = butler?.process_facts ?? null
  const modules = butler?.modules ?? []
  const schedules = butler?.schedules ?? []
  const skills = butler?.skills ?? []
  const sessions24h = row?.sessions24h ?? butler?.sessions_24h ?? 0
  const spendSourceError = costQuery.data?.data?.source_error === true
  const spendUnpricedModels: readonly UnpricedModelUsage[] =
    costQuery.data?.data?.unpriced_models ?? []
  const spendCoverageIncomplete = spendUnpricedModels.length > 0
  const costToday =
    spendSourceError || spendCoverageIncomplete
      ? null
      : (costQuery.data?.data?.by_butler?.[butlerName] ?? 0)
  const costPerSession = costToday != null && sessions24h > 0 ? costToday / sessions24h : null
  const visiblePendingActions = pendingActions ?? []
  const recentEvents = activityFeedData?.events ?? []
  const stripe = row?.hourlyStripe ?? Array(24).fill(0)
  const status = butler?.status ?? row?.status
  // meta.total (not the page-size-capped result length) is the true count of
  // pending approvals -- the KPI previously read "5" when 20 were pending
  // because it counted the preview page instead of the real total.
  const awaitingCount = approvalsQuery.data?.meta?.total ?? visiblePendingActions.length
  const spendSourcesDegraded = (costQuery.data?.data?.unavailable_butlers ?? []).filter(
    (name) => name === butlerName,
  )
  const approvalSourcesDegraded = approvalsQuery.data?.meta?.sources_degraded ?? []
  const failureSourcesDegraded =
    (failedSessionsQuery.data?.meta?.sources_degraded as string[] | undefined) ?? []
  const boardSourceError =
    aggregates.isError ||
    !row ||
    row.schemaUnreachable ||
    row.heartbeatUnavailable ||
    row.hourlyStripeError

  return (
    <>
    <ButlerVerdictOpener
      butlerName={butlerName}
      activity={row?.activity}
      sessions24h={sessions24h}
      boardLoading={aggregates.isLoading}
      boardError={butlerError || boardSourceError}
      spendToday={
        spendSourceError || spendCoverageIncomplete
          ? undefined
          : costQuery.data?.data?.by_butler?.[butlerName]
      }
      spendLoading={costQuery.isLoading}
      spendError={costQuery.isError || spendSourceError || (!costQuery.isLoading && !costQuery.data)}
      spendSourcesDegraded={spendSourcesDegraded}
      spendUnpricedModels={spendUnpricedModels}
      pendingApprovals={visiblePendingActions}
      pendingTotal={awaitingCount}
      approvalsLoading={approvalsQuery.isLoading}
      approvalsError={approvalsQuery.isError}
      failedSessions={failedSessionsQuery.data?.data.failed_count}
      failedSessionsLoading={failedSessionsQuery.isLoading}
      failedSessionsError={failedSessionsQuery.isError}
      approvalSourcesDegraded={approvalSourcesDegraded}
      failureSourcesDegraded={failureSourcesDegraded}
    />
    <ButlerPanelGrid
      className="sm:grid-cols-2 md:grid-cols-4"
      data-testid="overview-panel-grid"
    >
      <Panel title="status" testId="panel-status">
        <div className="flex items-center gap-2">
          <span
            className={[
              "h-2 w-2 rounded-full",
              statusTone(status) === "green" && "bg-[var(--green)]",
              statusTone(status) === "amber" && "bg-[var(--amber)]",
              statusTone(status) === "red" && "bg-destructive",
              statusTone(status) === "dim" && "bg-muted-foreground",
            ].filter(Boolean).join(" ")}
            aria-hidden="true"
          />
          <span className="font-mono text-sm uppercase tracking-[0.06em]">
            {statusLabel(status)}
            {row?.activity ? ` · ${row.activity}` : ""}
          </span>
        </div>
        <MonoLabel color="dim" className="mt-2 block">
          last run {row?.lastRunISO ? <Time value={row.lastRunISO} mode="relative" /> : "--"}
        </MonoLabel>
      </Panel>

      <Panel title="sessions" sub="24h" testId="panel-sessions">
        <KpiCell label="" value={sessions24h} sub="started in the last day" />
      </Panel>

      <Panel title="spend" sub="today" testId="panel-spend">
        {costQuery.isLoading ? (
          <Skeleton className="h-8 w-20 rounded-sm" />
        ) : (
          <KpiCell
            label=""
            value={costToday == null ? "—" : formatCurrency(costToday)}
            sub={
              spendSourceError
                ? "spend source unavailable"
                : spendCoverageIncomplete
                  ? "spend coverage incomplete"
                  : `${formatCurrency(costPerSession)} / session`
            }
          />
        )}
      </Panel>

      <Panel title="awaiting" testId="panel-awaiting">
        <KpiCell
          label=""
          value={awaitingCount}
          sub={awaitingCount > 0 ? "pending review" : "nothing pending"}
          tone={awaitingCount > 0 ? "amber" : "fg"}
        />
      </Panel>

      <Panel title="activity" sub="24h" span={2} height="140px" className="sm:col-span-2" testId="panel-activity">
        <ActivityStripe counts={stripe} className="h-[68px]" onBarClick={handleActivityStripeClick} />
        <HourAxis />
      </Panel>

      <Panel title="recent" sub={`${recentEvents.length} events`} span={2} scroll height="140px" className="sm:col-span-2" testId="panel-recent">
        {activityFeedLoading ? (
          <div className="space-y-2" data-testid="activity-feed-loading">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-7 w-full rounded-sm" />
            ))}
          </div>
        ) : activityFeedError ? (
          <ErrorLine>Could not load recent events.</ErrorLine>
        ) : recentEvents.length === 0 ? (
          <MonoLabel color="dim">no recent events</MonoLabel>
        ) : (
          <div data-testid="activity-feed-list">
            {recentEvents.map((event, index) => (
              <EventRow
                key={`${event.ts}-${index}`}
                event={event}
                onOpenSession={setSelectedSessionId}
              />
            ))}
          </div>
        )}
      </Panel>

      <Panel title="awaiting your action" span={2} scroll className="sm:col-span-2" testId="panel-awaiting-actions">
        {approvalsQuery.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-7 w-full rounded-sm" />
            <Skeleton className="h-7 w-full rounded-sm" />
          </div>
        ) : approvalsQuery.isError ? (
          <ErrorLine>Could not load approvals.</ErrorLine>
        ) : visiblePendingActions.length === 0 ? (
          <MonoLabel color="dim">no items pending review</MonoLabel>
        ) : (
          <div>
            {visiblePendingActions.map((action) => (
              <ActionRow
                key={action.id}
                action={action}
                butlerName={butlerName}
                onApprove={handleApprove}
                onReject={handleReject}
                pendingDecision={pendingDecisions.get(action.id)}
              />
            ))}
          </div>
        )}
      </Panel>

      <Panel
        title="config"
        sub={processFacts?.config_path ?? undefined}
        span={2}
        className="sm:col-span-2"
        testId="panel-config"
      >
        <div className="grid gap-0">
          <KV k="port" v={processFacts?.port ?? butler?.port ?? "--"} mono />
          <KV
            k="registered"
            v={processFacts?.registered_duration_seconds != null ? `${Math.floor(processFacts.registered_duration_seconds / 3600)}h` : "--"}
            mono
          />
          <KV k="modules" v={`${modules.length} registered`} />
          <KV k="schedules" v={`${schedules.length} configured`} />
          <KV k="skills" v={`${skills.length} available`} />
        </div>
      </Panel>

      <ButlerDelegationsPanel butlerName={butlerName} />
    </ButlerPanelGrid>

    <SessionDetailDrawer
      sessionId={selectedSessionId}
      onClose={() => setSelectedSessionId(null)}
    />
    </>
  )
}

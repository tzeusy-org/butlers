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

import { useState } from "react"
import { Link } from "react-router"

import {
  ButlerPanelGrid,
  ErrorLine,
  KpiCell,
  KV,
  MonoLabel,
  Panel,
} from "@/components/butler-detail/atoms"
import { SessionDetailDrawer } from "@/components/sessions/SessionDetailDrawer"
import { Skeleton } from "@/components/ui/skeleton"
import { Time } from "@/components/ui/time"
import { useApprovalActions } from "@/hooks/use-approvals"
import { useButler } from "@/hooks/use-butlers"
import { useButlerActivityFeed } from "@/hooks/use-butler-analytics"
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board"
import { useSpendSummary } from "@/hooks/use-spend"
import { formatCostUsd } from "@/lib/format-cost"
import type { ActivityEventType, ApprovalAction, ButlerActivityEvent } from "@/api/types"

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

/**
 * 24-hour activity stripe. Every bar is a door (bu-86c4c.18): clicking a bar
 * deep-links to the Activity tab's Sessions section, pre-filtered to that
 * hour's window, instead of a purely decorative chart.
 */
function ActivityStripe({ values }: { values: number[] }) {
  const max = Math.max(...values, 1)
  return (
    <div className="flex h-[68px] items-end gap-px" aria-label="24-hour activity">
      {values.map((value, index) => {
        const height = value === 0 ? 2 : 2 + Math.round((value / max) * 66)
        const { since, until } = stripeSlotWindow(index)
        const hoursAgo = 23 - index
        return (
          <Link
            key={index}
            to={`?tab=activity&section=sessions&since=${encodeURIComponent(since)}&until=${encodeURIComponent(until)}`}
            aria-label={`${value} session${value === 1 ? "" : "s"}, ${hoursAgo === 0 ? "this hour" : `${hoursAgo}h ago`}`}
            data-testid="activity-stripe-bar"
            className={[
              "flex-1 rounded-[1px] transition-colors hover:bg-primary/70",
              value === 0 ? "bg-muted" : "bg-foreground/70",
            ].join(" ")}
            style={{ height }}
          />
        )
      })}
    </div>
  )
}

function HourAxis() {
  return (
    <div className="mt-2 flex justify-between font-mono text-[9px] text-muted-foreground">
      {["00", "03", "06", "09", "12", "15", "18", "21", "now"].map((label) => (
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

function ActionRow({ action, butlerName }: { action: ApprovalAction; butlerName: string }) {
  return (
    <div className="grid grid-cols-[8px_minmax(0,1fr)_auto] items-baseline gap-3 border-b border-border/40 py-2 last:border-b-0">
      <span className="mt-1.5 h-1.5 w-1.5 rounded-[1px] bg-[var(--amber)]" aria-hidden="true" />
      <span className="min-w-0 truncate text-xs">
        {action.agent_summary || action.tool_name}
        <span className="text-muted-foreground"> · </span>
        <Time value={action.requested_at} mode="relative" />
      </span>
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
  const { data: butlerResponse, isLoading: butlerLoading } = useButler(butlerName)
  const { rows } = useButlerStatusBoard()
  const costQuery = useSpendSummary("today")
  const approvalsQuery = useApprovalActions({ status: "pending", butler: butlerName, limit: 5 })
  const {
    data: activityFeedData,
    isLoading: activityFeedLoading,
    isError: activityFeedError,
  } = useButlerActivityFeed(butlerName, 5)

  // Session drawer state for the "recent events" door (bu-86c4c.18): clicking
  // a session_completed row opens its transcript in place instead of leaving
  // the operator on a dead-end line of text.
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null)

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
  const costToday = costQuery.data?.data?.by_butler?.[butlerName] ?? 0
  const pendingActions = approvalsQuery.data?.data ?? []
  const recentEvents = activityFeedData?.events ?? []
  const stripe = row?.hourlyStripe ?? Array(24).fill(0)
  const status = butler?.status ?? row?.status
  // meta.total (not the page-size-capped result length) is the true count of
  // pending approvals -- the KPI previously read "5" when 20 were pending
  // because it counted the preview page instead of the real total.
  const awaitingCount = approvalsQuery.data?.meta?.total ?? pendingActions.length

  return (
    <>
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
            value={formatCurrency(costToday)}
            sub={`${formatCurrency(sessions24h > 0 ? costToday / sessions24h : 0)} / session`}
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
        <ActivityStripe values={stripe} />
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
        ) : pendingActions.length === 0 ? (
          <MonoLabel color="dim">no items pending review</MonoLabel>
        ) : (
          <div>
            {pendingActions.map((action) => (
              <ActionRow key={action.id} action={action} butlerName={butlerName} />
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
    </ButlerPanelGrid>

    <SessionDetailDrawer
      sessionId={selectedSessionId}
      onClose={() => setSelectedSessionId(null)}
    />
    </>
  )
}

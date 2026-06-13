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

import { Link } from "react-router"

import {
  ButlerPanelGrid,
  ErrorLine,
  KpiCell,
  KV,
  MonoLabel,
  Panel,
} from "@/components/butler-detail/atoms"
import { Skeleton } from "@/components/ui/skeleton"
import { Time } from "@/components/ui/time"
import { useApprovalActions } from "@/hooks/use-approvals"
import { useButler } from "@/hooks/use-butlers"
import { useButlerActivityFeed } from "@/hooks/use-butler-analytics"
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board"
import { useSpendSummary } from "@/hooks/use-spend"
import type { ActivityEventType, ApprovalAction } from "@/api/types"

interface ButlerOverviewTabProps {
  butlerName: string
}

function formatCurrency(amount: number | null | undefined): string {
  if (amount == null) return "--"
  if (amount < 0.01) return "$0.00"
  return `$${amount.toFixed(2)}`
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

function ActivityStripe({ values }: { values: number[] }) {
  const max = Math.max(...values, 1)
  return (
    <div className="flex h-[68px] items-end gap-px" aria-label="24-hour activity">
      {values.map((value, index) => {
        const height = value === 0 ? 2 : 2 + Math.round((value / max) * 66)
        return (
          <span
            key={index}
            className={value === 0 ? "flex-1 rounded-[1px] bg-muted" : "flex-1 rounded-[1px] bg-foreground/70"}
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

function ActionRow({ action }: { action: ApprovalAction }) {
  return (
    <div className="grid grid-cols-[8px_minmax(0,1fr)_auto] items-baseline gap-3 border-b border-border/40 py-2 last:border-b-0">
      <span className="mt-1.5 h-1.5 w-1.5 rounded-[1px] bg-amber-500" aria-hidden="true" />
      <span className="min-w-0 truncate text-xs">
        {action.agent_summary || action.tool_name}
        <span className="text-muted-foreground"> · </span>
        <Time value={action.requested_at} mode="relative" />
      </span>
      <Link
        to="/approvals"
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
  const awaitingCount = pendingActions.length

  return (
    <ButlerPanelGrid
      className="sm:grid-cols-2 md:grid-cols-4"
      data-testid="overview-panel-grid"
    >
      <Panel title="status" testId="panel-status">
        <div className="flex items-center gap-2">
          <span
            className={[
              "h-2 w-2 rounded-full",
              statusTone(status) === "green" && "bg-emerald-500",
              statusTone(status) === "amber" && "bg-amber-500",
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
              <div
                key={`${event.ts}-${index}`}
                className="grid grid-cols-[50px_minmax(0,1fr)_auto] items-baseline gap-3 border-b border-border/40 py-1.5 last:border-b-0"
                data-testid="activity-feed-row"
              >
                <span className="font-mono text-[11px] text-muted-foreground">
                  <Time value={event.ts} mode="relative" compact />
                </span>
                <span className="min-w-0 truncate text-xs">{event.summary}</span>
                <EventKind eventType={event.event_type} />
              </div>
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
              <ActionRow key={action.id} action={action} />
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
  )
}

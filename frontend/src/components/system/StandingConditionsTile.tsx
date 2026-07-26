// ---------------------------------------------------------------------------
// StandingConditionsTile -- standing condition ledger, both flavors
// (bu-27dxl.6.2 / bu-ep4ks.3 / bu-ep4ks.6)
//
// Data source: useSystemConditions -> GET /api/system/conditions, called
// twice (ledger="infra" and ledger="owner") and merged into one list, most-
// recently-detected first. Opens the door on public.infra_conditions: until
// now an L0-L3 escalating outage was durable in Postgres but visible only
// via psql, and QA-dispatch Gate 5.5 suppressed repeat findings against it
// invisibly. bu-ep4ks.6 generalizes the same lifecycle to owner-facing
// standing concerns (public.owner_conditions -- an overdue bill, a spending
// anomaly still true this month) and surfaces them on this SAME panel rather
// than a duplicate one, distinguished by a small ledger badge per row.
// Also surfaces, per infra condition, how many QA dispatches it suppressed
// (useInfraConditionSuppressionCounts -> GET /api/healing/dispatch-events?
// decision=infra_condition_open), joined on the shared fingerprint identity
// -- the concrete "link to the condition that suppressed them" slice 3 asks
// for. Owner conditions have no QA-dispatch suppression concept, so that
// chip is only ever computed/shown for ledger="infra" rows.
// ---------------------------------------------------------------------------

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { SourceDegradedNote } from "@/components/ui/query-boundary"
import { Skeleton } from "@/components/ui/skeleton"
import { StateDot, type DispatchState } from "@/components/ui/StateDot"
import { Time } from "@/components/ui/time"
import { useSystemConditions } from "@/hooks/use-system"
import { useInfraConditionSuppressionCounts } from "@/hooks/use-healing"
import { formatDurationCompact } from "@/lib/format-duration"
import type { ConditionEntry } from "@/api/types"

function TileSkeleton() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Standing Conditions</CardTitle>
        <CardDescription>Infrastructure outages and owner-facing concerns tracked by the reliability ledger</CardDescription>
      </CardHeader>
      <CardContent>
        <div data-testid="standing-conditions-skeleton" className="space-y-2">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-5 w-44" />
        </div>
      </CardContent>
    </Card>
  )
}

function TileFrame({ children, testId }: { children: React.ReactNode; testId?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Standing Conditions</CardTitle>
        <CardDescription>Infrastructure outages and owner-facing concerns tracked by the reliability ledger</CardDescription>
      </CardHeader>
      <CardContent data-testid={testId}>{children}</CardContent>
    </Card>
  )
}

function conditionDotState(state: string): DispatchState {
  if (state === "open") return "error"
  if (state === "aging") return "degraded"
  return "ok"
}

function supersedingIdentityVersion(condition: ConditionEntry): number | null {
  if (condition.ledger !== "infra" || !condition.metadata) return null
  const payload = condition.metadata.identity_payload
  if (!payload || typeof payload !== "object") return null
  const { resolution_reason: reason, successor } = payload as Record<string, unknown>
  if (reason !== "superseded_by_identity_version_bump" || !successor || typeof successor !== "object") {
    return null
  }
  const version = (successor as Record<string, unknown>).version
  return typeof version === "number" ? version : null
}

function ConditionRow({
  condition,
  suppressedCount,
}: {
  condition: ConditionEntry
  /** null means the suppression-count source is degraded -- render nothing rather than a fabricated 0. */
  suppressedCount: number | null
}) {
  const isResolved = condition.state === "resolved"
  const supersededByVersion = supersedingIdentityVersion(condition)
  return (
    <li className="text-sm" data-testid="standing-condition-row">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 min-w-0">
          <StateDot state={conditionDotState(condition.state)} />
          <span
            className="text-muted-foreground shrink-0 rounded border px-1 text-[10px] uppercase"
            data-testid="standing-condition-ledger-badge"
          >
            {condition.ledger === "owner" ? "Owner" : "Infra"}
          </span>
          <span className="font-medium truncate">{condition.source}</span>
          <span className="text-muted-foreground font-mono text-xs shrink-0">
            {condition.escalation_level}
          </span>
        </span>
        <span className="text-muted-foreground tabular-nums shrink-0 text-xs uppercase">
          {condition.state}
        </span>
      </div>
      {condition.summary ? (
        <div className="text-muted-foreground text-xs truncate" title={condition.summary}>
          {condition.summary}
        </div>
      ) : null}
      <div className="text-muted-foreground text-xs">
        {isResolved ? (
          supersededByVersion !== null ? (
            <>
              Superseded by identity version v{supersededByVersion} <Time value={condition.resolved_at ?? condition.last_confirmed_at} mode="relative" />
            </>
          ) : (
            <>
              Resolved <Time value={condition.resolved_at ?? condition.last_confirmed_at} mode="relative" />
              {condition.recovered_after_s != null
                ? ` (recovered after ${formatDurationCompact(condition.recovered_after_s * 1000)})`
                : null}
            </>
          )
        ) : (
          <>
            Detected <Time value={condition.first_detected_at} mode="relative" />
          </>
        )}
        {suppressedCount !== null && suppressedCount > 0 ? (
          <span data-testid="condition-suppressed-count">
            {" "}
            · {suppressedCount} QA dispatch{suppressedCount === 1 ? "" : "es"} suppressed
          </span>
        ) : null}
      </div>
    </li>
  )
}

/** Merge two most-recently-detected-first lists into one, deduped by id. */
function mergeConditions(a: ConditionEntry[], b: ConditionEntry[]): ConditionEntry[] {
  const byId = new Map<string, ConditionEntry>()
  for (const condition of [...a, ...b]) {
    if (!byId.has(condition.id)) byId.set(condition.id, condition)
  }
  return Array.from(byId.values()).sort((x, y) =>
    x.first_detected_at < y.first_detected_at ? 1 : x.first_detected_at > y.first_detected_at ? -1 : 0,
  )
}

export function StandingConditionsTile() {
  const infraQuery = useSystemConditions({ limit: 20 })
  const ownerQuery = useSystemConditions({ limit: 20, ledger: "owner" })
  const { counts: suppressedCounts, isError: suppressionCountsError } =
    useInfraConditionSuppressionCounts()

  if (infraQuery.isPending || ownerQuery.isPending) return <TileSkeleton />

  if (infraQuery.isError && ownerQuery.isError) {
    return (
      <TileFrame testId="standing-conditions-error">
        <SourceDegradedNote label="Standing conditions" detail="could not be reached" />
      </TileFrame>
    )
  }

  const infraFacts = infraQuery.isError ? undefined : infraQuery.data?.data
  const ownerFacts = ownerQuery.isError ? undefined : ownerQuery.data?.data
  const infraAvailable = Boolean(infraFacts?.conditions_available)
  const ownerAvailable = Boolean(ownerFacts?.conditions_available)

  if (!infraAvailable && !ownerAvailable) {
    return (
      <TileFrame testId="standing-conditions-degraded">
        <SourceDegradedNote label="Standing conditions" detail="reliability ledger unavailable" />
      </TileFrame>
    )
  }

  const merged = mergeConditions(
    infraAvailable ? (infraFacts?.conditions ?? []) : [],
    ownerAvailable ? (ownerFacts?.conditions ?? []) : [],
  )

  if (merged.length === 0 && infraAvailable && ownerAvailable) {
    return (
      <TileFrame testId="standing-conditions-empty">
        <p className="text-muted-foreground text-sm">No standing conditions recorded.</p>
      </TileFrame>
    )
  }

  return (
    <TileFrame testId="standing-conditions-content">
      {!infraAvailable ? (
        <SourceDegradedNote
          className="mb-2"
          label="Infrastructure conditions"
          detail="reliability ledger unavailable"
          testId="standing-conditions-infra-degraded"
        />
      ) : null}
      {!ownerAvailable ? (
        <SourceDegradedNote
          className="mb-2"
          label="Owner conditions"
          detail="condition ledger unavailable"
          testId="standing-conditions-owner-degraded"
        />
      ) : null}
      {suppressionCountsError ? (
        <SourceDegradedNote
          className="mb-2"
          label="QA dispatch suppression counts"
          testId="standing-conditions-suppression-degraded"
        />
      ) : null}
      {merged.length === 0 ? (
        <p className="text-muted-foreground text-sm">No standing conditions recorded.</p>
      ) : (
        <ul className="space-y-3 max-h-[320px] overflow-y-auto">
          {merged.map((condition) => (
            <ConditionRow
              key={condition.id}
              condition={condition}
              suppressedCount={
                condition.ledger !== "infra"
                  ? null
                  : suppressionCountsError
                    ? null
                    : (suppressedCounts.get(condition.fingerprint) ?? 0)
              }
            />
          ))}
        </ul>
      )}
    </TileFrame>
  )
}

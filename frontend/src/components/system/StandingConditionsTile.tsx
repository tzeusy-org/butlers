// ---------------------------------------------------------------------------
// StandingConditionsTile -- standing condition ledger, both flavors
// (bu-27dxl.6.2 / bu-ep4ks.3 / bu-ep4ks.6 / bu-jyd6e)
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
//
// bu-jyd6e / RFC 0026: commitments ("I told Sam I'd send him that book") are
// not a fourth ledger -- they are owner_conditions rows whose
// `metadata.class` is "commitment" (butlers.core.commitments). They already
// arrived on this panel as bare source/summary rows; this slice reads the
// metadata convention that producer writes -- kind, direction,
// counterparty_entity_id, deadline, confidence -- and renders it as
// structured fields, plus a filter across the two classes. Everything about
// the non-commitment rendering path is deliberately untouched: a row with no
// commitment metadata takes exactly the same code it took before.
// ---------------------------------------------------------------------------

import { useMemo, useState } from "react"

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
import { useRelationshipEntitiesByIds } from "@/hooks/use-entities"
import { useInfraConditionSuppressionCounts } from "@/hooks/use-healing"
import { formatDurationCompact } from "@/lib/format-duration"
import { cn } from "@/lib/utils"
import type { ConditionEntry } from "@/api/types"

// ---------------------------------------------------------------------------
// Commitment metadata convention (butlers/core/commitments.py)
//
// Mirrored, not imported -- the wire carries `metadata` as an opaque JSONB
// object, so every field below is validated here before it is rendered. A
// row whose metadata does not match this shape is not a commitment and falls
// through to the pre-existing rendering path untouched.
// ---------------------------------------------------------------------------

const COMMITMENT_METADATA_CLASS = "commitment"

/** Confidence at or above which the producer considers a commitment surfaceable. */
const SURFACING_CONFIDENCE_THRESHOLD = 0.8

const KIND_LABELS: Record<string, string> = {
  promise: "Promise",
  waiting_for: "Waiting for",
  follow_up: "Follow-up",
  obligation: "Obligation",
  decision: "Decision",
}

type CommitmentDirection = "owner_to_other" | "other_to_owner" | "self"

const DIRECTION_GLYPHS: Record<CommitmentDirection, { glyph: string; label: string }> = {
  owner_to_other: { glyph: "→", label: "Outgoing: you owe the counterparty" },
  other_to_owner: { glyph: "←", label: "Incoming: the counterparty owes you" },
  self: { glyph: "↺", label: "Self-commitment: no counterparty" },
}

interface CommitmentFields {
  kind: string | null
  direction: CommitmentDirection | null
  counterpartyEntityId: string | null
  /** ISO-8601 deadline, present only when it parses to a real instant. */
  deadline: string | null
  deadlineAt: number | null
  confidence: number | null
}

function isDirection(value: unknown): value is CommitmentDirection {
  return value === "owner_to_other" || value === "other_to_owner" || value === "self"
}

/**
 * Read the commitment convention off one condition's metadata.
 *
 * Returns null for every row that is not a commitment -- which is the whole
 * regression guarantee: a null here means the caller renders the row exactly
 * as it did before this component knew commitments existed. Each field is
 * independently optional, so a producer that omits (or malforms) a deadline
 * still yields a commitment with the rest of its fields intact rather than
 * demoting the whole row.
 */
function commitmentFields(condition: ConditionEntry): CommitmentFields | null {
  const metadata = condition.metadata
  if (!metadata || metadata.class !== COMMITMENT_METADATA_CLASS) return null

  const rawDeadline = typeof metadata.deadline === "string" ? metadata.deadline : null
  const parsedDeadline = rawDeadline === null ? NaN : Date.parse(rawDeadline)
  const deadlineAt = Number.isNaN(parsedDeadline) ? null : parsedDeadline

  const rawConfidence = metadata.confidence
  const confidence =
    typeof rawConfidence === "number" && Number.isFinite(rawConfidence) ? rawConfidence : null

  return {
    kind: typeof metadata.kind === "string" && metadata.kind !== "" ? metadata.kind : null,
    direction: isDirection(metadata.direction) ? metadata.direction : null,
    counterpartyEntityId:
      typeof metadata.counterparty_entity_id === "string" && metadata.counterparty_entity_id !== ""
        ? metadata.counterparty_entity_id
        : null,
    deadline: deadlineAt === null ? null : rawDeadline,
    deadlineAt,
    confidence,
  }
}

/** One condition paired with its commitment reading (null = not a commitment). */
interface DecoratedCondition {
  condition: ConditionEntry
  commitment: CommitmentFields | null
}

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

// bu-o4i4j: `resolution_reason` has one home -- top-level `metadata` -- for
// every resolution path, so this reads it there rather than having to know
// which path closed the episode. The `successor` cross-reference is identity
// lineage and stays under `identity_payload` beside the `version` it names.
function supersedingIdentityVersion(condition: ConditionEntry): number | null {
  if (condition.ledger !== "infra" || !condition.metadata) return null
  if (condition.metadata.resolution_reason !== "superseded_by_identity_version_bump") return null
  const payload = condition.metadata.identity_payload
  if (!payload || typeof payload !== "object") return null
  const { successor } = payload as Record<string, unknown>
  if (!successor || typeof successor !== "object") return null
  const version = (successor as Record<string, unknown>).version
  return typeof version === "number" ? version : null
}

/**
 * Whether a commitment's deadline has already passed while the commitment is
 * still standing. A deadline in the past on an already-resolved commitment is
 * history, not a warning, so only a still-standing commitment can be overdue.
 *
 * A standalone module-level function (same shape as DecisionsPage's
 * resolveExportAsOfIsWarn and ButlerActivityTab's window helper) so the
 * react-hooks/purity rule does not flag a direct Date.now() call inside a
 * component body. Reading the clock here rather than threading a `now` prop
 * down from the tile also keeps the freshness of the comparison local to the
 * one place that needs it.
 */
function deadlineIsOverdue(deadlineAt: number | null, isResolved: boolean): boolean {
  if (isResolved || deadlineAt === null) return false
  return deadlineAt < Date.now()
}

/**
 * The commitment-only detail line: who, which way, what kind, by when.
 *
 * Rendered *in addition to* the shared row chrome, never instead of it --
 * a commitment is still a standing condition and keeps its state dot,
 * escalation level, ledger badge and summary.
 */
function CommitmentDetail({
  commitment,
  counterpartyName,
  isResolved,
}: {
  commitment: CommitmentFields
  /** Resolved display name, or null when the id is unknown/unresolved. */
  counterpartyName: string | null
  isResolved: boolean
}) {
  const direction = commitment.direction ? DIRECTION_GLYPHS[commitment.direction] : null
  const isOverdue = deadlineIsOverdue(commitment.deadlineAt, isResolved)
  const showConfidence =
    commitment.confidence !== null && commitment.confidence >= SURFACING_CONFIDENCE_THRESHOLD

  return (
    <div
      className="text-muted-foreground mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs"
      data-testid="commitment-detail"
    >
      {direction ? (
        <span
          role="img"
          aria-label={direction.label}
          title={direction.label}
          data-testid="commitment-direction"
          data-direction={commitment.direction}
          className="text-foreground shrink-0"
        >
          {direction.glyph}
        </span>
      ) : null}
      {commitment.counterpartyEntityId !== null ? (
        counterpartyName !== null ? (
          <span className="text-foreground font-medium" data-testid="commitment-counterparty">
            {counterpartyName}
          </span>
        ) : (
          // Never fabricate a name for an id the entity lookup did not return.
          <span className="italic" data-testid="commitment-counterparty-unresolved">
            Counterparty unresolved
          </span>
        )
      ) : null}
      {commitment.kind !== null ? (
        <span
          className="shrink-0 rounded border px-1 text-[10px] uppercase"
          data-testid="commitment-kind-badge"
          data-kind={commitment.kind}
        >
          {KIND_LABELS[commitment.kind] ?? commitment.kind}
        </span>
      ) : null}
      {commitment.deadline !== null ? (
        <span
          data-testid="commitment-deadline"
          data-overdue={isOverdue ? "true" : "false"}
          className={cn("shrink-0", isOverdue && "text-destructive font-medium")}
        >
          {isOverdue ? "Overdue " : "Due "}
          <Time value={commitment.deadline} mode="relative" />
        </span>
      ) : null}
      {showConfidence ? (
        <span
          className="shrink-0 opacity-70"
          data-testid="commitment-confidence"
          title="High-confidence commitment (eligible for proactive surfacing)"
        >
          High confidence
        </span>
      ) : null}
    </div>
  )
}

function ConditionRow({
  condition,
  commitment,
  counterpartyName,
  suppressedCount,
}: {
  condition: ConditionEntry
  /** Commitment reading, or null for every non-commitment condition. */
  commitment: CommitmentFields | null
  counterpartyName: string | null
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
      {commitment ? (
        <CommitmentDetail
          commitment={commitment}
          counterpartyName={counterpartyName}
          isResolved={isResolved}
        />
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

type ConditionFilter = "all" | "commitments" | "non-commitments"

const FILTER_OPTIONS: { value: ConditionFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "commitments", label: "Commitments" },
  { value: "non-commitments", label: "Other" },
]

/**
 * Class filter across the merged ledger.
 *
 * Same three-button idiom as `RangeToggle`, kept local rather than
 * generalized: that component's value type is the time-range vocabulary, and
 * widening it for one call site would buy nothing here.
 */
function ConditionFilterToggle({
  value,
  onChange,
}: {
  value: ConditionFilter
  onChange: (next: ConditionFilter) => void
}) {
  return (
    <div
      role="group"
      aria-label="Condition class"
      data-testid="standing-conditions-filter"
      className="mb-2 inline-flex items-center rounded-md border border-border"
    >
      {FILTER_OPTIONS.map(({ value: optValue, label }) => {
        const isActive = value === optValue
        return (
          <button
            key={optValue}
            type="button"
            aria-pressed={isActive}
            onClick={() => onChange(optValue)}
            data-testid={`standing-conditions-filter-${optValue}`}
            className={cn(
              "inline-flex items-center justify-center px-2 py-1",
              "font-mono text-[10px] uppercase tabular-nums",
              "transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-background focus-visible:ring-offset-1",
              isActive && "bg-foreground text-background",
              !isActive && "bg-transparent text-foreground hover:bg-muted",
              "first:rounded-l-sm last:rounded-r-sm",
            )}
          >
            {label}
          </button>
        )
      })}
    </div>
  )
}

/**
 * Float deadline-bearing commitments to the top, soonest (and most overdue)
 * first; leave every other row in the merged list's existing
 * most-recently-detected-first order.
 *
 * Deliberately narrower than "sort the tail by escalation level": that would
 * re-order the non-commitment rows this panel has always ordered by
 * detection recency, which is the one thing the commitment slice must not
 * change.
 */
function sortForDisplay(rows: DecoratedCondition[]): DecoratedCondition[] {
  const deadlined: DecoratedCondition[] = []
  const rest: DecoratedCondition[] = []
  for (const row of rows) {
    if (row.commitment?.deadlineAt != null) deadlined.push(row)
    else rest.push(row)
  }
  deadlined.sort((x, y) => (x.commitment?.deadlineAt ?? 0) - (y.commitment?.deadlineAt ?? 0))
  return [...deadlined, ...rest]
}

/** Collect the distinct counterparty ids a commitment row needs a name for. */
function collectCounterpartyIds(conditions: ConditionEntry[]): string[] {
  const ids = new Set<string>()
  for (const condition of conditions) {
    const id = commitmentFields(condition)?.counterpartyEntityId
    if (id) ids.add(id)
  }
  // Sorted so the react-query key is stable across re-renders that produce
  // the same id set in a different order.
  return Array.from(ids).sort()
}

export function StandingConditionsTile() {
  const infraQuery = useSystemConditions({ limit: 20 })
  const ownerQuery = useSystemConditions({ limit: 20, ledger: "owner" })
  const { counts: suppressedCounts, isError: suppressionCountsError } =
    useInfraConditionSuppressionCounts()
  const [filter, setFilter] = useState<ConditionFilter>("all")

  // Counterparty ids are read off the raw query payloads rather than the
  // merged list below, because this hook must run before the early returns.
  const infraConditions = infraQuery.data?.data.conditions
  const ownerConditions = ownerQuery.data?.data.conditions
  const counterpartyIds = useMemo(
    () => collectCounterpartyIds([...(infraConditions ?? []), ...(ownerConditions ?? [])]),
    [infraConditions, ownerConditions],
  )
  const counterpartyQuery = useRelationshipEntitiesByIds({ ids: counterpartyIds })
  const counterpartyNames = useMemo(() => {
    const byId = new Map<string, string>()
    // Written without a `?? []` coercion on purpose: an unresolved id renders
    // as "Counterparty unresolved" rather than silently vanishing, so there
    // is no fabricated-calm default to fall back to (check-query-result-
    // coercion.mjs guards exactly this shape).
    const items = counterpartyQuery.data?.items
    if (items === undefined) return byId
    for (const item of items) byId.set(item.id, item.canonical_name)
    return byId
  }, [counterpartyQuery.data])

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

  const decorated: DecoratedCondition[] = merged.map((condition) => ({
    condition,
    commitment: commitmentFields(condition),
  }))
  const visible = sortForDisplay(
    decorated.filter(({ commitment }) =>
      filter === "all"
        ? true
        : filter === "commitments"
          ? commitment !== null
          : commitment === null,
    ),
  )

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
        <>
          <ConditionFilterToggle value={filter} onChange={setFilter} />
          {visible.length === 0 ? (
            <p className="text-muted-foreground text-sm" data-testid="standing-conditions-filtered-empty">
              {filter === "commitments"
                ? "No commitments recorded."
                : "No non-commitment conditions recorded."}
            </p>
          ) : (
            <ul className="space-y-3 max-h-[320px] overflow-y-auto">
              {visible.map(({ condition, commitment }) => (
                <ConditionRow
                  key={condition.id}
                  condition={condition}
                  commitment={commitment}
                  counterpartyName={
                    commitment?.counterpartyEntityId
                      ? (counterpartyNames.get(commitment.counterpartyEntityId) ?? null)
                      : null
                  }
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
        </>
      )}
    </TileFrame>
  )
}

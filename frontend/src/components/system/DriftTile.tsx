// ---------------------------------------------------------------------------
// DriftTile -- migration-drift sentinel (bu-9r3hd.1)
//
// Data source: useDriftFacts -> GET /api/system/drift
// Fields used: is_drifted, drifted, first_detected_at, escalated,
//              drift_check_available
//
// Red clause when the codebase's Alembic head has drifted from what a
// schema's alembic_version table actually holds (the bu-zhfd0 incident: a
// merged migration that never got deployed). Escalates to a QA case once the
// drift has persisted more than 24h (see butlers.jobs.deploy_drift).
// ---------------------------------------------------------------------------

import {
  Tile,
  TileContent,
  TileDescription,
  TileHeader,
  TileTitle,
} from "@/components/ui/Tile"
import { Skeleton } from "@/components/ui/skeleton"
import { Time } from "@/components/ui/time"
import { useDriftFacts } from "@/hooks/use-system"

// ---------------------------------------------------------------------------
// Loading / error sub-components
// ---------------------------------------------------------------------------

function TileSkeleton() {
  return (
    <Tile loading>
      <TileHeader>
        <TileTitle>Migration Drift</TileTitle>
        <TileDescription>Codebase vs. deployed schema state</TileDescription>
      </TileHeader>
      <TileContent>
        <div data-testid="drift-tile-skeleton" className="space-y-2">
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-4 w-52" />
        </div>
      </TileContent>
    </Tile>
  )
}

function TileError() {
  return (
    <Tile degraded>
      <TileHeader>
        <TileTitle>Migration Drift</TileTitle>
        <TileDescription>Codebase vs. deployed schema state</TileDescription>
      </TileHeader>
      <TileContent>
        <p data-testid="drift-tile-error" className="text-destructive text-sm">
          Could not load migration drift status.
        </p>
      </TileContent>
    </Tile>
  )
}

// ---------------------------------------------------------------------------
// DriftTile
// ---------------------------------------------------------------------------

/**
 * Displays the migration-drift sentinel's live comparison.
 *
 * Three states:
 *   - Unavailable (drift_check_available=false): the comparison itself
 *     failed -- rendered as "unknown", never a fabricated all-clear.
 *   - Aligned (is_drifted=false): green "In sync" badge.
 *   - Drifted (is_drifted=true): red badge, one row per out-of-sync chain,
 *     plus first-detected time and whether it has already been escalated
 *     to QA (>24h persistence).
 */
export function DriftTile() {
  const { data: response, isPending, isError } = useDriftFacts()

  if (isPending) return <TileSkeleton />
  if (isError) return <TileError />

  const facts = response?.data

  if (!facts?.drift_check_available) {
    return (
      <Tile degraded>
        <TileHeader>
          <TileTitle>Migration Drift</TileTitle>
          <TileDescription>Codebase vs. deployed schema state</TileDescription>
        </TileHeader>
        <TileContent data-testid="drift-tile-unavailable">
          <p className="text-muted-foreground text-sm">Drift check unavailable.</p>
          <p className="text-muted-foreground mt-1 text-xs">
            The comparison itself failed. This is not a clean bill of health.
          </p>
        </TileContent>
      </Tile>
    )
  }

  if (!facts.is_drifted) {
    return (
      <Tile>
        <TileHeader>
          <TileTitle>Migration Drift</TileTitle>
          <TileDescription>Codebase vs. deployed schema state</TileDescription>
        </TileHeader>
        <TileContent data-testid="drift-tile-clean">
          <span
            data-testid="drift-tile-clean-badge"
            className="bg-[var(--green)] text-white inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
          >
            In sync
          </span>
        </TileContent>
      </Tile>
    )
  }

  return (
    <Tile className="border-[var(--red)]/40">
      <TileHeader>
        <TileTitle>Migration Drift</TileTitle>
        <TileDescription>Codebase vs. deployed schema state</TileDescription>
      </TileHeader>
      <TileContent data-testid="drift-tile-drifted">
        <span
          data-testid="drift-tile-drifted-badge"
          className="bg-[var(--red)] text-white mb-3 inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
        >
          {facts.drifted.length} chain{facts.drifted.length === 1 ? "" : "s"} drifted
        </span>
        <ul className="space-y-1 text-sm">
          {facts.drifted.map((d) => (
            <li key={`${d.schema_name}:${d.chain}`} className="font-mono text-xs">
              {d.schema_name}/{d.chain}: expected {d.expected_head}, has{" "}
              {d.actual_revision ?? "none"}
            </li>
          ))}
        </ul>
        {facts.first_detected_at && (
          <p className="text-muted-foreground mt-3 text-xs">
            First detected <Time value={facts.first_detected_at} mode="relative" />
            {facts.escalated ? ", escalated to QA" : ""}
          </p>
        )}
      </TileContent>
    </Tile>
  )
}

// ---------------------------------------------------------------------------
// EgressCatalogTile -- external-actor egress catalog (owner-only)
// (bu-ngfzz.6)
//
// Data source: useEgressFacts -> GET /api/system/egress
// Fields used: actors (display_name, last_seen_at, total_calls), catalog_covers_from
// The hook surfaces `isForbidden` separately for non-owner callers (HTTP 403).
// ---------------------------------------------------------------------------

import {
  Tile,
  TileContent,
  TileDescription,
  TileFooter,
  TileHeader,
  TileTitle,
} from "@/components/ui/Tile"
import { RowLink } from "@/components/ui/RowLink"
import { Skeleton } from "@/components/ui/skeleton"
import { Time } from "@/components/ui/time"
import type { EgressActor } from "@/api/types"
import { useEgressFacts } from "@/hooks/use-system"

/**
 * The server names the audit-log action each registered actor stands for (bu-vbznk); it is
 * null for the "other" bucket. Declared here, not in the shared API types, so the client
 * never re-derives the actor registry.
 */
type LinkableEgressActor = EgressActor & { audit_action?: string | null }

/** Audit-log deep link for an actor's events. noise=all: egress actions sit outside the default allowlist. */
function egressAuditLogPath(auditAction: string): string {
  return `/audit-log?action=${encodeURIComponent(auditAction)}&noise=all`
}

// ---------------------------------------------------------------------------
// Loading / error sub-components
// ---------------------------------------------------------------------------

function TileSkeleton() {
  return (
    <Tile loading>
      <TileHeader>
        <TileTitle>Data Egress</TileTitle>
        <TileDescription>External services that received data</TileDescription>
      </TileHeader>
      <TileContent>
        <div data-testid="egress-tile-skeleton" className="space-y-2">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-5 w-44" />
        </div>
      </TileContent>
    </Tile>
  )
}

function TileError() {
  return (
    <Tile degraded>
      <TileHeader>
        <TileTitle>Data Egress</TileTitle>
        <TileDescription>External services that received data</TileDescription>
      </TileHeader>
      <TileContent>
        <p data-testid="egress-tile-error" className="text-destructive text-sm">
          Could not load egress catalog.
        </p>
      </TileContent>
    </Tile>
  )
}

// ---------------------------------------------------------------------------
// EgressCatalogTile
// ---------------------------------------------------------------------------

/**
 * Displays the owner-only data-egress catalog: which external actors have
 * received data from this instance, when they were last seen, and how many
 * calls were made.
 *
 * Non-owner callers see a permission notice (403 -> isForbidden).
 * When no egress has been recorded yet, renders a neutral empty-state notice.
 * The catalog_covers_from field shows the earliest audit event timestamp.
 */
export function EgressCatalogTile() {
  const { data: response, isPending, isError, isForbidden } = useEgressFacts()

  if (isPending) return <TileSkeleton />

  if (isForbidden) {
    return (
      <Tile degraded>
        <TileHeader>
          <TileTitle>Data Egress</TileTitle>
          <TileDescription>External services that received data</TileDescription>
        </TileHeader>
        <TileContent data-testid="egress-tile-forbidden">
          <p className="text-muted-foreground text-sm">
            Owner only -- sign in as the owner to view.
          </p>
        </TileContent>
      </Tile>
    )
  }

  if (isError) return <TileError />

  const catalog = response?.data

  if (!catalog || catalog.actors.length === 0) {
    return (
      <Tile>
        <TileHeader>
          <TileTitle>Data Egress</TileTitle>
          <TileDescription>External services that received data</TileDescription>
        </TileHeader>
        <TileContent data-testid="egress-tile-empty">
          <p className="text-muted-foreground text-sm">No external egress recorded yet.</p>
        </TileContent>
      </Tile>
    )
  }

  return (
    <Tile>
      <TileHeader>
        <TileTitle>Data Egress</TileTitle>
        <TileDescription>External services that received data</TileDescription>
      </TileHeader>
      <TileContent data-testid="egress-tile-content" className="max-h-[320px] overflow-y-auto">
        <ul className="space-y-3">
          {(catalog.actors as LinkableEgressActor[]).map((actor) => {
            const row = (
              <>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium truncate" title={actor.display_name}>{actor.display_name}</span>
                  <span className="text-muted-foreground tabular-nums shrink-0">
                    {actor.total_calls.toLocaleString()}{" "}
                    {actor.total_calls === 1 ? "call" : "calls"}
                  </span>
                </div>
                <div className="text-muted-foreground text-xs">
                  Last seen <Time value={actor.last_seen_at} mode="relative" />
                </div>
              </>
            )
            return (
              <li key={actor.actor_id} className="text-sm">
                {actor.audit_action ? (
                  <RowLink
                    to={egressAuditLogPath(actor.audit_action)}
                    aria-label={`View ${actor.display_name} audit events`}
                    className="block no-underline text-inherit -mx-1 rounded px-1 hover:bg-accent/40"
                  >
                    {row}
                  </RowLink>
                ) : (
                  row
                )}
              </li>
            )
          })}
        </ul>
      </TileContent>
      {catalog.catalog_covers_from && (
        <TileFooter>
          <p
            data-testid="egress-tile-covers-from"
            className="text-muted-foreground text-xs"
          >
            Records since{" "}
            <Time value={catalog.catalog_covers_from} mode="absolute" />
          </p>
        </TileFooter>
      )}
    </Tile>
  )
}

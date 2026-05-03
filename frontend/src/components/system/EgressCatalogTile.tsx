// ---------------------------------------------------------------------------
// EgressCatalogTile -- external-actor egress catalog (owner-only)
// (bu-ngfzz.6)
//
// Data source: useEgressFacts -> GET /api/system/egress
// Fields used: actors (display_name, last_seen_at, total_calls), catalog_covers_from
// The hook surfaces `isForbidden` separately for non-owner callers (HTTP 403).
// ---------------------------------------------------------------------------

import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Time } from "@/components/ui/time"
import { useEgressFacts } from "@/hooks/use-system"

// ---------------------------------------------------------------------------
// Loading / error sub-components
// ---------------------------------------------------------------------------

function TileSkeleton() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Data Egress</CardTitle>
        <CardDescription>External services that received data</CardDescription>
      </CardHeader>
      <CardContent>
        <div data-testid="egress-tile-skeleton" className="space-y-2">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-5 w-44" />
        </div>
      </CardContent>
    </Card>
  )
}

function TileError() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Data Egress</CardTitle>
        <CardDescription>External services that received data</CardDescription>
      </CardHeader>
      <CardContent>
        <p data-testid="egress-tile-error" className="text-destructive text-sm">
          Could not load egress catalog.
        </p>
      </CardContent>
    </Card>
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
      <Card>
        <CardHeader>
          <CardTitle>Data Egress</CardTitle>
          <CardDescription>External services that received data</CardDescription>
        </CardHeader>
        <CardContent data-testid="egress-tile-forbidden">
          <p className="text-muted-foreground text-sm">
            Owner only -- sign in as the owner to view.
          </p>
        </CardContent>
      </Card>
    )
  }

  if (isError) return <TileError />

  const catalog = response?.data

  if (!catalog || catalog.actors.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Data Egress</CardTitle>
          <CardDescription>External services that received data</CardDescription>
        </CardHeader>
        <CardContent data-testid="egress-tile-empty">
          <p className="text-muted-foreground text-sm">No external egress recorded yet.</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Data Egress</CardTitle>
        <CardDescription>External services that received data</CardDescription>
      </CardHeader>
      <CardContent data-testid="egress-tile-content" className="max-h-[320px] overflow-y-auto">
        <ul className="space-y-3">
          {catalog.actors.map((actor) => (
            <li key={actor.actor_id} className="text-sm">
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
            </li>
          ))}
        </ul>
      </CardContent>
      {catalog.catalog_covers_from && (
        <CardFooter>
          <p
            data-testid="egress-tile-covers-from"
            className="text-muted-foreground text-xs"
          >
            Records since{" "}
            <Time value={catalog.catalog_covers_from} mode="absolute" />
          </p>
        </CardFooter>
      )}
    </Card>
  )
}

// ---------------------------------------------------------------------------
// StoredFunctionsTile -- deployed stored-function bodies vs init-db.sql (bu-bi5an)
//
// Data source: useStoredFunctionFacts -> GET /api/system/stored-functions
// Fields used: is_drifted, drifted, not_deployed, matched_count,
//              stored_function_check_available
//
// `not_deployed` is an ordinary state (the function's bootstrap installer has
// not run yet), never rendered as an alarm -- only `drifted` (a deployed body
// that disagrees with the committed source) turns the card red. The API
// carries names, init-db.sql line references, and short digests only, never a
// function body or a digest preimage; this tile renders names and line
// references and never touches the digest fields.
// ---------------------------------------------------------------------------

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useStoredFunctionFacts } from "@/hooks/use-system"

// ---------------------------------------------------------------------------
// Loading / error sub-components
// ---------------------------------------------------------------------------

function TileSkeleton() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Stored Functions</CardTitle>
        <CardDescription>Deployed bodies vs. init-db.sql</CardDescription>
      </CardHeader>
      <CardContent>
        <div data-testid="stored-functions-tile-skeleton" className="space-y-2">
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-4 w-52" />
        </div>
      </CardContent>
    </Card>
  )
}

function TileError() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Stored Functions</CardTitle>
        <CardDescription>Deployed bodies vs. init-db.sql</CardDescription>
      </CardHeader>
      <CardContent>
        <p data-testid="stored-functions-tile-error" className="text-destructive text-sm">
          Could not load stored-function drift status.
        </p>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// StoredFunctionsTile
// ---------------------------------------------------------------------------

/**
 * Displays the stored-function drift comparison's live result.
 *
 * States:
 *   - Unavailable (stored_function_check_available=false): the comparison
 *     itself failed -- rendered as "unknown", never a fabricated all-clear.
 *   - All matched (is_drifted=false, no not_deployed): green "All matched" badge.
 *   - Otherwise: matched/drifted/not_deployed counts, a red section naming
 *     each drifted function with its init-db.sql line reference(s), and an
 *     amber (never red) section naming not_deployed functions -- a distinct,
 *     non-alarm color because not_deployed is an expected state.
 */
export function StoredFunctionsTile() {
  const { data: response, isPending, isError } = useStoredFunctionFacts()

  if (isPending) return <TileSkeleton />
  if (isError) return <TileError />

  const facts = response?.data

  if (!facts?.stored_function_check_available) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Stored Functions</CardTitle>
          <CardDescription>Deployed bodies vs. init-db.sql</CardDescription>
        </CardHeader>
        <CardContent data-testid="stored-functions-tile-unavailable">
          <p className="text-muted-foreground text-sm">Stored-function check unavailable.</p>
          <p className="text-muted-foreground mt-1 text-xs">
            The comparison itself failed. This is not a clean bill of health.
          </p>
        </CardContent>
      </Card>
    )
  }

  const hasNotDeployed = facts.not_deployed.length > 0

  if (!facts.is_drifted && !hasNotDeployed) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Stored Functions</CardTitle>
          <CardDescription>Deployed bodies vs. init-db.sql</CardDescription>
        </CardHeader>
        <CardContent data-testid="stored-functions-tile-clean">
          <span
            data-testid="stored-functions-tile-clean-badge"
            className="bg-[var(--green)] text-white inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
          >
            All {facts.matched_count} matched
          </span>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className={facts.is_drifted ? "border-[var(--red)]/40" : undefined}>
      <CardHeader>
        <CardTitle>Stored Functions</CardTitle>
        <CardDescription>Deployed bodies vs. init-db.sql</CardDescription>
      </CardHeader>
      <CardContent data-testid="stored-functions-tile-drifted">
        <p className="text-muted-foreground mb-3 text-xs">
          {facts.matched_count} matched, {facts.drifted.length} drifted,{" "}
          {facts.not_deployed.length} not deployed
        </p>

        {facts.is_drifted && (
          <div className="mb-3">
            <span
              data-testid="stored-functions-tile-drifted-badge"
              className="bg-[var(--red)] text-white mb-2 inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
            >
              {facts.drifted.length} function{facts.drifted.length === 1 ? "" : "s"} drifted
            </span>
            <ul className="space-y-1 text-sm">
              {facts.drifted.map((entry) => (
                <li key={entry.function} className="font-mono text-xs">
                  {entry.function} (init-db.sql line
                  {entry.committed_lines.length === 1 ? "" : "s"}{" "}
                  {entry.committed_lines.join(", ")})
                </li>
              ))}
            </ul>
          </div>
        )}

        {hasNotDeployed && (
          <div>
            <span
              data-testid="stored-functions-tile-not-deployed-badge"
              className="text-[var(--amber-text)] border-[var(--amber)] mb-2 inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium"
            >
              {facts.not_deployed.length} not deployed
            </span>
            <ul className="space-y-1 text-sm">
              {facts.not_deployed.map((name) => (
                <li key={name} className="font-mono text-xs">
                  {name}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

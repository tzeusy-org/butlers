// ---------------------------------------------------------------------------
// DeploymentTile -- deploy spine's last mile (bu-9r3hd.3 / bu-hmdqz.1)
//
// Data source: useDeploymentFacts -> GET /api/system/deployments
// Fields used: current (git_sha, migration_head, result), recent,
//              commits_behind_main, commits_behind_available
//
// Red clause: "serving <sha>, N commits behind origin/main" whenever the
// current deployment is measurably behind origin/main, or the last deploy
// attempt itself failed. This card had zero consumers before bu-hmdqz.1 --
// the live instance could silently drift 16+ merges behind a stale
// .worktrees/ checkout with nothing on /system saying so.
// ---------------------------------------------------------------------------

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Time } from "@/components/ui/time"
import { useDeploymentFacts } from "@/hooks/use-system"

function shortSha(sha: string): string {
  return sha === "unknown" ? sha : sha.slice(0, 7)
}

// ---------------------------------------------------------------------------
// Loading / error sub-components
// ---------------------------------------------------------------------------

function TileSkeleton() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Deployment</CardTitle>
        <CardDescription>What's actually serving right now</CardDescription>
      </CardHeader>
      <CardContent>
        <div data-testid="deployment-tile-skeleton" className="space-y-2">
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
        <CardTitle>Deployment</CardTitle>
        <CardDescription>What's actually serving right now</CardDescription>
      </CardHeader>
      <CardContent>
        <p data-testid="deployment-tile-error" className="text-destructive text-sm">
          Could not load deployment status.
        </p>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// DeploymentTile
// ---------------------------------------------------------------------------

/**
 * Displays the current deployment ledger entry and how far it trails
 * origin/main.
 *
 * States:
 *   - No deployment recorded yet (current=null): neutral "never recorded".
 *   - Last deploy attempt failed: red badge regardless of commits-behind.
 *   - commits_behind_available=false: "unknown" -- never a fabricated
 *     all-clear.
 *   - commits_behind_main > 0: red clause "serving <sha>, N commits behind
 *     origin/main".
 *   - commits_behind_main === 0: green "up to date with origin/main".
 */
export function DeploymentTile() {
  const { data: response, isPending, isError } = useDeploymentFacts()

  if (isPending) return <TileSkeleton />
  if (isError) return <TileError />

  const facts = response?.data

  if (!facts?.current) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Deployment</CardTitle>
          <CardDescription>What's actually serving right now</CardDescription>
        </CardHeader>
        <CardContent data-testid="deployment-tile-empty">
          <p className="text-muted-foreground text-sm">No deployment recorded yet.</p>
        </CardContent>
      </Card>
    )
  }

  const { current } = facts
  const lastDeployFailed = current.result === "failed"
  const behind = facts.commits_behind_main
  const behindKnown = facts.commits_behind_available
  const isRed = lastDeployFailed || (behindKnown && (behind ?? 0) > 0)

  return (
    <Card className={isRed ? "border-[var(--red)]/40" : undefined}>
      <CardHeader>
        <CardTitle>Deployment</CardTitle>
        <CardDescription>What's actually serving right now</CardDescription>
      </CardHeader>
      <CardContent data-testid="deployment-tile-content">
        {isRed ? (
          <span
            data-testid="deployment-tile-red-badge"
            className="bg-[var(--red)] text-white mb-3 inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
          >
            {lastDeployFailed
              ? "last deploy failed"
              : `serving ${shortSha(current.git_sha)}, ${behind} commit${behind === 1 ? "" : "s"} behind origin/main`}
          </span>
        ) : behindKnown ? (
          <span
            data-testid="deployment-tile-clean-badge"
            className="bg-[var(--green)] text-white mb-3 inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
          >
            up to date with origin/main
          </span>
        ) : (
          <p
            data-testid="deployment-tile-commits-unknown"
            className="text-muted-foreground mb-3 text-xs"
          >
            Commits-behind-origin/main check unavailable.
          </p>
        )}
        <dl className="space-y-1 font-mono text-xs">
          <div>
            <dt className="text-muted-foreground inline">serving: </dt>
            <dd className="inline">{shortSha(current.git_sha)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground inline">migration head: </dt>
            {current.migration_head ? (
              <dd className="inline">{current.migration_head}</dd>
            ) : (
              // Honest unknown -- a null head means the deploy could not read
              // the core migration chain (bu-l94um). Render it as an explicit
              // amber "unknown", never the same calm mono value as a real head.
              <dd
                data-testid="deployment-tile-migration-head-unknown"
                className="text-[var(--amber-text)] inline font-medium"
              >
                head unknown
              </dd>
            )}
          </div>
        </dl>
        <p className="text-muted-foreground mt-3 text-xs">
          Deployed <Time value={current.started_at} mode="relative" />
        </p>
      </CardContent>
    </Card>
  )
}

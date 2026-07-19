// ---------------------------------------------------------------------------
// DeploymentTile -- deploy spine's last mile (bu-9r3hd.3 / bu-hmdqz.1)
//
// Data source: useDeploymentFacts -> GET /api/system/deployments
// Fields used: current (git_sha, migration_head, result, source,
//              serving_mode, serving_worktree), recent,
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

function bindMountedWorktreeTruth(current: {
  source: string | null
  serving_mode: string | null
  serving_worktree: string | null
}): string | null {
  if (current.serving_mode !== "hotreload-worktree") return null

  const actor = current.source === "boot" ? "boot" : "serving"
  const worktree = current.serving_worktree ? ` ${current.serving_worktree}` : ""
  return `${actor} from bind-mounted worktree${worktree} (hotreload)`
}

function servingModeText(current: {
  serving_mode: string | null
  serving_worktree: string | null
}): string {
  if (current.serving_mode === "image") return "image"
  if (current.serving_mode === "hotreload-worktree") {
    return current.serving_worktree
      ? `bind-mounted worktree ${current.serving_worktree} (hotreload)`
      : "bind-mounted worktree (hotreload)"
  }
  return "unknown"
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
 *   - A boot from a bind-mounted linked worktree: red truth clause regardless
 *     of image SHA or commits-behind.
 *   - Last deploy attempt failed: red clause regardless of commits-behind.
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
  const worktreeTruth = bindMountedWorktreeTruth(current)
  const recordedVerb =
    current.source === "boot" ? "Boot recorded" : current.source === "deploy" ? "Deployed" : "Recorded"
  const redClauses = [
    worktreeTruth,
    lastDeployFailed ? "last deploy failed" : null,
    behindKnown && (behind ?? 0) > 0
      ? `serving ${shortSha(current.git_sha)}, ${behind} commit${behind === 1 ? "" : "s"} behind origin/main`
      : null,
  ].filter((clause): clause is string => clause !== null)
  const isRed = redClauses.length > 0

  return (
    <Card className={isRed ? "border-[var(--red)]/40" : undefined}>
      <CardHeader>
        <CardTitle>Deployment</CardTitle>
        <CardDescription>What's actually serving right now</CardDescription>
      </CardHeader>
      <CardContent data-testid="deployment-tile-content">
        {isRed ? (
          <div data-testid="deployment-tile-red-badge" className="mb-3 flex flex-col gap-1">
            {redClauses.map((clause) => (
              <p
                key={clause}
                data-testid={clause === worktreeTruth ? "deployment-tile-red-clause" : undefined}
                className="border-[var(--red)]/40 text-[var(--red-text)] w-fit rounded border px-2 py-0.5 text-xs font-medium"
              >
                {clause}
              </p>
            ))}
          </div>
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
            <dt className="text-muted-foreground inline">source: </dt>
            <dd className="inline">{current.source ?? "unknown"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground inline">serving mode: </dt>
            <dd
              className={
                worktreeTruth ? "text-[var(--red-text)] inline font-medium" : "inline"
              }
            >
              {servingModeText(current)}
            </dd>
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
          {recordedVerb} <Time value={current.started_at} mode="relative" />
        </p>
      </CardContent>
    </Card>
  )
}

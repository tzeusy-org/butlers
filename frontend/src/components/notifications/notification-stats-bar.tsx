import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { StatsSkeleton } from "@/components/skeletons"
import type { NotificationStats } from "@/api/types"
import { Bell, CheckCircle, XCircle, Percent } from "lucide-react"

interface NotificationStatsBarProps {
  stats: NotificationStats | undefined
  isLoading?: boolean
  /**
   * When provided, the Sent/Failed tiles become filter anchors (bu-qvnce.13,
   * JARVIS pursuit move 13 — "stat tiles become filter anchors"): clicking one
   * pivots the page's own status filter to that value instead of only ever
   * displaying an inert count.
   */
  onFilterClick?: (status: "sent" | "failed") => void
}

/** Em-dash placeholder for a tile whose real value is unknown because the
 * source is down. Never a fabricated zero (which reads as a truthful "nothing
 * happened" all-clear). */
const EM_DASH = "—"

export function NotificationStatsBar({ stats, isLoading, onFilterClick }: NotificationStatsBarProps) {
  if (isLoading) {
    return <StatsSkeleton count={4} />
  }

  // source_available === false means the Switchboard notifications source was
  // unreachable: every count below is a zero placeholder, not a real tally.
  // Em-dash the tiles rather than rendering a green 0.0% failure rate that
  // reads as "all delivered" (CLAUDE.md degraded-envelope convention;
  // bu-jad4j.2). Classify-before-flagging: an absent flag (older payload) or
  // `true` with genuine zeros is a legitimate empty state and keeps its 0s.
  const sourceUnavailable = stats?.source_available === false
  const total = stats?.total ?? 0
  const sent = stats?.sent ?? 0
  const failed = stats?.failed ?? 0
  const failureRate = total > 0 ? ((failed / total) * 100).toFixed(1) : "0.0"
  const channels = stats?.by_channel ?? {}

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {/* Total Notifications */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Total Notifications
            </CardTitle>
            <Bell className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold" data-testid="stat-value-total">
              {sourceUnavailable ? EM_DASH : total.toLocaleString()}
            </div>
          </CardContent>
        </Card>

        {/* Sent — clickable filter anchor when onFilterClick is wired */}
        <Card
          role={onFilterClick ? "button" : undefined}
          tabIndex={onFilterClick ? 0 : undefined}
          onClick={onFilterClick ? () => onFilterClick("sent") : undefined}
          onKeyDown={
            onFilterClick
              ? (e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    onFilterClick("sent")
                  }
                }
              : undefined
          }
          className={onFilterClick ? "cursor-pointer transition-colors hover:bg-muted/40" : undefined}
          data-testid="stat-tile-sent"
        >
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Sent</CardTitle>
            <CheckCircle className="h-4 w-4 text-[var(--green)]" />
          </CardHeader>
          <CardContent>
            <div
              className={`text-2xl font-bold ${sourceUnavailable ? "text-muted-foreground" : "text-[var(--green)]"}`}
              data-testid="stat-value-sent"
            >
              {sourceUnavailable ? EM_DASH : sent.toLocaleString()}
            </div>
          </CardContent>
        </Card>

        {/* Failed — clickable filter anchor when onFilterClick is wired */}
        <Card
          role={onFilterClick ? "button" : undefined}
          tabIndex={onFilterClick ? 0 : undefined}
          onClick={onFilterClick ? () => onFilterClick("failed") : undefined}
          onKeyDown={
            onFilterClick
              ? (e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    onFilterClick("failed")
                  }
                }
              : undefined
          }
          className={onFilterClick ? "cursor-pointer transition-colors hover:bg-muted/40" : undefined}
          data-testid="stat-tile-failed"
        >
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Failed</CardTitle>
            <XCircle className="h-4 w-4 text-[var(--red-text)]" />
          </CardHeader>
          <CardContent>
            <div
              className={`text-2xl font-bold ${sourceUnavailable ? "text-muted-foreground" : "text-[var(--red-text)]"}`}
              data-testid="stat-value-failed"
            >
              {sourceUnavailable ? EM_DASH : failed.toLocaleString()}
            </div>
          </CardContent>
        </Card>

        {/* Failure Rate */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Failure Rate
            </CardTitle>
            <Percent className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div
              className={`text-2xl font-bold ${
                sourceUnavailable
                  ? "text-muted-foreground"
                  : Number(failureRate) > 10
                    ? "text-[var(--red-text)]"
                    : Number(failureRate) > 0
                      ? "text-[var(--amber-text)]"
                      : "text-[var(--green)]"
              }`}
              data-testid="stat-value-failure-rate"
            >
              {sourceUnavailable ? EM_DASH : `${failureRate}%`}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Per-channel breakdown */}
      {Object.keys(channels).length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-muted-foreground">By channel:</span>
          {Object.entries(channels).map(([channel, count]) => (
            <Badge key={channel} variant="secondary">
              {channel}: {count.toLocaleString()}
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}

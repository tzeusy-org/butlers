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

export function NotificationStatsBar({ stats, isLoading, onFilterClick }: NotificationStatsBarProps) {
  if (isLoading) {
    return <StatsSkeleton count={4} />
  }

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
            <div className="text-2xl font-bold">{total.toLocaleString()}</div>
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
            <div className="text-2xl font-bold text-[var(--green)]">
              {sent.toLocaleString()}
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
            <div className="text-2xl font-bold text-[var(--red-text)]">
              {failed.toLocaleString()}
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
                Number(failureRate) > 10
                  ? "text-[var(--red-text)]"
                  : Number(failureRate) > 0
                    ? "text-[var(--amber-text)]"
                    : "text-[var(--green)]"
              }`}
            >
              {failureRate}%
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

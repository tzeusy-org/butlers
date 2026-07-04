import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { StatsSkeleton } from "@/components/skeletons"
import type { NotificationStats } from "@/api/types"
import { Bell, CheckCircle, XCircle, Percent } from "lucide-react"

interface NotificationStatsBarProps {
  stats: NotificationStats | undefined
  isLoading?: boolean
}

export function NotificationStatsBar({ stats, isLoading }: NotificationStatsBarProps) {
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

        {/* Sent */}
        <Card>
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

        {/* Failed */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Failed</CardTitle>
            <XCircle className="h-4 w-4 text-[var(--red)]" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-[var(--red)]">
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
                  ? "text-[var(--red)]"
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

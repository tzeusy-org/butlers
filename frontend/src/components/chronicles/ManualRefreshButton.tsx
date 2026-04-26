import { useState } from "react"
import { RefreshCw } from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { chroniclesKeys } from "@/hooks/use-chronicles"

/**
 * Manual refresh control for historical (static) chronicles windows.
 *
 * Rendered when `timeWindow.pollingDisabled === true` (older windows where
 * auto-refresh is suppressed). Triggers a TanStack invalidation for ALL
 * chronicles queries (by-day, by-category, day-close, source-state, point
 * events) so the user can pull fresh data on demand.
 *
 * Shows a spinner while the refetch is in flight. Hidden for live/today
 * windows where auto-refresh is active.
 */
export function ManualRefreshButton() {
  const queryClient = useQueryClient()
  const [isRefreshing, setIsRefreshing] = useState(false)

  async function handleRefresh() {
    if (isRefreshing) return
    setIsRefreshing(true)
    try {
      await queryClient.invalidateQueries({ queryKey: chroniclesKeys.all })
    } finally {
      setIsRefreshing(false)
    }
  }

  return (
    <Button
      variant="outline"
      size="sm"
      className="h-8 text-xs"
      onClick={() => void handleRefresh()}
      disabled={isRefreshing}
      aria-busy={isRefreshing}
      aria-label="Refresh chronicles data"
    >
      <RefreshCw className={isRefreshing ? "animate-spin" : ""} />
      {isRefreshing ? "Refreshing…" : "Refresh"}
    </Button>
  )
}

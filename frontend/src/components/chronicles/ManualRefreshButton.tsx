/**
 * ManualRefreshButton — window-scoped cache invalidation for the Chronicles dashboard.
 *
 * Accepts a `timeWindow` prop ({ from: Date; to: Date }) and invalidates only the
 * TanStack Query cache entries that belong to that exact window, leaving cache
 * entries for other windows untouched.
 *
 * Families invalidated on click:
 *   - chroniclesKeys.byDay({ start_at, end_at })
 *   - chroniclesKeys.byCategory({ start_at, end_at })
 *   - chroniclesKeys.dayClose({ window_start, window_end })
 *   - chroniclesKeys.sourceState()            (singleton — no window params)
 *   - chroniclesKeys.pointEvents({ since, until, limit })
 *
 * The button is disabled and shows a spinner while any invalidation is in flight.
 * Visible UX: "Refresh" / "Refreshing" with a spinner (aria-busy=true while busy).
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { chroniclesKeys } from "@/hooks/use-chronicles";

export interface ManualRefreshButtonTimeWindow {
  from: Date;
  to: Date;
}

interface ManualRefreshButtonProps {
  timeWindow: ManualRefreshButtonTimeWindow;
}

export function ManualRefreshButton({ timeWindow }: ManualRefreshButtonProps) {
  const queryClient = useQueryClient();
  const [isRefreshing, setIsRefreshing] = useState(false);

  async function handleRefresh() {
    if (isRefreshing) return;
    setIsRefreshing(true);

    const windowFrom = timeWindow.from.toISOString();
    const windowTo = timeWindow.to.toISOString();

    // Derive the exact param shapes that ChroniclesPage passes to each hook.
    const aggregateParams = { start_at: windowFrom, end_at: windowTo };
    const dayCloseParams = { window_start: windowFrom, window_end: windowTo };
    const pointEventsParams = { since: windowFrom, until: windowTo, limit: 500 };

    await Promise.all([
      queryClient.invalidateQueries({ queryKey: chroniclesKeys.byDay(aggregateParams) }),
      queryClient.invalidateQueries({ queryKey: chroniclesKeys.byCategory(aggregateParams) }),
      queryClient.invalidateQueries({ queryKey: chroniclesKeys.dayClose(dayCloseParams) }),
      queryClient.invalidateQueries({ queryKey: chroniclesKeys.sourceState() }),
      queryClient.invalidateQueries({ queryKey: chroniclesKeys.pointEvents(pointEventsParams) }),
    ]);

    setIsRefreshing(false);
  }

  return (
    <Button
      variant="outline"
      size="sm"
      className="h-8 text-xs"
      disabled={isRefreshing}
      aria-busy={isRefreshing}
      onClick={() => void handleRefresh()}
    >
      {isRefreshing ? (
        <>
          <Loader2 className="animate-spin" />
          Refreshing
        </>
      ) : (
        "Refresh"
      )}
    </Button>
  );
}

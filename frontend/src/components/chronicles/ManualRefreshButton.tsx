/**
 * ManualRefreshButton — full-window cache invalidation for the Chronicles dashboard.
 *
 * Invalidates every TanStack Query cache family the drilldown panel renders for
 * the selected day, by family prefix (react-query's default `exact: false`
 * matching), so it does not need to know the exact params (trends window, tz,
 * day) each family was last fetched with — those live in ChroniclesDrilldownPanel
 * state, not in this button.
 *
 * Families invalidated on click (chroniclesFamilyKeys — all 11 day/window-scoped
 * families the drilldown panel renders; see use-chronicles.ts for the exclusions):
 *   - byDay, byCategory, dayClose, sourceState, pointEvents, episodes, balance,
 *     whoYouWereWith, correctionPrompts, trends, rollups
 *
 * The button is disabled and shows a spinner while any invalidation is in flight.
 * Visible UX: "Refresh" / "Refreshing" with a spinner (aria-busy=true while busy).
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { chroniclesFamilyKeys } from "@/hooks/use-chronicles";

export function ManualRefreshButton() {
  const queryClient = useQueryClient();
  const [isRefreshing, setIsRefreshing] = useState(false);

  async function handleRefresh() {
    if (isRefreshing) return;
    setIsRefreshing(true);

    await Promise.all(
      Object.values(chroniclesFamilyKeys).map((queryKey) =>
        queryClient.invalidateQueries({ queryKey }),
      ),
    );

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

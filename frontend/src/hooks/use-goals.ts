import { useQuery } from "@tanstack/react-query";
import { getGoal, getGoalRegions, getGoals, type GoalFilters } from "@/api/goals";

// Goal ledger reads have no fleet-bus invalidation yet; reconcile in the foreground.
const GOAL_POLL_MS = 30_000;
export function useGoals(filters: GoalFilters = {}) {
  return useQuery({ queryKey: ["goals", "list", filters],
    queryFn: ({ signal }) => getGoals(filters, signal), refetchInterval: GOAL_POLL_MS });
}
export function useGoal(id: string) {
  return useQuery({ queryKey: ["goals", "detail", id],
    queryFn: ({ signal }) => getGoal(id, signal), refetchInterval: GOAL_POLL_MS });
}
export function useGoalRegions() {
  return useQuery({ queryKey: ["goals", "regions"],
    queryFn: ({ signal }) => getGoalRegions(signal), refetchInterval: GOAL_POLL_MS });
}

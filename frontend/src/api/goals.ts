import { apiFetch } from "./client";
import type { ApiResponse, CursorPage } from "./types";

// Mirrors src/butlers/api/models/goals.py. These reads convey no authority.
export interface GoalSummary {
  id: string;
  origin: string;
  state: string;
  exposed: boolean;
  admitted_exposed: boolean;
  chain_depth: number;
  caused_by_goal: string | null;
  caused_by_effect: string | null;
  regions: string[];
  outcome: string | null;
  created_at: string;
  updated_at: string;
}
export interface GoalInvocation {
  id: string;
  parent_invocation_id: string | null;
  role: string;
  depth: number;
  status: string;
  exposed: boolean;
  exposure_cause: string | null;
  used_turns: number;
  max_turns: number;
  used_tokens: number;
  max_tokens: number;
  end_reason: string | null;
  started_at: string | null;
  ended_at: string | null;
}
export interface GoalEffect {
  id: string;
  invocation_id: string;
  capability: string;
  region: string;
  status: string;
  approval_ref: string | null;
  receipt_ref: string | null;
  delivery_ref: string | null;
  recipient_entity: string | null;
  recipient_channel: string | null;
  created_at: string;
  updated_at: string;
}
export interface GoalApprovalLineage {
  effect_id: string;
  approval_ref: string;
  effect_status: string;
  capability: string;
  caused_by_goal: string | null;
  caused_by_effect: string | null;
  caused_goals: string[];
}
export interface GoalBudget {
  max_invocations: number; reserved_invocations: number; used_invocations: number;
  max_children: number; active_children: number;
  max_calls: number; reserved_calls: number; used_calls: number;
  max_tokens: number; reserved_tokens: number; used_tokens: number;
  max_spend: string; reserved_spend: string; used_spend: string;
  deadline: string;
}
export interface GoalDetail extends GoalSummary {
  invocations: GoalInvocation[];
  effects: GoalEffect[];
  approvals: GoalApprovalLineage[];
  budget: GoalBudget | null;
  goal_outcome: {
    outcome: string; final_status: string | null; delivery_failure: string | null;
    relied_effects: string[]; reply_effect_id: string | null;
    created_at: string; resolved_at: string | null;
  } | null;
  exposure_causes: string[];
}
export interface RegionSummary {
  region: string;
  recent_goals: { id: string; state: string; origin: string; created_at: string }[];
  obligations_due: { name: string; source: string; cron: string | null; next_run_at: string }[];
}
export interface RegionSummaries {
  regions: RegionSummary[];
  sources_degraded: string[];
}
export interface GoalFilters {
  origin?: string; state?: string; exposed?: string; region?: string; cursor?: string;
}
export function getGoals(filters: GoalFilters = {}, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: "25" });
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  return apiFetch<CursorPage<GoalSummary>>(`/goals?${params}`, { signal });
}
export function getGoal(id: string, signal?: AbortSignal) {
  return apiFetch<ApiResponse<GoalDetail>>(`/goals/${encodeURIComponent(id)}`, { signal });
}
export function getGoalRegions(signal?: AbortSignal) {
  return apiFetch<ApiResponse<RegionSummaries>>("/goals/regions", { signal });
}

/** Build-time admission only. No localStorage or query-string can activate D19. */
export const D19_PREVIEW_ENABLED = import.meta.env.VITE_D19_PREVIEW === "true";
export const D19_PREFIX = "/d19";
export const goalHref = (id: string) => `${D19_PREFIX}/goals/${encodeURIComponent(id)}`;
export const regionHref = (name: string) => `${D19_PREFIX}/regions/${encodeURIComponent(name)}`;
export const approvalHref = (id: string) => `${D19_PREFIX}/approvals/${encodeURIComponent(id)}`;

export const GOAL_STATES = ["admitted", "running", "awaiting_approval", "recovering", "settling",
  "completed", "partial", "failed", "stopped", "budget_exhausted"] as const;
export const GOAL_ORIGINS = ["owner", "obligation", "third_party", "staffer"] as const;
export const TERMINAL_GOAL_STATES = new Set<string>([
  "completed", "partial", "failed", "stopped", "budget_exhausted",
]);
export function goalStateLabel(state: string): string {
  return GOAL_STATES.some((known) => known === state)
    ? state.replaceAll("_", " ") : `Unrecognized state: ${state}`;
}
export function obligationTiming(value: string, now = Date.now()): string {
  const instant = Date.parse(value);
  if (!Number.isFinite(instant)) return "Due time unavailable";
  return instant < now ? "Overdue" : instant === now ? "Due now" : "Upcoming";
}

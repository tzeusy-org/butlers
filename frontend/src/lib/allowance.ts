// ---------------------------------------------------------------------------
// Provider allowance display helpers [bu-q7vx1q.50]
//
// Pure functions shared by the Models tab "Allowance" strip and the /system
// verdict so both read the same account the same way.
// ---------------------------------------------------------------------------

import type { AllowanceAccount } from "@/api/types.ts";

/** How the reset time was learned, as the owner reads it. */
export function allowanceSourceLabel(source: AllowanceAccount["reset_source"]): string {
  if (source === "parsed") return "provider said";
  if (source === "default_window") return "estimated";
  return "unknown";
}

/** "2h 05m", "14m", "<1m"; never negative. */
export function formatAllowanceCountdown(msRemaining: number): string {
  const totalMinutes = Math.max(0, Math.ceil(msRemaining / 60_000));
  if (totalMinutes < 1) return "<1m";
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  if (days >= 1) return `${days}d ${hours}h`;
  if (hours >= 1) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  return `${minutes}m`;
}

export type AllowanceReading =
  | { kind: "available" }
  | { kind: "unknown" }
  | { kind: "exhausted-no-reset" }
  | { kind: "should-be-back" }
  | { kind: "countdown"; msRemaining: number; label: string };

/** Classify one account at `nowMs`. */
export function readAllowance(account: AllowanceAccount, nowMs: number): AllowanceReading {
  if (account.state === "available") return { kind: "available" };
  if (account.state === "unknown") return { kind: "unknown" };
  const resetMs = account.reset_at ? new Date(account.reset_at).getTime() : Number.NaN;
  if (Number.isNaN(resetMs)) return { kind: "exhausted-no-reset" };
  if (resetMs <= nowMs) return { kind: "should-be-back" };
  return {
    kind: "countdown",
    msRemaining: resetMs - nowMs,
    label: allowanceSourceLabel(account.reset_source),
  };
}

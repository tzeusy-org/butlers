import type { TickResponse } from "@/api/types";

/**
 * Success-toast text for a forced scheduler tick that ran. The API's typed `dispatched` count
 * wins; without one, the reply's own message, then the surface's fallback copy.
 */
export function ranTickToast(name: string, tick: TickResponse, fallback: string): string {
  if (typeof tick.dispatched === "number") {
    const noun = tick.dispatched === 1 ? "job" : "jobs";
    return `${name}: tick ran, ${tick.dispatched} ${noun} dispatched`;
  }
  return tick.message ? `${name}: ${tick.message}` : `${name}: ${fallback}`;
}

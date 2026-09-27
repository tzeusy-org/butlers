import type { GoogleHealthConnectorState } from "@/api/types";
import type { DispatchState } from "@/components/ui/StateDot";

/** Backend account health adapted to the shared operational role, never a token map. */
export function googleHealthState(state: GoogleHealthConnectorState): DispatchState {
  switch (state) {
    case "healthy":
      return "ok";
    case "degraded":
      return "degraded";
    case "error":
      return "error";
    case "not_configured":
      return "waiting";
  }
}

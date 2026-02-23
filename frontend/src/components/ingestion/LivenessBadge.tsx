/**
 * Liveness badge for connector health state.
 */

import { Badge } from "@/components/ui/badge";

const LIVENESS_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  online: "default",
  stale: "outline",
  offline: "destructive",
};

const STATE_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  healthy: "secondary",
  degraded: "outline",
  error: "destructive",
};

interface LivenessBadgeProps {
  liveness: string;
  state?: string;
  showState?: boolean;
}

export function LivenessBadge({ liveness, state, showState = false }: LivenessBadgeProps) {
  return (
    <div className="flex gap-1 flex-wrap">
      <Badge variant={LIVENESS_VARIANT[liveness] ?? "outline"}>
        {liveness}
      </Badge>
      {showState && state && (
        <Badge variant={STATE_VARIANT[state] ?? "outline"}>
          {state}
        </Badge>
      )}
    </div>
  );
}

/**
 * Quick health badge row for connector liveness (Overview tab bottom strip).
 */

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { ConnectorSummary } from "@/api/index.ts";

const BADGE_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  online: "default",
  stale: "outline",
  offline: "destructive",
};

interface ConnectorHealthRowProps {
  connectors: ConnectorSummary[];
  isLoading: boolean;
}

export function ConnectorHealthRow({ connectors, isLoading }: ConnectorHealthRowProps) {
  if (isLoading) {
    return (
      <div className="flex flex-wrap gap-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-6 w-32" />
        ))}
      </div>
    );
  }

  if (connectors.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">No connectors registered.</p>
    );
  }

  return (
    <div className="flex flex-wrap gap-2">
      {connectors.map((c) => {
        const label = `${c.connector_type}:${c.endpoint_identity}`;
        return (
          <div key={label} className="flex items-center gap-1.5">
            <span className="text-xs text-muted-foreground font-mono">{label}</span>
            <Badge variant={BADGE_VARIANT[c.liveness] ?? "outline"} className="text-xs">
              {c.liveness}
            </Badge>
          </div>
        );
      })}
    </div>
  );
}

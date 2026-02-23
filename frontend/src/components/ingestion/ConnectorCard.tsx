/**
 * Connector card for the Connectors tab grid.
 *
 * Shows: type icon label, endpoint identity, liveness badge, health state,
 * today's ingestion count, last heartbeat age, and backfill-active indicator.
 * Clicking the card navigates to /ingestion/connectors/:type/:identity.
 */

import { useNavigate } from "react-router";
import { formatDistanceToNow } from "date-fns";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { LivenessBadge } from "./LivenessBadge";
import type { ConnectorSummary } from "@/api/index.ts";

interface ConnectorCardProps {
  connector: ConnectorSummary;
  hasActiveBackfill?: boolean;
}

export function ConnectorCard({
  connector,
  hasActiveBackfill = false,
}: ConnectorCardProps) {
  const navigate = useNavigate();

  function handleClick() {
    navigate(
      `/ingestion/connectors/${encodeURIComponent(connector.connector_type)}/${encodeURIComponent(connector.endpoint_identity)}`,
    );
  }

  const todayIngested = connector.today?.messages_ingested ?? 0;
  const uptimePct = connector.today?.uptime_pct;

  const lastSeen = connector.last_heartbeat_at
    ? formatDistanceToNow(new Date(connector.last_heartbeat_at), {
        addSuffix: true,
      })
    : "never";

  return (
    <Card
      className="cursor-pointer transition-shadow hover:shadow-md"
      onClick={handleClick}
      data-testid="connector-card"
    >
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="text-sm font-semibold">
              {connector.connector_type}
            </CardTitle>
            <CardDescription className="font-mono text-xs">
              {connector.endpoint_identity}
            </CardDescription>
          </div>
          <div className="flex flex-col gap-1 items-end">
            <LivenessBadge
              liveness={connector.liveness}
              state={connector.state}
              showState
            />
            {hasActiveBackfill && (
              <Badge variant="secondary" className="text-xs">
                backfill active
              </Badge>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          <dt className="text-muted-foreground">Today ingested</dt>
          <dd className="text-right font-medium tabular-nums">
            {todayIngested.toLocaleString()}
          </dd>

          {uptimePct != null && (
            <>
              <dt className="text-muted-foreground">Uptime</dt>
              <dd className="text-right tabular-nums">{uptimePct.toFixed(1)}%</dd>
            </>
          )}

          <dt className="text-muted-foreground">Last seen</dt>
          <dd className="text-right text-xs text-muted-foreground">{lastSeen}</dd>
        </dl>
      </CardContent>
    </Card>
  );
}

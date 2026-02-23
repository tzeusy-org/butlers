/**
 * Connector detail page: /ingestion/connectors/:connectorType/:endpointIdentity
 *
 * Shows:
 * - Full connector metadata (liveness, state, version, uptime, counters)
 * - Time-series statistics chart with period selector
 * - Checkpoint info
 * - Back navigation to /ingestion?tab=connectors
 */

import { useParams, useSearchParams, Link } from "react-router";
import { formatDistanceToNow } from "date-fns";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableRow,
} from "@/components/ui/table";

import { LivenessBadge } from "@/components/ingestion/LivenessBadge";
import { VolumeTrendChart } from "@/components/ingestion/VolumeTrendChart";
import { useConnectorDetail, useConnectorStats } from "@/hooks/use-ingestion";
import type { IngestionPeriod } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// ConnectorDetailPage
// ---------------------------------------------------------------------------

export default function ConnectorDetailPage() {
  const { connectorType, endpointIdentity } = useParams<{
    connectorType: string;
    endpointIdentity: string;
  }>();

  const [searchParams, setSearchParams] = useSearchParams();
  const periodParam = searchParams.get("period") as IngestionPeriod | null;
  const period: IngestionPeriod =
    periodParam === "7d" || periodParam === "30d" ? periodParam : "24h";

  function handlePeriodChange(p: IngestionPeriod) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("period", p);
        return next;
      },
      { replace: true },
    );
  }

  const { data: detailResp, isLoading: detailLoading } = useConnectorDetail(
    connectorType ?? null,
    endpointIdentity ?? null,
  );

  const { data: statsResp, isLoading: statsLoading } = useConnectorStats(
    connectorType ?? null,
    endpointIdentity ?? null,
    period,
  );

  const connector = detailResp?.data;
  const stats = statsResp?.data;

  const lastSeen = connector?.last_heartbeat_at
    ? formatDistanceToNow(new Date(connector.last_heartbeat_at), {
        addSuffix: true,
      })
    : "never";

  const firstSeen = connector?.first_seen_at
    ? new Date(connector.first_seen_at).toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
      })
    : "—";

  return (
    <div className="space-y-6">
      {/* Back navigation */}
      <div>
        <Button variant="ghost" size="sm" asChild>
          <Link to="/ingestion?tab=connectors">
            <ArrowLeft className="mr-1 h-4 w-4" />
            Back to Connectors
          </Link>
        </Button>
      </div>

      {/* Header */}
      <div>
        {detailLoading ? (
          <>
            <Skeleton className="h-7 w-48 mb-1" />
            <Skeleton className="h-4 w-64" />
          </>
        ) : (
          <>
            <h1 className="text-2xl font-bold tracking-tight">
              {connector?.connector_type ?? connectorType}
            </h1>
            <p className="text-sm text-muted-foreground font-mono mt-1">
              {connector?.endpoint_identity ?? endpointIdentity}
            </p>
          </>
        )}
      </div>

      {/* Metadata card */}
      <Card>
        <CardHeader>
          <CardTitle>Status</CardTitle>
          {connector?.version && (
            <CardDescription>Version {connector.version}</CardDescription>
          )}
        </CardHeader>
        <CardContent>
          {detailLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : connector ? (
            <div className="space-y-4">
              <div className="flex flex-wrap gap-4 items-center">
                <LivenessBadge
                  liveness={connector.liveness}
                  state={connector.state}
                  showState
                />
                {connector.today?.uptime_pct != null && (
                  <span className="text-sm text-muted-foreground">
                    Uptime: {connector.today.uptime_pct.toFixed(1)}%
                  </span>
                )}
              </div>

              {connector.error_message && (
                <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {connector.error_message}
                </div>
              )}

              <Table>
                <TableBody>
                  <TableRow>
                    <TableCell className="text-muted-foreground">Last seen</TableCell>
                    <TableCell>{lastSeen}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="text-muted-foreground">First seen</TableCell>
                    <TableCell>{firstSeen}</TableCell>
                  </TableRow>
                  {connector.registered_via && (
                    <TableRow>
                      <TableCell className="text-muted-foreground">Registered via</TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {connector.registered_via}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  )}
                  {connector.checkpoint?.cursor && (
                    <TableRow>
                      <TableCell className="text-muted-foreground">
                        Checkpoint cursor
                      </TableCell>
                      <TableCell className="font-mono text-xs truncate max-w-xs">
                        {connector.checkpoint.cursor}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Connector not found.</p>
          )}
        </CardContent>
      </Card>

      {/* Counters card */}
      {connector?.counters && (
        <Card>
          <CardHeader>
            <CardTitle>Lifetime Counters</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
              {[
                {
                  label: "Ingested",
                  value: connector.counters.messages_ingested,
                },
                {
                  label: "Failed",
                  value: connector.counters.messages_failed,
                },
                {
                  label: "API calls",
                  value: connector.counters.source_api_calls,
                },
                {
                  label: "Dedupe accepted",
                  value: connector.counters.dedupe_accepted,
                },
                {
                  label: "Checkpoint saves",
                  value: connector.counters.checkpoint_saves,
                },
              ].map(({ label, value }) => (
                <div key={label} className="space-y-1">
                  <p className="text-xs text-muted-foreground">{label}</p>
                  <p className="text-lg font-bold tabular-nums">
                    {value.toLocaleString()}
                  </p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Stats summary card */}
      {stats?.summary && (
        <Card>
          <CardHeader>
            <CardTitle>Period Summary</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              {[
                {
                  label: "Ingested",
                  value: stats.summary.messages_ingested.toLocaleString(),
                },
                {
                  label: "Failed",
                  value: stats.summary.messages_failed.toLocaleString(),
                },
                {
                  label: "Error rate",
                  value: `${stats.summary.error_rate_pct.toFixed(1)}%`,
                },
                {
                  label: "Avg/hour",
                  value: stats.summary.avg_messages_per_hour.toFixed(1),
                },
              ].map(({ label, value }) => (
                <div key={label} className="space-y-1">
                  <p className="text-xs text-muted-foreground">{label}</p>
                  <p className="text-lg font-bold tabular-nums">{value}</p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Volume timeseries chart */}
      <VolumeTrendChart
        data={stats?.timeseries ?? []}
        period={period}
        onPeriodChange={handlePeriodChange}
        isLoading={statsLoading}
        title="Volume Trend"
      />
    </div>
  );
}

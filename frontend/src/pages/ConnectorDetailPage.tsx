/**
 * Connector detail page: /ingestion/connectors/:connectorType/:endpointIdentity
 *
 * Shows:
 * - Full connector metadata (liveness, state, version, uptime, counters)
 * - Time-series statistics chart with period selector
 * - Checkpoint info with inline cursor editing
 * - Back navigation to /ingestion?tab=connectors
 */

import { useState } from "react";
import { useParams, useSearchParams, Link } from "react-router";
import { formatDistanceToNow } from "date-fns";
import { ArrowLeft, Info, Pencil } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableRow,
} from "@/components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import { LivenessBadge } from "@/components/ingestion/LivenessBadge";
import { VolumeTrendChart } from "@/components/ingestion/VolumeTrendChart";
import { ConnectorFiltersDialog } from "@/components/ingestion/ConnectorFiltersDialog";
import {
  useConnectorDetail,
  useConnectorStats,
  useUpdateConnectorCursor,
} from "@/hooks/use-ingestion";
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

  // Cursor edit state
  const [isEditingCursor, setIsEditingCursor] = useState(false);
  const [cursorDraft, setCursorDraft] = useState("");
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);

  const cursorMutation = useUpdateConnectorCursor(
    connectorType ?? "",
    endpointIdentity ?? "",
  );

  function handleEditClick() {
    setCursorDraft(connector?.checkpoint?.cursor ?? "");
    setIsEditingCursor(true);
  }

  function handleCancelEdit() {
    setIsEditingCursor(false);
    setCursorDraft("");
  }

  function handleSaveClick() {
    if (!cursorDraft.trim()) return;
    setShowConfirmDialog(true);
  }

  function handleConfirmSave() {
    setShowConfirmDialog(false);
    cursorMutation.mutate(cursorDraft.trim(), {
      onSuccess: () => {
        setIsEditingCursor(false);
        setCursorDraft("");
      },
    });
  }

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
    : "\u2014";

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
      <div className="flex items-start justify-between gap-4">
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
        {connectorType && endpointIdentity && (
          <div className="flex-shrink-0 pt-1">
            <ConnectorFiltersDialog
              connectorType={connectorType}
              endpointIdentity={endpointIdentity}
              triggerVariant="page"
            />
          </div>
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
                  <TableRow>
                    <TableCell className="text-muted-foreground">
                      <div className="flex items-center gap-1">
                        Checkpoint cursor
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Info className="h-3.5 w-3.5 text-muted-foreground/60" />
                            </TooltipTrigger>
                            <TooltipContent side="right" className="max-w-xs">
                              Takes effect on next connector restart
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      </div>
                    </TableCell>
                    <TableCell>
                      {isEditingCursor ? (
                        <div className="flex items-center gap-2">
                          <Input
                            value={cursorDraft}
                            onChange={(e) => setCursorDraft(e.target.value)}
                            className="font-mono text-xs h-8 max-w-sm"
                            autoFocus
                            data-testid="cursor-edit-input"
                          />
                          <Button
                            size="sm"
                            variant="default"
                            onClick={handleSaveClick}
                            disabled={!cursorDraft.trim() || cursorMutation.isPending}
                            data-testid="cursor-save-btn"
                          >
                            {cursorMutation.isPending ? "Saving..." : "Save"}
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={handleCancelEdit}
                            disabled={cursorMutation.isPending}
                            data-testid="cursor-cancel-btn"
                          >
                            Cancel
                          </Button>
                        </div>
                      ) : (
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs truncate max-w-xs">
                            {connector.checkpoint?.cursor ?? "\u2014"}
                          </span>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-6 w-6 p-0"
                            onClick={handleEditClick}
                            data-testid="cursor-edit-btn"
                          >
                            <Pencil className="h-3.5 w-3.5" />
                            <span className="sr-only">Edit cursor</span>
                          </Button>
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
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

      {/* Confirmation dialog for cursor change */}
      <AlertDialog open={showConfirmDialog} onOpenChange={setShowConfirmDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Change checkpoint cursor?</AlertDialogTitle>
            <AlertDialogDescription>
              Changing the cursor affects which messages are ingested on the
              next connector restart. Continue?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmSave}>
              Continue
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

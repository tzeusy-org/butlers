/**
 * Connector detail page: /ingestion/connectors/:connectorType/:endpointIdentity
 *
 * Shows:
 * - Full connector metadata (liveness, state, version, uptime, counters)
 * - Time-series statistics chart with period selector
 * - Checkpoint info with inline cursor editing
 * - Connector-scoped ingestion rules (block rules)
 * - Back navigation to /ingestion?tab=connectors
 */

import { useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { Time } from "@/components/ui/time";
import { Info, Pencil } from "lucide-react";

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
import { Page } from "@/components/ui/page";
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
import { ConnectorRulesSection } from "@/components/ingestion/ConnectorRulesSection";
import {
  BatchSettingsCard,
  BATCH_CONNECTOR_TYPES,
} from "@/components/ingestion/BatchSettingsCard";
import {
  useConnectorDetail,
  useConnectorStats,
  useUpdateConnectorCursor,
  useUpdateConnectorSettings,
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

  const { data: detailResp, isLoading: detailLoading, error: detailError } = useConnectorDetail(
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

  const settingsMutation = useUpdateConnectorSettings(
    connectorType ?? "",
    endpointIdentity ?? "",
  );

  // Discretion settings state
  const discretionSettings = (connector?.settings as Record<string, unknown> | null)?.discretion as
    | Record<string, unknown>
    | undefined;
  const currentWeightBypass = (discretionSettings?.weight_bypass as number) ?? 1.0;
  const currentWeightFailOpen = (discretionSettings?.weight_fail_open as number) ?? 0.5;
  const [weightBypassDraft, setWeightBypassDraft] = useState<string | null>(null);
  const [weightFailOpenDraft, setWeightFailOpenDraft] = useState<string | null>(null);

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

  // Derive record fields for <Page>
  const title = connector?.connector_type ?? connectorType ?? "Connector";

  const breadcrumbs = useMemo(
    () => [
      { label: "Ingestion", href: "/ingestion" },
      { label: "Connectors", href: "/ingestion?tab=connectors" },
      { label: title },
    ],
    [title],
  );

  return (
    <Page
      archetype="detail"
      title={title}
      description={connector?.endpoint_identity ?? endpointIdentity}
      breadcrumbs={breadcrumbs}
      loading={detailLoading}
      error={detailError ?? null}
    >
      <>
        {/* Primary: status + config */}

        {/* Metadata card */}
        <Card>
          <CardHeader>
            <CardTitle>Status</CardTitle>
            {connector?.version && (
              <CardDescription>Version {connector.version}</CardDescription>
            )}
          </CardHeader>
          <CardContent>
            {connector ? (
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
                      <TableCell>
                        {connector.last_heartbeat_at
                          ? <Time value={connector.last_heartbeat_at} mode="relative" />
                          : "never"}
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="text-muted-foreground">First seen</TableCell>
                      <TableCell>
                        {connector.first_seen_at
                          ? <Time value={connector.first_seen_at} mode="absolute" precision="day" />
                          : "—"}
                      </TableCell>
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

        {/* Discretion settings card */}
        {connector && (
          <Card>
            <CardHeader>
              <CardTitle>Discretion Settings</CardTitle>
              <CardDescription>
                LLM-based filter thresholds. Changes take effect on next connector restart.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <label className="text-sm font-medium">
                      Weight Bypass Threshold
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Info className="inline ml-1 h-3.5 w-3.5 text-muted-foreground/60" />
                          </TooltipTrigger>
                          <TooltipContent side="right" className="max-w-xs">
                            Senders with weight at or above this value bypass the discretion LLM
                            entirely (always FORWARD). Default 1.0 = only owner bypasses.
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </label>
                    <div className="flex items-center gap-2">
                      <Input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        value={weightBypassDraft ?? currentWeightBypass}
                        onChange={(e) => setWeightBypassDraft(e.target.value)}
                        className="w-24 font-mono text-sm"
                        data-testid="weight-bypass-input"
                      />
                      {weightBypassDraft !== null &&
                        parseFloat(weightBypassDraft) !== currentWeightBypass && (
                          <Button
                            size="sm"
                            onClick={() => {
                              const val = parseFloat(weightBypassDraft);
                              if (!isNaN(val)) {
                                settingsMutation.mutate({
                                  discretion: {
                                    ...discretionSettings,
                                    weight_bypass: val,
                                  },
                                });
                                setWeightBypassDraft(null);
                              }
                            }}
                            disabled={settingsMutation.isPending}
                          >
                            {settingsMutation.isPending ? "Saving..." : "Save"}
                          </Button>
                        )}
                    </div>
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">
                      Fail-Open Threshold
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Info className="inline ml-1 h-3.5 w-3.5 text-muted-foreground/60" />
                          </TooltipTrigger>
                          <TooltipContent side="right" className="max-w-xs">
                            Senders below this weight fail-closed (LLM errors = IGNORE). Above =
                            fail-open (LLM errors = FORWARD). Default 0.5.
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </label>
                    <div className="flex items-center gap-2">
                      <Input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        value={weightFailOpenDraft ?? currentWeightFailOpen}
                        onChange={(e) => setWeightFailOpenDraft(e.target.value)}
                        className="w-24 font-mono text-sm"
                        data-testid="weight-fail-open-input"
                      />
                      {weightFailOpenDraft !== null &&
                        parseFloat(weightFailOpenDraft) !== currentWeightFailOpen && (
                          <Button
                            size="sm"
                            onClick={() => {
                              const val = parseFloat(weightFailOpenDraft);
                              if (!isNaN(val)) {
                                settingsMutation.mutate({
                                  discretion: {
                                    ...discretionSettings,
                                    weight_fail_open: val,
                                  },
                                });
                                setWeightFailOpenDraft(null);
                              }
                            }}
                            disabled={settingsMutation.isPending}
                          >
                            {settingsMutation.isPending ? "Saving..." : "Save"}
                          </Button>
                        )}
                    </div>
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">
                  Weight tiers: Owner=1.0, Family/Close-friends=0.9, Known=0.7, Unknown=0.3
                </p>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Batch settings card — only for batch connector types */}
        {connector && BATCH_CONNECTOR_TYPES.has(connector.connector_type) && (
          <BatchSettingsCard
            connector={connector}
            settingsMutation={settingsMutation}
          />
        )}

        {/* Supporting: ingest stats + volume chart + rules */}

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

        {/* Connector-scoped ingestion rules */}
        {connectorType && endpointIdentity && (
          <ConnectorRulesSection
            connectorType={connectorType}
            endpointIdentity={endpointIdentity}
          />
        )}

        {/* Practical: dangerous ops — checkpoint cursor edit */}
        {connector && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Checkpoint Cursor</CardTitle>
              <CardDescription>
                Takes effect on next connector restart.
              </CardDescription>
            </CardHeader>
            <CardContent>
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
                    {connector.checkpoint?.cursor ?? "—"}
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
            </CardContent>
          </Card>
        )}

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
      </>
    </Page>
  );
}

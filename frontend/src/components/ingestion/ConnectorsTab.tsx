/**
 * Connectors tab content for the /ingestion page.
 *
 * Sections (spec §5.2):
 * - Cross-connector summary bar
 * - Connector card grid (with backfill-active indicators)
 * - Volume time-series chart (per-period)
 * - Fanout distribution table
 * - Error log panel
 *
 * Shared query-key strategy: reuses connector list/summary/fanout warm cache
 * from Overview tab without forcing fresh loads.
 */

import { useSearchParams } from "react-router";

import { ConnectorSummaryBar } from "./ConnectorSummaryBar";
import { ConnectorCard } from "./ConnectorCard";
import { VolumeTrendChart } from "./VolumeTrendChart";
import { FanoutMatrix } from "./FanoutMatrix";
import { ConnectorErrorLog } from "./ConnectorErrorLog";
import { Skeleton } from "@/components/ui/skeleton";
import { PeriodSelector } from "./PeriodSelector";
import {
  useConnectorSummaries,
  useCrossConnectorSummary,
  useConnectorFanout,
  useIngestionVolume,
} from "@/hooks/use-ingestion";
import { useBackfillJobs } from "@/hooks/use-backfill";
import type { IngestionPeriod } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// ConnectorsTab
// ---------------------------------------------------------------------------

interface ConnectorsTabProps {
  isActive: boolean;
}

export function ConnectorsTab({ isActive }: ConnectorsTabProps) {
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

  const { data: connectorsResp, isLoading: connectorsLoading } =
    useConnectorSummaries({ enabled: isActive });

  const { data: summaryResp, isLoading: summaryLoading } =
    useCrossConnectorSummary(period, { enabled: isActive });

  const fanoutPeriod: IngestionPeriod = period === "24h" ? "7d" : period;
  const { data: fanoutResp, isLoading: fanoutLoading } = useConnectorFanout(
    fanoutPeriod,
    { enabled: isActive },
  );

  // Track which connectors have an active backfill
  const { data: backfillResp } = useBackfillJobs({ status: "active" });
  const activeBackfills = backfillResp?.data ?? [];
  const activeBackfillKeys = new Set(
    activeBackfills.map(
      (j) => `${j.connector_type}:${j.endpoint_identity}`,
    ),
  );

  const connectors = connectorsResp?.data ?? [];

  // Derive online/stale/offline counts from client-side liveness (same logic
  // as the LivenessBadge on each card) so the summary bar matches the cards.
  // The backend summary counts by DB `state` (healthy/degraded/error) which
  // uses different semantics from liveness.
  const correctedSummary = summaryResp?.data
    ? {
        ...summaryResp.data,
        connectors_online: connectors.filter((c) => c.liveness === "online").length,
        connectors_stale: connectors.filter((c) => c.liveness === "stale").length,
        connectors_offline: connectors.filter((c) => c.liveness === "offline").length,
      }
    : undefined;

  // Aggregate volume timeseries across all connectors (DB-backed)
  const { data: volumeResp, isLoading: volumeLoading } = useIngestionVolume(
    period,
    { enabled: isActive },
  );

  const timeseries = volumeResp?.data?.timeseries ?? [];

  return (
    <div className="space-y-6">
      {/* Period selector + summary bar */}
      <div className="flex items-center justify-between">
        <ConnectorSummaryBar
          summary={correctedSummary}
          isLoading={summaryLoading || connectorsLoading}
        />
        <PeriodSelector value={period} onChange={handlePeriodChange} />
      </div>

      {/* Connector cards grid */}
      {connectorsLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-40 w-full" />
          ))}
        </div>
      ) : connectors.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No connectors registered.
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {connectors.map((c) => {
            const key = `${c.connector_type}:${c.endpoint_identity}`;
            return (
              <ConnectorCard
                key={key}
                connector={c}
                hasActiveBackfill={activeBackfillKeys.has(key)}
              />
            );
          })}
        </div>
      )}

      {/* Volume time series chart */}
      <VolumeTrendChart
        data={timeseries}
        period={period}
        onPeriodChange={handlePeriodChange}
        isLoading={volumeLoading && isActive}
        title="Ingestion Volume"
      />

      {/* Fanout distribution table */}
      <FanoutMatrix fanout={fanoutResp?.data} isLoading={fanoutLoading} />

      {/* Error log */}
      <ConnectorErrorLog connectors={connectors} isLoading={connectorsLoading} />
    </div>
  );
}

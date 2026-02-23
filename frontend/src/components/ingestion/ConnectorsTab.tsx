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
  useConnectorStats,
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
  const firstConnector = connectors[0] ?? null;

  // Volume timeseries: use first connector as representative (or show empty)
  const { data: statsResp, isLoading: statsLoading } = useConnectorStats(
    firstConnector?.connector_type ?? null,
    firstConnector?.endpoint_identity ?? null,
    period,
    { enabled: isActive && !!firstConnector },
  );

  const timeseries = statsResp?.data?.timeseries ?? [];

  return (
    <div className="space-y-6">
      {/* Period selector + summary bar */}
      <div className="flex items-center justify-between">
        <ConnectorSummaryBar
          summary={summaryResp?.data}
          isLoading={summaryLoading}
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
        isLoading={statsLoading && isActive}
        title="Ingestion Volume by Connector"
      />

      {/* Fanout distribution table */}
      <FanoutMatrix fanout={fanoutResp?.data} isLoading={fanoutLoading} />

      {/* Error log */}
      <ConnectorErrorLog connectors={connectors} isLoading={connectorsLoading} />
    </div>
  );
}

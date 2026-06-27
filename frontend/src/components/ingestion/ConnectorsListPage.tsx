/**
 * ConnectorsListPage — extracted list component for /ingestion/connectors.
 *
 * Sections (spec §3.4, §3.5):
 * - Cross-connector summary bar
 * - Connector card grid (with backfill-active indicators)
 * - Dormant/available connectors section (§3.5) with "connect →" deep-link to /secrets
 * - Volume time-series chart (per-period)
 * - Fanout distribution table
 * - Error log panel
 *
 * NOTE: useConnectorDetail MUST NOT be mounted from this list view.
 * Only summary-level data is shown here (per spec §6.2 "no useConnectorDetail on roster").
 * Detail data loads only on the connector detail page.
 *
 * The "connect →" link deep-links to /secrets?focus=u:<provider>, where the
 * DirectionPassport credential page for that provider lives.
 *
 * Extracted from ConnectorsTab.tsx for use on the first-class /ingestion/connectors
 * sub-route. ConnectorsTab.tsx retains its isActive-gated version for the legacy
 * /ingestion?tab=connectors tab-param mount (backward-compatible until full migration).
 *
 * Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
 *       connector-base-spec/spec.md §"Dashboard Connector Page"
 *       ingestion-ui-information-architecture/spec.md §"Connector roster list summary-only polling"
 *       tasks.md §3.4, §3.5
 */

import { Link } from "react-router";
import { useSearchParams } from "react-router";

import { ConnectorSummaryBar } from "./ConnectorSummaryBar";
import { ConnectorCard } from "./ConnectorCard";
import { VolumeTrendChart } from "./VolumeTrendChart";
import { FanoutMatrix } from "./FanoutMatrix";
import { ConnectorErrorLog } from "./ConnectorErrorLog";
import { Skeleton } from "@/components/ui/skeleton";
import { PeriodSelector } from "./PeriodSelector";
import {
  useAvailableConnectors,
  useConnectorSummaries,
  useCrossConnectorSummary,
  useConnectorFanout,
  useIngestionVolume,
  usePipelineStats,
} from "@/hooks/use-ingestion";
import { useBackfillJobs } from "@/hooks/use-backfill";
import type { IngestionPeriod } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// ConnectorsListPage
// ---------------------------------------------------------------------------

export function ConnectorsListPage() {
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
    useConnectorSummaries();

  const { data: summaryResp, isLoading: summaryLoading } =
    useCrossConnectorSummary(period);

  const fanoutPeriod: IngestionPeriod = period === "24h" ? "7d" : period;
  const { data: fanoutResp, isLoading: fanoutLoading } =
    useConnectorFanout(fanoutPeriod);

  // Track which connectors have an active backfill
  const { data: backfillResp } = useBackfillJobs({ status: "active" });
  const activeBackfills = backfillResp?.data ?? [];
  const activeBackfillKeys = new Set(
    activeBackfills.map((j) => `${j.connector_type}:${j.endpoint_identity}`),
  );

  const connectors = connectorsResp?.data ?? [];

  // Available (dormant) connectors: catalog entries not yet registered.
  // Filters out connector types that already have at least one registered instance.
  const { data: availableResp } = useAvailableConnectors();
  const registeredTypes = new Set(connectors.map((c) => c.connector_type));
  const dormantConnectors = (availableResp?.data ?? []).filter(
    (p) => !registeredTypes.has(p.connector_type),
  );

  // Pipeline stats for aggregates_available flag
  const { data: pipelineStats } = usePipelineStats("24h");
  const aggregatesAvailable = pipelineStats?.aggregates_available !== false;

  // Aggregate volume timeseries across all connectors (DB-backed)
  const { data: volumeResp, isLoading: volumeLoading } =
    useIngestionVolume(period);

  const timeseries = volumeResp?.data?.timeseries ?? [];

  return (
    <div className="space-y-6">
      {/* Metrics unavailable eyebrow — shown when Prometheus is unreachable */}
      {!aggregatesAvailable && (
        <div className="rounded-md border border-yellow-200 bg-yellow-50 px-4 py-2 text-sm text-yellow-800 dark:border-yellow-800 dark:bg-yellow-950 dark:text-yellow-200">
          metrics unavailable: aggregate statistics are temporarily unavailable
        </div>
      )}

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
        <p className="text-sm text-muted-foreground">No connectors registered.</p>
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

      {/* Dormant / available connectors section (§3.5)
          Shown when catalog profiles exist that are not yet registered.
          Each card includes a "connect →" deep-link to /secrets?focus=u:<provider>
          where the DirectionPassport credential page for that provider lives. */}
      {dormantConnectors.length > 0 && (
        <div data-testid="dormant-available-section">
          <h3 className="text-sm font-semibold text-muted-foreground mb-3">
            Available: not yet configured
          </h3>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {dormantConnectors.map((profile) => (
              <div
                key={profile.connector_type}
                data-testid={`dormant-connector-${profile.connector_type}`}
                className="rounded-md border border-dashed p-4 flex flex-col gap-1 opacity-60"
              >
                <p className="text-sm font-medium">{profile.display_name}</p>
                <p className="text-xs text-muted-foreground capitalize">
                  {profile.channel}
                </p>
                {profile.supports_backfill && (
                  <p className="text-xs text-muted-foreground">Supports backfill</p>
                )}
                <Link
                  to="/secrets"
                  className="mt-2 text-xs text-primary hover:underline self-start"
                  data-testid={`dormant-connect-link-${profile.connector_type}`}
                >
                  connect →
                </Link>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Volume time series chart */}
      <VolumeTrendChart
        data={timeseries}
        period={period}
        onPeriodChange={handlePeriodChange}
        isLoading={volumeLoading}
        title="Ingestion Volume"
      />

      {/* Fanout distribution table */}
      <FanoutMatrix fanout={fanoutResp?.data} isLoading={fanoutLoading} />

      {/* Error log */}
      <ConnectorErrorLog connectors={connectors} isLoading={connectorsLoading} />
    </div>
  );
}

/**
 * Overview tab content for the /ingestion page.
 *
 * Sections (spec §5.1):
 * - Aggregate stat row (total ingested, skipped, error rate, active connectors)
 * - Volume trend chart with 24h/7d/30d toggle
 * - Tier breakdown donut
 * - Fanout matrix (connector x butler)
 * - Health badge row
 */

import { useSearchParams } from "react-router";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { IngestionStatRow } from "./IngestionStatRow";
import { VolumeTrendChart } from "./VolumeTrendChart";
import { TierBreakdownDonut } from "./TierBreakdownDonut";
import { FanoutMatrix } from "./FanoutMatrix";
import { ConnectorHealthRow } from "./ConnectorHealthRow";
import {
  useConnectorSummaries,
  useCrossConnectorSummary,
  useIngestionOverview,
  useConnectorFanout,
  useConnectorStats,
} from "@/hooks/use-ingestion";
import type { IngestionPeriod } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// OverviewTab
// ---------------------------------------------------------------------------

interface OverviewTabProps {
  isActive: boolean;
}

export function OverviewTab({ isActive }: OverviewTabProps) {
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

  // Cross-connector summary: used for TierBreakdownDonut fallback and ConnectorHealthRow context
  const { data: summaryResp, isLoading: summaryLoading } =
    useCrossConnectorSummary(period, { enabled: isActive });

  // Ingestion overview: period-scoped stats from message_inbox for the stat row
  const { data: overviewResp, isLoading: overviewLoading } =
    useIngestionOverview(period, { enabled: isActive });

  // Fanout uses 7d/30d only; fall back to 7d when period is 24h
  const fanoutPeriod: IngestionPeriod = period === "24h" ? "7d" : period;
  const { data: fanoutResp, isLoading: fanoutLoading } = useConnectorFanout(
    fanoutPeriod,
    { enabled: isActive },
  );

  // Volume trend: aggregate over all connectors — use the first connector's
  // stats as a representative timeseries when only one connector is present,
  // or build a synthetic aggregate from the summary.
  // The API doesn't provide an aggregate timeseries endpoint, so we use the
  // summary per_connector array to build a synthetic total when available.
  // For a proper aggregate, we'd need a dedicated endpoint. For now we use
  // the summary data to populate the chart with per-bucket approximations.
  const connectors = connectorsResp?.data ?? [];
  const firstConnector = connectors[0] ?? null;

  const { data: statsResp, isLoading: statsLoading } = useConnectorStats(
    firstConnector?.connector_type ?? null,
    firstConnector?.endpoint_identity ?? null,
    period,
    { enabled: isActive && !!firstConnector },
  );

  const timeseries = statsResp?.data?.timeseries ?? [];
  const summary = summaryResp?.data;
  const overview = overviewResp?.data;

  return (
    <div className="space-y-6">
      {/* Aggregate stat row — uses period-scoped message_inbox counts */}
      <IngestionStatRow overview={overview} isLoading={overviewLoading} />

      {/* Volume trend + Tier breakdown */}
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <VolumeTrendChart
            data={timeseries}
            period={period}
            onPeriodChange={handlePeriodChange}
            isLoading={statsLoading && isActive}
            title={
              connectors.length > 1 && firstConnector
                ? `Volume Trend (${firstConnector.connector_type})`
                : "Volume Trend"
            }
          />
        </div>
        <div>
          <TierBreakdownDonut summary={summary} overview={overview} isLoading={summaryLoading || overviewLoading} />
        </div>
      </div>

      {/* Fanout matrix */}
      <FanoutMatrix fanout={fanoutResp?.data} isLoading={fanoutLoading} />

      {/* Health badge row */}
      <Card>
        <CardHeader>
          <CardTitle>Connector Health</CardTitle>
        </CardHeader>
        <CardContent>
          <ConnectorHealthRow
            connectors={connectors}
            isLoading={connectorsLoading}
          />
        </CardContent>
      </Card>
    </div>
  );
}

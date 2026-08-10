// ---------------------------------------------------------------------------
// Ingestion verdict openers — JARVIS pursuit move 9, slice 4 (bu-vyjoi)
//
// Each opener composes data already used by its corresponding ingestion route
// into DispatchVerdict's loading / degraded / calm contract. The clauses only
// carry links when there is an existing drill-down route; source-health notes
// stay explicit text rather than pretending a same-page URL is a remedy.
// ---------------------------------------------------------------------------

import { DispatchVerdict, type VerdictClause } from "@/components/ui/dispatch-verdict";
import { useConnectorSummaries, useConnectorSummariesWithAggregates, usePipelineStats } from "@/hooks/use-ingestion";
import { useIngestionWindowRollup } from "@/hooks/use-ingestion-events";
import { formatCostUsd } from "@/lib/format-cost";
import { deriveConnectorDispatchInfo } from "@/components/ingestion/connectors/connector-auth";
import type { ConnectorSummary, PipelineStats } from "@/api/types";
import type { IngestionRange } from "@/components/ingestion/TimelineTab";
import { useMemo } from "react";

function plural(count: number, singular: string, pluralWord = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralWord}`;
}

function connectorLabel(connector: ConnectorSummary): string {
  const kind = connector.connector_type.replace(/_/g, " ");
  return connector.endpoint_identity && connector.endpoint_identity !== "default"
    ? `${kind} · ${connector.endpoint_identity}`
    : kind;
}

function connectorHref(connector: ConnectorSummary): string {
  return `/ingestion/connectors/${encodeURIComponent(connector.connector_type)}/${encodeURIComponent(connector.endpoint_identity)}`;
}

function attentionClauses(connectors: ConnectorSummary[]): VerdictClause[] {
  return connectors
    .filter(
      (connector) =>
        deriveConnectorDispatchInfo(connector).needsAttention ||
        Boolean(connector.operational_warnings?.length),
    )
    .slice(0, 3)
    .map((connector) => ({
      key: `connector-${connector.connector_type}-${connector.endpoint_identity}`,
      text: `${connectorLabel(connector)} needs attention`,
      href: connectorHref(connector),
    }));
}

function timelineWindow(range: IngestionRange): { from: string; to: string } {
  const to = new Date();
  const hours = range === "1h" ? 1 : range === "7d" ? 7 * 24 : 24;
  const from = new Date(to.getTime() - hours * 60 * 60 * 1000);
  return { from: from.toISOString(), to: to.toISOString() };
}

function timelineAllClear(
  range: IngestionRange,
  rollup:
    | {
        events: number;
        sessions: number;
        cost: number | null;
        unpriced_session_count?: number;
        no_usage_session_count?: number;
      }
    | undefined,
): string {
  if (!rollup) return "Ingestion window ready";

  const facts = [
    `${plural(rollup.events, "signal")} received in the last ${range}`,
    `${plural(rollup.sessions, "session")} dispatched`,
    rollup.cost != null ? `${formatCostUsd(rollup.cost)} attributed` : null,
  ].filter((fact): fact is string => Boolean(fact));

  return facts.join("; ");
}

function timelineCostCoverageClauses(
  rollup:
    | {
        events: number;
        sessions: number;
        cost: number | null;
        unpriced_session_count?: number;
        no_usage_session_count?: number;
      }
    | undefined,
): VerdictClause[] {
  const unpriced = rollup?.unpriced_session_count ?? 0;
  const noUsage = rollup?.no_usage_session_count ?? 0;
  if (unpriced === 0 && noUsage === 0) return [];

  return [
    ...(unpriced > 0
      ? [
          {
            key: "unpriced-session-cost",
            text: `${plural(unpriced, "session")} cost unavailable`,
          },
        ]
      : []),
    ...(noUsage > 0
      ? [
          {
            key: "no-usage-session-cost",
            text: `${plural(noUsage, "session")} recorded no token usage`,
          },
        ]
      : []),
  ];
}

/** Verdict above the timeline ledger. Its rollup follows the visible range. */
export function IngestionTimelineVerdictOpener({ range }: { range: IngestionRange }) {
  const window = useMemo(() => timelineWindow(range), [range]);
  const rollup = useIngestionWindowRollup(window);
  const connectors = useConnectorSummaries();
  const connectorRows = connectors.data?.data ?? [];

  return (
    <DispatchVerdict
      testId="ingestion-timeline"
      landmarkLabel="Ingestion timeline verdict"
      sources={[
        { label: "ingestion rollup", isLoading: rollup.isLoading, isError: rollup.isError },
        {
          label: "connector registry",
          isLoading: connectors.isLoading,
          isError:
            connectors.isError || connectors.data?.meta?.connector_registry_available === false,
        },
      ]}
      clauses={[
        ...timelineCostCoverageClauses(rollup.data),
        ...attentionClauses(connectorRows),
      ]}
      allClear={timelineAllClear(range, rollup.data)}
      className="border-b border-border/60 pb-3"
    />
  );
}

function connectorHealthClauses(
  connectors: ConnectorSummary[],
  hourlyEventsAvailable: boolean,
  deviceLivenessAvailable: boolean,
  owntracksCadenceAvailable: boolean,
): VerdictClause[] {
  const clauses = attentionClauses(connectors);

  if (!hourlyEventsAvailable) {
    clauses.unshift({
      key: "hourly-events-unavailable",
      text: "24h connector activity unavailable",
    });
  }
  if (!deviceLivenessAvailable) {
    clauses.unshift({
      key: "device-liveness-unavailable",
      text: "connector device liveness unavailable",
    });
  }
  if (!owntracksCadenceAvailable) {
    clauses.unshift({
      key: "owntracks-cadence-unavailable",
      text: "OwnTracks cadence unavailable",
    });
  }

  return clauses;
}

/** Verdict above the connectors roster. */
export function IngestionConnectorsVerdictOpener() {
  const connectors = useConnectorSummariesWithAggregates();
  const response = connectors.data?.data;
  const rows = response?.connectors ?? [];
  const activeRows = rows.filter((connector) => !connector.archived);
  const healthy = activeRows.filter(
    (connector) => !deriveConnectorDispatchInfo(connector).needsAttention,
  ).length;

  return (
    <DispatchVerdict
      testId="ingestion-connectors"
      landmarkLabel="Ingestion connectors verdict"
      sources={[
        {
          label: "connector registry",
          isLoading: connectors.isLoading,
          isError:
            connectors.isError || response?.connector_registry_available === false,
        },
      ]}
      clauses={connectorHealthClauses(
        activeRows,
        response?.hourly_events_available !== false,
        response?.device_liveness_available !== false,
        response?.owntracks_cadence_available !== false,
      )}
      allClear={`${plural(healthy, "connector")} healthy`}
      className="border-b border-border/60 pb-3"
    />
  );
}

function filtersClauses(stats: PipelineStats | undefined): VerdictClause[] {
  if (!stats || !stats.aggregates_available) return [];

  const total = stats.ingested + stats.filtered;
  if (stats.filtered <= 0 || total <= 0) return [];

  const droppedPercent = Math.round((stats.filtered / total) * 100);
  return [
    {
      key: "signals-filtered",
      text: `gates filtered ${droppedPercent}% of ${total.toLocaleString()} signals`,
    },
  ];
}

/** Verdict above the filters pipeline. */
export function IngestionFiltersVerdictOpener() {
  const stats = usePipelineStats("24h");
  const total = stats.data ? stats.data.ingested + stats.data.filtered : 0;

  return (
    <DispatchVerdict
      testId="ingestion-filters"
      landmarkLabel="Ingestion filters verdict"
      sources={[
        {
          label: "pipeline metrics",
          isLoading: stats.isLoading,
          isError: stats.isError || stats.data?.aggregates_available === false,
        },
      ]}
      clauses={filtersClauses(stats.data)}
      allClear={`All gates clear: ${total.toLocaleString()} signals evaluated in the last 24h`}
      className="border-b border-border/60 pb-3"
    />
  );
}

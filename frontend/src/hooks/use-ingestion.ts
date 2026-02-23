/**
 * TanStack Query hooks for the /ingestion page analytics.
 *
 * Shared query-key strategy (spec §7):
 * - ingestionKeys.connectorsList()            → list of ConnectorSummary
 * - ingestionKeys.connectorsSummary(period)   → CrossConnectorSummary
 * - ingestionKeys.fanout(period)              → ConnectorFanout matrix
 * - ingestionKeys.connectorDetail(type, id, period) → ConnectorDetail + stats
 * - ingestionKeys.connectorStats(type, id, period)  → ConnectorStats timeseries
 *
 * Overview and Connectors tabs share the connectors list / summary / fanout
 * keys so switching tabs reuses warm cache.
 */

import { useQuery } from "@tanstack/react-query";

import {
  getCrossConnectorSummary,
  getConnectorDetail,
  getConnectorFanout,
  getConnectorStats,
  listConnectorSummaries,
} from "@/api/index.ts";
import type { IngestionPeriod } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const ingestionKeys = {
  all: ["ingestion"] as const,
  connectorsList: () => [...ingestionKeys.all, "connectors-list"] as const,
  connectorsSummary: (period: IngestionPeriod) =>
    [...ingestionKeys.all, "connectors-summary", period] as const,
  fanout: (period: IngestionPeriod) =>
    [...ingestionKeys.all, "fanout", period] as const,
  connectorDetail: (connectorType: string, endpointIdentity: string) =>
    [...ingestionKeys.all, "connector-detail", connectorType, endpointIdentity] as const,
  connectorStats: (
    connectorType: string,
    endpointIdentity: string,
    period: IngestionPeriod,
  ) =>
    [
      ...ingestionKeys.all,
      "connector-stats",
      connectorType,
      endpointIdentity,
      period,
    ] as const,
};

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

/**
 * List all connector summaries (shared between Overview and Connectors tabs).
 */
export function useConnectorSummaries(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ingestionKeys.connectorsList(),
    queryFn: () => listConnectorSummaries(),
    refetchInterval: 60_000,
    enabled: options?.enabled !== false,
  });
}

/**
 * Cross-connector aggregate summary. Lazy-loaded per active tab.
 */
export function useCrossConnectorSummary(
  period: IngestionPeriod,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionKeys.connectorsSummary(period),
    queryFn: () => getCrossConnectorSummary(period),
    refetchInterval: 60_000,
    enabled: options?.enabled !== false,
  });
}

/**
 * Fanout distribution matrix (connector x butler message counts).
 * Shared between Overview and Connectors tabs.
 */
export function useConnectorFanout(
  period: IngestionPeriod,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionKeys.fanout(period),
    queryFn: () => getConnectorFanout(period),
    refetchInterval: 120_000,
    enabled: options?.enabled !== false,
  });
}

/**
 * Full detail for a single connector (used in detail page).
 */
export function useConnectorDetail(
  connectorType: string | null,
  endpointIdentity: string | null,
) {
  return useQuery({
    queryKey: ingestionKeys.connectorDetail(
      connectorType ?? "",
      endpointIdentity ?? "",
    ),
    queryFn: () => getConnectorDetail(connectorType!, endpointIdentity!),
    enabled: !!connectorType && !!endpointIdentity,
    refetchInterval: 30_000,
  });
}

/**
 * Time-series stats for a single connector.
 */
export function useConnectorStats(
  connectorType: string | null,
  endpointIdentity: string | null,
  period: IngestionPeriod,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionKeys.connectorStats(
      connectorType ?? "",
      endpointIdentity ?? "",
      period,
    ),
    queryFn: () => getConnectorStats(connectorType!, endpointIdentity!, period),
    enabled:
      !!connectorType && !!endpointIdentity && options?.enabled !== false,
    refetchInterval: 60_000,
  });
}

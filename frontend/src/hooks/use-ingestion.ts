/**
 * TanStack Query hooks for the /ingestion page analytics.
 *
 * Shared query-key strategy (spec §7):
 * - ingestionKeys.connectorsList()            → list of ConnectorSummary
 * - ingestionKeys.connectorDetail(type, id)           → ConnectorDetail
 * - ingestionKeys.connectorStats(type, id, period)  → ConnectorStats timeseries
 * - ingestionKeys.connectorSummariesWithAggregates()  → ConnectorSummariesResponse
 * - ingestionKeys.crossSummaryWithAggregates()        → ConnectorCrossSummaryResponse
 * - ingestionKeys.pipelineStats(window)               → PipelineStats
 *
 * Overview and Connectors tabs share the connectors list key so switching
 * tabs reuses warm cache.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  archiveConnector,
  getCrossConnectorSummaryWithAggregates,
  getConnectorDetail,
  getConnectorEvents,
  getConnectorIncidents,
  getConnectorRoutingRules,
  getConnectorStats,
  getConnectorSummariesWithAggregates,
  getPipelineStats,
  listAvailableConnectors,
  listConnectorSummaries,
  updateConnectorCursor,
  updateConnectorSettings,
} from "@/api/index.ts";
import type { IngestionPeriod } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const ingestionKeys = {
  all: ["ingestion"] as const,
  connectorsList: () => [...ingestionKeys.all, "connectors-list"] as const,
  connectorsAvailable: () => [...ingestionKeys.all, "connectors-available"] as const,
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
  connectorSummariesWithAggregates: () =>
    [...ingestionKeys.all, "connectors-summaries-with-aggregates"] as const,
  crossSummaryWithAggregates: () =>
    [...ingestionKeys.all, "cross-summary-with-aggregates"] as const,
  pipelineStats: (window: string) =>
    [...ingestionKeys.all, "pipeline-stats", window] as const,
  connectorEvents: (connectorType: string, endpointIdentity: string, limit: number) =>
    [
      ...ingestionKeys.all,
      "connector-events",
      connectorType,
      endpointIdentity,
      limit,
    ] as const,
  connectorIncidents: (connectorType: string, endpointIdentity: string, limit: number) =>
    [
      ...ingestionKeys.all,
      "connector-incidents",
      connectorType,
      endpointIdentity,
      limit,
    ] as const,
  connectorRoutingRules: (connectorType: string, endpointIdentity: string) =>
    [
      ...ingestionKeys.all,
      "connector-routing-rules",
      connectorType,
      endpointIdentity,
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

/**
 * Mutation to update a connector's checkpoint cursor.
 * Invalidates the connector-detail query on success so the UI refreshes.
 */
export function useUpdateConnectorCursor(
  connectorType: string,
  endpointIdentity: string,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (cursor: string) =>
      updateConnectorCursor(connectorType, endpointIdentity, cursor),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ingestionKeys.connectorDetail(connectorType, endpointIdentity),
      });
    },
  });
}

/**
 * Mutation to update connector settings (shallow merge).
 * Invalidates the connector detail so the page refreshes.
 */
export function useUpdateConnectorSettings(
  connectorType: string,
  endpointIdentity: string,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (settings: Record<string, unknown>) =>
      updateConnectorSettings(connectorType, endpointIdentity, settings),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ingestionKeys.connectorDetail(connectorType, endpointIdentity),
      });
    },
  });
}

/**
 * Mutation to soft-archive a connector identity (bu-u19yv one-click archive from
 * the review queue; reuses the audit-logged archive endpoint, no new mechanics).
 *
 * On success, invalidates the summaries + cross-summary queries so the archived
 * identity moves from the active roster (and its review-queue candidate row)
 * into the collapsed "archived" section and drops out of the fleet-health
 * rollups — no manual refetch needed.
 */
export function useArchiveConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      connectorType,
      endpointIdentity,
    }: {
      connectorType: string;
      endpointIdentity: string;
    }) => archiveConnector(connectorType, endpointIdentity),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ingestionKeys.connectorSummariesWithAggregates(),
      });
      queryClient.invalidateQueries({
        queryKey: ingestionKeys.crossSummaryWithAggregates(),
      });
    },
  });
}

/**
 * Fetch the catalog of available connector profiles (§3.5 discovery endpoint).
 *
 * Returns connector types the framework can deploy, regardless of whether
 * any instance is currently registered in connector_registry.
 * Response is safe to cache for 60s.
 */
export function useAvailableConnectors(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ingestionKeys.connectorsAvailable(),
    queryFn: () => listAvailableConnectors(),
    staleTime: 60_000,
    gcTime: 120_000,
    enabled: options?.enabled !== false,
  });
}

/**
 * Connector list (all fields DB-sourced; no aggregates_available flag).
 * Uses the /api/ingestion/connectors/summaries endpoint.
 */
export function useConnectorSummariesWithAggregates(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ingestionKeys.connectorSummariesWithAggregates(),
    queryFn: () => getConnectorSummariesWithAggregates(),
    refetchInterval: 60_000,
    enabled: options?.enabled !== false,
  });
}

/**
 * Cross-connector aggregate summary with aggregates_available flag.
 * Uses the new /api/ingestion/connectors/cross-summary endpoint.
 */
export function useCrossConnectorSummaryWithAggregates(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ingestionKeys.crossSummaryWithAggregates(),
    queryFn: () => getCrossConnectorSummaryWithAggregates(),
    refetchInterval: 60_000,
    enabled: options?.enabled !== false,
  });
}

/**
 * Pipeline funnel statistics from Prometheus (60s TTL cache on the backend).
 * aggregates_available=false means Prometheus is unreachable — show "metrics unavailable" eyebrow.
 */
export function usePipelineStats(
  window: "1h" | "24h" | "7d" = "24h",
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionKeys.pipelineStats(window),
    queryFn: () => getPipelineStats(window),
    refetchInterval: 60_000,
    enabled: options?.enabled !== false,
  });
}

/**
 * Recent events for a single connector (left zone, below histogram).
 * Polls every 60s; disabled when connectorType or endpointIdentity is null.
 * [bu-5ywn2]
 */
export function useConnectorEvents(
  connectorType: string | null,
  endpointIdentity: string | null,
  limit = 20,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionKeys.connectorEvents(
      connectorType ?? "",
      endpointIdentity ?? "",
      limit,
    ),
    queryFn: () => getConnectorEvents(connectorType!, endpointIdentity!, limit),
    enabled:
      !!connectorType && !!endpointIdentity && options?.enabled !== false,
    refetchInterval: 60_000,
  });
}

/**
 * Incident events (failures, errors) for a single connector (left zone).
 * Polls every 60s; disabled when connectorType or endpointIdentity is null.
 * [bu-5ywn2]
 */
export function useConnectorIncidents(
  connectorType: string | null,
  endpointIdentity: string | null,
  limit = 10,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionKeys.connectorIncidents(
      connectorType ?? "",
      endpointIdentity ?? "",
      limit,
    ),
    queryFn: () => getConnectorIncidents(connectorType!, endpointIdentity!, limit),
    enabled:
      !!connectorType && !!endpointIdentity && options?.enabled !== false,
    refetchInterval: 60_000,
  });
}

/**
 * Routing rules scoped to a single connector (right zone).
 * Polls every 120s; rules change infrequently.
 * Disabled when connectorType or endpointIdentity is null.
 * [bu-5ywn2]
 */
export function useConnectorRoutingRules(
  connectorType: string | null,
  endpointIdentity: string | null,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionKeys.connectorRoutingRules(
      connectorType ?? "",
      endpointIdentity ?? "",
    ),
    queryFn: () => getConnectorRoutingRules(connectorType!, endpointIdentity!),
    enabled:
      !!connectorType && !!endpointIdentity && options?.enabled !== false,
    refetchInterval: 120_000,
  });
}

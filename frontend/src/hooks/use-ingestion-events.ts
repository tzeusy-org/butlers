/**
 * TanStack Query hooks for the ingestion event lineage Timeline tab.
 *
 * Query key strategy:
 * - ingestionEventKeys.list(filters)          → paginated IngestionEventSummary list
 * - ingestionEventKeys.sessions(requestId)     → sessions for a given request_id
 * - ingestionEventKeys.rollup(requestId)       → cost/token rollup for a request_id
 * - ingestionEventKeys.replays(requestId)      → replay history from public.audit_log
 * - ingestionEventKeys.senderContact(requestId) → resolved contact name for sender_identity
 *
 * Stale time of 30s matches the spec for Timeline tab data freshness.
 */

import { useQuery } from "@tanstack/react-query";

import {
  listIngestionEvents,
  getIngestionEventSessions,
  getIngestionEventRollup,
  getIngestionEventReplays,
  getIngestionEventSenderContact,
} from "@/api/index.ts";
import type { IngestionEventsParams } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const ingestionEventKeys = {
  all: ["ingestion", "events"] as const,
  list: (filters: IngestionEventsParams) =>
    [...ingestionEventKeys.all, "list", filters] as const,
  sessions: (requestId: string) =>
    [...ingestionEventKeys.all, requestId, "sessions"] as const,
  rollup: (requestId: string) =>
    [...ingestionEventKeys.all, requestId, "rollup"] as const,
  replays: (requestId: string) =>
    [...ingestionEventKeys.all, requestId, "replays"] as const,
  senderContact: (requestId: string) =>
    [...ingestionEventKeys.all, requestId, "sender-contact"] as const,
};

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

/**
 * Paginated list of ingestion events, newest first.
 *
 * Fetches from GET /api/ingestion/events.
 * Supports optional source_channel filter, limit, and offset.
 */
export function useIngestionEvents(
  filters: IngestionEventsParams = {},
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionEventKeys.list(filters),
    queryFn: () => listIngestionEvents(filters),
    staleTime: 30_000,
    enabled: options?.enabled !== false,
  });
}

/**
 * Fan-out sessions for a single ingestion event request_id.
 *
 * Fetches from GET /api/ingestion/events/{requestId}/sessions.
 * Only enabled when a non-empty requestId is provided.
 */
export function useIngestionEventSessions(
  requestId: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionEventKeys.sessions(requestId),
    queryFn: () => getIngestionEventSessions(requestId),
    staleTime: 30_000,
    enabled: !!requestId && options?.enabled !== false,
  });
}

/**
 * Cost/token rollup for a single ingestion event request_id.
 *
 * Fetches from GET /api/ingestion/events/{requestId}/rollup.
 * Only enabled when a non-empty requestId is provided.
 */
export function useIngestionEventRollup(
  requestId: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionEventKeys.rollup(requestId),
    queryFn: () => getIngestionEventRollup(requestId),
    staleTime: 30_000,
    enabled: !!requestId && options?.enabled !== false,
  });
}

/**
 * Parallel fetch of sessions + rollup for a single ingestion event.
 *
 * Both queries share the same requestId and run concurrently.
 * Only fires when requestId is non-empty.
 */
export function useIngestionEventLineage(
  requestId: string,
  options?: { enabled?: boolean },
) {
  const enabled = !!requestId && options?.enabled !== false;
  const sessions = useIngestionEventSessions(requestId, { enabled });
  const rollup = useIngestionEventRollup(requestId, { enabled });
  return { sessions, rollup };
}

/**
 * Replay attempt history for a single ingestion event.
 *
 * Fetches from GET /api/ingestion/events/{requestId}/replays.
 * Only enabled when a non-empty requestId is provided.
 */
export function useIngestionEventReplays(
  requestId: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionEventKeys.replays(requestId),
    queryFn: () => getIngestionEventReplays(requestId),
    staleTime: 30_000,
    enabled: !!requestId && options?.enabled !== false,
  });
}

/**
 * Resolved contact name for the sender_identity of an ingestion event.
 *
 * Fetches from GET /api/ingestion/events/{requestId}/sender-contact.
 * Returns resolved=false on miss — always 200 from the backend.
 * Only enabled when a non-empty requestId is provided.
 */
export function useIngestionEventSenderContact(
  requestId: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionEventKeys.senderContact(requestId),
    queryFn: () => getIngestionEventSenderContact(requestId),
    staleTime: 60_000,
    enabled: !!requestId && options?.enabled !== false,
  });
}

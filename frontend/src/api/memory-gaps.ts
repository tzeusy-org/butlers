/**
 * Owner knowledge gaps: the questions no butler could answer yet (bu-q7vx1q.73).
 *
 * Types live here, not in types.ts: that file is pinned by open contract-evolution
 * manifests, so this slice adds its own module instead of editing it (same
 * precedent as home-safety-watch.ts). Envelopes come from types.ts as type-only imports.
 */

import { apiFetch } from "@/api/client.ts";
import type { ApiResponse, KeysetResponse } from "@/api/types.ts";

export type MemoryGapStatus = "open" | "answerable" | "delivered" | "dismissed" | "expired";

/** What the server knows about whether anyone looked for the answer. */
export type MemoryGapCoverageState = "unknown" | "absent_proven" | "unavailable" | "present";

/** One thread the question was asked in. */
export interface MemoryGapOrigin {
  conversation_id: string;
  request_id: string | null;
  channel: string;
  asked_at: string;
  delivered_at: string | null;
}

export interface MemoryGap {
  gap_id: string;
  butler: string;
  entity_id: string;
  entity_name: string | null;
  predicate: string;
  question: string;
  status: MemoryGapStatus;
  coverage_state: MemoryGapCoverageState;
  asked_at: string;
  expires_at: string;
  answered_by_ref: string | null;
  delivery_failed: boolean;
  last_error: string | null;
  origins: MemoryGapOrigin[];
}

export interface MemoryGapDismissal {
  gap_id: string;
  status: MemoryGapStatus;
}

/** Open questions per predicate: how many gaps, and how many threads asked. */
export interface MemoryGapDemandRow {
  predicate: string;
  open_gaps: number;
  asks: number;
}

/** Demand envelope: a pool that failed is named, never read as zero demand. */
export interface MemoryGapDemandResponse {
  data: MemoryGapDemandRow[];
  meta: { sources_degraded?: string[] };
}

export interface MemoryGapParams {
  status?: MemoryGapStatus;
  limit?: number;
  cursor?: string | null;
  signal?: AbortSignal;
}

function gapSearch(params: MemoryGapParams | undefined): string {
  const search = new URLSearchParams();
  if (params?.status) search.set("status", params.status);
  if (params?.limit != null) search.set("limit", String(params.limit));
  if (params?.cursor) search.set("cursor", params.cursor);
  return search.toString();
}

/** GET /api/memory/gaps: open plus answerable gaps by default, newest first, keyset paged. */
export function getMemoryGaps(params?: MemoryGapParams): Promise<KeysetResponse<MemoryGap>> {
  const qs = gapSearch(params);
  return apiFetch<KeysetResponse<MemoryGap>>(qs ? `/memory/gaps?${qs}` : "/memory/gaps", {
    signal: params?.signal,
  });
}

/** GET /api/memory/gaps/demand: per-predicate demand over open gaps, most asked first. */
export function getMemoryGapDemand(signal?: AbortSignal): Promise<MemoryGapDemandResponse> {
  return apiFetch<MemoryGapDemandResponse>("/memory/gaps/demand", { signal });
}

/** POST /api/memory/gaps/{id}/dismiss: terminal and idempotent; returns the resulting status. */
export function dismissMemoryGap(gapId: string): Promise<ApiResponse<MemoryGapDismissal>> {
  return apiFetch<ApiResponse<MemoryGapDismissal>>(
    `/memory/gaps/${encodeURIComponent(gapId)}/dismiss`,
    { method: "POST" },
  );
}

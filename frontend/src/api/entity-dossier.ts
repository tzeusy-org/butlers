/**
 * Entity graph dossier: GET /api/entities/{id}/dossier (bu-8cdl1.8, REQ-entity-graph-010).
 *
 * Types live here, not in types.ts: that file is pinned by an open contract-evolution
 * manifest, so this slice adds its own module instead of editing it.
 */

import { apiFetch } from "@/api/client.ts";
import type { ApiResponse } from "@/api/types.ts";

export interface EntityDossierReceipt {
  edge_id: string;
  source_id: string;
  predicate: string;
  /** "subject": this entity -> counterpart; "object": counterpart -> this entity. */
  direction: "subject" | "object";
  counterpart_entity_id: string;
  counterpart_name: string | null;
  sensitivity: string;
  updated_at: string;
}

export interface EntityDossierSource {
  source_schema: string;
  source_table: string;
  live_count: number;
  receipts: EntityDossierReceipt[];
}

export interface EntityDossier {
  entity_id: string;
  relationships_known: number;
  /** A bare count: a withheld relationship's predicate, object and source are never sent. */
  relationships_withheld: number;
  sources: EntityDossierSource[];
  receipts_limit: number;
  receipts_truncated: boolean;
}

export function getEntityDossier(entityId: string): Promise<ApiResponse<EntityDossier>> {
  return apiFetch<ApiResponse<EntityDossier>>(
    `/entities/${encodeURIComponent(entityId)}/dossier`,
  );
}

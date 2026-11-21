/**
 * The owner's critical-entity registry (home.critical_entities, RFC 0028 Amendment 1): entities
 * whose Home Assistant calls always wait for the owner's own approval. Marking one runs nothing.
 *
 * Types live here, not in types.ts: that file is pinned by an open contract-evolution manifest.
 */

import { apiFetch } from "@/api/client.ts";

export interface HomeCriticalEntity {
  entity_id: string;
  owner_label: string;
  added_by: string;
  added_at: string | null;
}

export interface HomeCriticalEntityInput {
  entity_id: string;
  owner_label: string;
}

export function getCriticalEntities(): Promise<HomeCriticalEntity[]> {
  return apiFetch<HomeCriticalEntity[]>("/home/critical-entities");
}

export function putCriticalEntity(input: HomeCriticalEntityInput): Promise<HomeCriticalEntity> {
  return apiFetch<HomeCriticalEntity>("/home/critical-entities", {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function deleteCriticalEntity(entityId: string): Promise<void> {
  return apiFetch<void>(`/home/critical-entities/${encodeURIComponent(entityId)}`, {
    method: "DELETE",
  });
}

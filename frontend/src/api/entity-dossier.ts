/** REQ-entity-graph-010: generated wire types preserve content-blind withheld counts. */
import { apiFetch } from "./client";
import type { components } from "./schema.gen";
export type EntityDossier = components["schemas"]["EntityDossier"];

export function getEntityDossier(entityId: string) {
  return apiFetch("/entities/{entity_id}/dossier", { path: { entity_id: String(entityId) } });
}

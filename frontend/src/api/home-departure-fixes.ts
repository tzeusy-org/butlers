/**
 * Owner-declared departure fixes (home.departure_fixes): the one exact call offered for approval
 * when a watched item is not secure as the owner leaves. Declaring one runs nothing.
 *
 * Types live here, not in types.ts: that file is pinned by an open contract-evolution manifest.
 */

import { apiFetch } from "@/api/client.ts";

export interface HomeDepartureWatchItem {
  entity_id: string;
  owner_label: string;
  secure_states: string[];
  updated_at: string | null;
}

export interface HomeDepartureFix {
  entity_id: string;
  domain: string;
  service: string;
  target_entity_id: string;
  data: Record<string, unknown>;
  risk: "safe" | "reversible" | "consequential" | "protected";
  updated_at: string | null;
}

export interface HomeDepartureFixInput {
  entity_id: string;
  domain: string;
  service: string;
  target_entity_id: string;
  data: Record<string, never>;
}

export function getDepartureWatch(): Promise<HomeDepartureWatchItem[]> {
  return apiFetch<HomeDepartureWatchItem[]>("/home/departure-watch");
}

export function getDepartureFixes(): Promise<HomeDepartureFix[]> {
  return apiFetch<HomeDepartureFix[]>("/home/departure-fixes");
}

export function putDepartureFix(input: HomeDepartureFixInput): Promise<HomeDepartureFix> {
  return apiFetch<HomeDepartureFix>("/home/departure-fixes", {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function deleteDepartureFix(entityId: string): Promise<void> {
  return apiFetch<void>(`/home/departure-fixes/${encodeURIComponent(entityId)}`, {
    method: "DELETE",
  });
}

/**
 * Owner-declared life-safety sensors (home.safety_watch).
 *
 * Types live here, not in types.ts: that file is pinned by an open contract-evolution
 * manifest, so this slice adds its own module instead of editing it.
 */

import { apiFetch } from "@/api/client.ts";

export type HomeSafetyRole = "leak" | "smoke" | "co" | "gas";

export interface HomeSafetyWatchEntry {
  entity_id: string;
  role: HomeSafetyRole;
  owner_label: string;
  updated_at: string | null;
}

export interface HomeSafetyWatchInput {
  entity_id: string;
  role: HomeSafetyRole;
  owner_label: string;
}

export function getHomeSafetyWatch(): Promise<HomeSafetyWatchEntry[]> {
  return apiFetch<HomeSafetyWatchEntry[]>("/home/safety-watch");
}

export function putHomeSafetyWatch(input: HomeSafetyWatchInput): Promise<HomeSafetyWatchEntry> {
  return apiFetch<HomeSafetyWatchEntry>("/home/safety-watch", {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function deleteHomeSafetyWatch(entityId: string, role: HomeSafetyRole): Promise<void> {
  return apiFetch<void>(
    `/home/safety-watch/${encodeURIComponent(entityId)}/${encodeURIComponent(role)}`,
    { method: "DELETE" },
  );
}

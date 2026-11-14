/**
 * Owner-declared away-safety values (home.temperature_watch, the departure-watch away
 * duration and the blind dwell). Every value is the owner's own: nothing here has a default.
 *
 * Types live here, not in types.ts: that file is pinned by an open contract-evolution
 * manifest, so this slice adds its own module instead of editing it.
 */

import { apiFetch } from "@/api/client.ts";

export type FreezerUnit = "°C" | "°F";

export interface HomeFreezerEntry {
  entity_id: string;
  owner_label: string;
  unit: FreezerUnit;
  max_c: number;
  hysteresis_c: number;
  dwell_minutes: number;
  max_reading_age_minutes: number;
  updated_at: string | null;
}

export type HomeFreezerInput = Omit<HomeFreezerEntry, "updated_at">;

export interface HomeAwayDoorEntry {
  entity_id: string;
  owner_label: string;
  away_alert_after_minutes: number | null;
}

export interface HomeAwaySafetyStatus {
  freezer: "ok" | "unconfigured";
  away_door: "ok" | "unconfigured";
  blind: "ok" | "unconfigured";
  sensing_group: "registered" | "unconfigured" | "unknown";
}

export interface HomeAwaySafetyConfig {
  freezers: HomeFreezerEntry[];
  doors: HomeAwayDoorEntry[];
  blind_after_minutes: number | null;
  status: HomeAwaySafetyStatus;
}

export function getHomeAwaySafety(): Promise<HomeAwaySafetyConfig> {
  return apiFetch<HomeAwaySafetyConfig>("/home/away-safety");
}

export function putHomeFreezer(input: HomeFreezerInput): Promise<HomeFreezerEntry> {
  return apiFetch<HomeFreezerEntry>("/home/away-safety/freezers", {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function deleteHomeFreezer(entityId: string): Promise<void> {
  return apiFetch<void>(`/home/away-safety/freezers/${encodeURIComponent(entityId)}`, {
    method: "DELETE",
  });
}

export function putHomeAwayDoor(entityId: string, minutes: number): Promise<HomeAwayDoorEntry> {
  return apiFetch<HomeAwayDoorEntry>(`/home/away-safety/doors/${encodeURIComponent(entityId)}`, {
    method: "PUT",
    body: JSON.stringify({ away_alert_after_minutes: minutes }),
  });
}

export function deleteHomeAwayDoor(entityId: string): Promise<void> {
  return apiFetch<void>(`/home/away-safety/doors/${encodeURIComponent(entityId)}`, {
    method: "DELETE",
  });
}

export function putHomeBlindDwell(minutes: number): Promise<{ blind_after_minutes: number }> {
  return apiFetch<{ blind_after_minutes: number }>("/home/away-safety/blind", {
    method: "PUT",
    body: JSON.stringify({ blind_after_minutes: minutes }),
  });
}

export function deleteHomeBlindDwell(): Promise<void> {
  return apiFetch<void>("/home/away-safety/blind", { method: "DELETE" });
}

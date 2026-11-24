/**
 * The owner place registry (public.owner_places), edited from the Home page.
 *
 * Owner-only: coordinates, Wi-Fi network names and Home Assistant zones are private and
 * reach only this editor. Types live here, not in types.ts: that file is pinned by an open
 * contract-evolution manifest, so this slice adds its own module instead of editing it.
 */

import { apiFetch } from "@/api/client.ts";

export type OwnerPlaceKind = "home" | "work" | "airport" | "other";

export interface OwnerPlaceInput {
  label: string;
  kind: OwnerPlaceKind;
  lat: number;
  lon: number;
  radius_m: number;
  aliases: string[];
  ssids: string[];
  ha_zone: string | null;
  paused: boolean;
}

export interface OwnerPlace extends OwnerPlaceInput {
  id: string;
  version: number;
  updated_at: string;
}

interface Envelope<T> {
  data: T;
}

export async function getOwnerPlaces(): Promise<OwnerPlace[]> {
  return (await apiFetch<Envelope<OwnerPlace[]>>("/home/places")).data;
}

export async function createOwnerPlace(input: OwnerPlaceInput): Promise<OwnerPlace> {
  const response = await apiFetch<Envelope<OwnerPlace>>("/home/places", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.data;
}

export async function updateOwnerPlace(
  id: string,
  input: OwnerPlaceInput,
  expectedVersion: number,
): Promise<OwnerPlace> {
  const response = await apiFetch<Envelope<OwnerPlace>>(
    `/home/places/${encodeURIComponent(id)}`,
    { method: "PUT", body: JSON.stringify({ ...input, expected_version: expectedVersion }) },
  );
  return response.data;
}

export function deleteOwnerPlace(id: string, expectedVersion: number): Promise<void> {
  return apiFetch<void>(
    `/home/places/${encodeURIComponent(id)}?expected_version=${expectedVersion}`,
    { method: "DELETE" },
  );
}

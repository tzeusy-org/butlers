/**
 * Owner presence entity settings (home:presence:owner_entities), edited from the Home page.
 *
 * Owner-only: the Home Assistant entity ids that identify the owner stay in this editor.
 * Types live here, not in types.ts, for the same reason as home-places.ts.
 */

import { apiFetch } from "@/api/client.ts";

export interface PresenceOwnerEntities {
  configured: boolean;
  owner_entities: string[];
  version: number;
}

interface Envelope<T> {
  data: T;
}

const ROUTE = "/home/settings/presence/owner-entities";

export async function getPresenceOwnerEntities(): Promise<PresenceOwnerEntities> {
  return (await apiFetch<Envelope<PresenceOwnerEntities>>(ROUTE)).data;
}

export async function putPresenceOwnerEntities(
  ownerEntities: string[],
  expectedVersion: number,
): Promise<PresenceOwnerEntities> {
  const response = await apiFetch<Envelope<PresenceOwnerEntities>>(ROUTE, {
    method: "PUT",
    body: JSON.stringify({ owner_entities: ownerEntities, expected_version: expectedVersion }),
  });
  return response.data;
}

export interface PresenceTransition {
  id: string;
  direction: "leave" | "enter";
  place_ref: string | null;
  at: string;
  confirmed_at: string;
  late: boolean;
  retracted: boolean;
  retraction_event_id: string | null;
}

/** The standard dashboard list envelope (not wrapped in `data`). */
export interface PresenceTransitions {
  items: PresenceTransition[];
  next_cursor: string | null;
  limit: number;
}

/**
 * Confirmed owner leave/enter edges, newest first. 503 until the reader login is activated.
 * An empty page with a cursor means the scan cap was hit before an edge was found.
 */
export async function getPresenceTransitions(
  limit = 20,
  cursor: string | null = null,
): Promise<PresenceTransitions> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor !== null) params.set("cursor", cursor);
  return apiFetch<PresenceTransitions>(`/home/presence/transitions?${params.toString()}`);
}

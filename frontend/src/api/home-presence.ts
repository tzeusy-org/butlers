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

export interface PresenceTransitions {
  transitions: PresenceTransition[];
  next_before: string | null;
}

/** Confirmed owner leave/enter edges, newest first. 503 until the reader login is activated. */
export async function getPresenceTransitions(limit = 20): Promise<PresenceTransitions> {
  const route = `/home/presence/transitions?limit=${limit}`;
  return (await apiFetch<Envelope<PresenceTransitions>>(route)).data;
}

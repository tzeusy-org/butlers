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

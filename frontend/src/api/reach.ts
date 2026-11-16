/**
 * Owner break-through clauses ("who always reaches me"): the owner-declared senders whose
 * messages interrupt quiet hours. Backed by /api/butlers/attention/break-through-clauses.
 *
 * Granting stays in the cookie-authenticated API; this client lists and revokes. A clause names
 * matchers only (an entity, a sender domain, a channel or a security kind), never message text.
 */

import { apiFetch } from "./client.ts";
import type { ApiResponse } from "./types.ts";

export interface ReachClause {
  id: string;
  entity_id: string | null;
  /** Display name of the entity, when it could be read. */
  entity_name?: string;
  sender_domain: string | null;
  channel: string | null;
  security_kind: string | null;
  scope: "always" | "quiet_hours_only";
  expires_at: string | null;
  created_at: string;
}

const PATH = "/butlers/attention/break-through-clauses";

export function listReachClauses(): Promise<ApiResponse<{ clauses: ReachClause[] }>> {
  return apiFetch<ApiResponse<{ clauses: ReachClause[] }>>(PATH);
}

export function revokeReachClause(id: string): Promise<ApiResponse<{ id: string }>> {
  return apiFetch<ApiResponse<{ id: string }>>(`${PATH}/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

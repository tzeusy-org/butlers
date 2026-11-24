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

/**
 * A clause the owner proposed from Telegram (`/reach add`). It is matchers only and grants
 * nothing until confirmed here; it expires after 24 hours.
 */
export interface ReachProposal {
  id: string;
  sender_domain: string | null;
  channel: string | null;
  security_kind: string | null;
  scope: "always" | "quiet_hours_only";
  created_at: string;
  expires_at: string;
}

const PROPOSALS = "/butlers/attention/break-through-proposals";

export function listReachProposals(): Promise<ApiResponse<{ proposals: ReachProposal[] }>> {
  return apiFetch<ApiResponse<{ proposals: ReachProposal[] }>>(PROPOSALS);
}

export function confirmReachProposal(id: string): Promise<ApiResponse<{ id: string }>> {
  return apiFetch<ApiResponse<{ id: string }>>(`${PROPOSALS}/${encodeURIComponent(id)}/confirm`, {
    method: "POST",
  });
}

export function dismissReachProposal(id: string): Promise<ApiResponse<{ id: string }>> {
  return apiFetch<ApiResponse<{ id: string }>>(`${PROPOSALS}/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/**
 * Decay (S4): a clause nobody opened for 30 days is "asked" about, one with no hit for 90 days is
 * "dormant" (not evaluated, still listed). Only the owner's session can keep or revive one.
 */
export interface ReachDecay {
  clause_id: string;
  state: "asked" | "dormant" | "kept";
  changed_at: string;
}

export function listReachDecay(): Promise<ApiResponse<{ decay: ReachDecay[] }>> {
  return apiFetch<ApiResponse<{ decay: ReachDecay[] }>>("/butlers/attention/break-through-decay");
}

export function keepReachClause(id: string): Promise<ApiResponse<{ id: string }>> {
  return apiFetch<ApiResponse<{ id: string }>>(`${PATH}/${encodeURIComponent(id)}/keep`, {
    method: "POST",
  });
}

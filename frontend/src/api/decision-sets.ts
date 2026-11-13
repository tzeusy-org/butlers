/**
 * Owner-facing decision sets (bu-jv1moa.27): one version of an assistant's batch of parked calls.
 *
 * The server decides everything that matters. `decidable`, `exact`, `in_default_selection` and the
 * wording values arrive on the view; the page renders them and posts back only the version, the
 * verdict and the ticked items' ids with the digests it was shown. Types live here, not in
 * types.ts, for the same reason as home-presence.ts.
 */

import { ApiError, apiFetch } from "@/api/client.ts";

export interface DecisionSetFact {
  label: string;
  value: string;
}

export type DecisionSetItemReason =
  | "missing"
  | "unavailable"
  | "not_pending"
  | "changed"
  | "no_preview";

export interface DecisionSetItem {
  position: number;
  /** Also the id of the item's approval action. */
  effect_id: string;
  capability: string;
  /** A rule or schedule change: decided only when ticked on its own. */
  explicit_only: boolean;
  /** Server verdict: part of the selection Approve covers. */
  in_default_selection: boolean;
  item_digest: string;
  recipient_channel: string | null;
  exact: boolean;
  reason: DecisionSetItemReason | null;
  title: string | null;
  facts: DecisionSetFact[];
  /** The assistant's own words: claims, never verified facts. */
  claims: DecisionSetFact[];
  lineage: { facts: DecisionSetFact[] } | null;
}

export interface DecisionSet {
  set_id: string;
  version: number;
  goal_id: string;
  state: string;
  expires_at: string;
  digest: string;
  items: DecisionSetItem[];
  decidable: boolean;
  reason: "state" | "expired" | "items" | null;
  /** What an unticked item comes to (`left` or `rejected`). */
  unticked_outcome: string;
}

export interface DecisionSetRead {
  set: DecisionSet;
  /** Approval pools that could not be read (their items show as unavailable). */
  sourcesDegraded: string[];
}

export interface DecisionSetPick {
  effect_id: string;
  item_digest: string;
}

export type DecisionSetVerdict = "approve" | "reject";

export interface DecisionSetResult {
  status: "approved" | "rejected";
  set_id: string;
  version: number;
  outcomes: { effect_id: string; outcome: string; status: string }[];
}

interface Envelope<T> {
  data: T;
  meta?: { sources_degraded?: string[] };
}

export async function getDecisionSet(setId: string): Promise<DecisionSetRead> {
  const response = await apiFetch<Envelope<DecisionSet>>(
    `/decision-sets/${encodeURIComponent(setId)}`,
  );
  return { set: response.data, sourcesDegraded: response.meta?.sources_degraded ?? [] };
}

export async function decideDecisionSet(
  setId: string,
  request: { version: number; decision: DecisionSetVerdict; selected: DecisionSetPick[] },
): Promise<DecisionSetResult> {
  const response = await apiFetch<Envelope<DecisionSetResult>>(
    `/decision-sets/${encodeURIComponent(setId)}/decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    },
  );
  return response.data;
}

/** The closed refusal reason of a decision that decided nothing, or null for any other failure. */
export function decisionRefusalReason(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const detail = error.detail;
  if (typeof detail !== "object" || detail === null) return null;
  const record = detail as Record<string, unknown>;
  return record.error === "decision_set_refused" && typeof record.reason === "string"
    ? record.reason
    : null;
}

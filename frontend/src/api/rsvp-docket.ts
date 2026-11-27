/**
 * The invitation docket (bu-5ii4gj.9 S7, REQ-calendar-invitation-docket-005).
 *
 * The server prepares every answer and judges it against a fresh read; the page shows the exact
 * calls and posts back only the ticked cards' ids with the digest each was shown with. Types live
 * here, not in types.ts, for the same reason as decision-sets.ts.
 */

import { apiFetch } from "@/api/client.ts";

export interface DocketFact {
  kind: string;
  note: string;
}

export interface DocketEvent {
  title?: string | null;
  starts_at?: string | null;
  ends_at?: string | null;
}

/** `current` can be approved; `changed` names why not; `unverifiable` could not be re-checked now. */
export type DocketCardState = "current" | "changed" | "unverifiable";

export interface DocketCard {
  action_id: string;
  entry_id: string;
  verdict: string;
  send_updates: string;
  policy_id: string;
  policy_version: number;
  facts: DocketFact[];
  event: DocketEvent | null;
  args_digest: string;
  state: DocketCardState;
  reason: string | null;
}

export interface DisagreeingPolicy {
  id: string;
  position: number;
  verdict: string;
  send_updates: string;
  version: number;
}

export interface DocketDisagreement {
  entry_id: string;
  event: DocketEvent | null;
  policies: DisagreeingPolicy[];
}

export interface Docket {
  cards: DocketCard[];
  disagreed: DocketDisagreement[];
  /** Invitations the docket could not evaluate: an empty docket with a count is not a clean one. */
  unprepared: number;
  unprepared_reasons: Record<string, number>;
  complete: boolean;
  degraded: string[];
  policy_agreement: Array<Record<string, unknown>> | null;
}

export interface DocketSubmitItem {
  action_id: string;
  args_digest: string;
}

export interface DocketRefusal {
  action_id: string;
  entry_id: string | null;
  event: DocketEvent | null;
  reason: string;
}

/** What the approve path did for one card. `dispatched` false means approved but not sent. */
export interface DocketCardOutcome {
  action_id: string;
  status: string;
  dispatched: boolean;
  /** `{code, label, terminal}` when the send failed or was rejected after approval. */
  execution_failure: { code?: string; label?: string; terminal?: boolean } | null;
}

export interface DocketSubmitResult {
  results: DocketCardOutcome[];
  refused: DocketRefusal[];
  /** The batch stopped on an unexpected failure: the rest are `not_attempted`. */
  stopped?: boolean;
}

interface Envelope<T> {
  data: T;
}

export async function getDocket(): Promise<Docket> {
  const response = await apiFetch<Envelope<Docket>>("/calendar/rsvp-docket");
  return response.data;
}

/** The server bounds each card's approve-and-dispatch at this long (DISPATCH_TIMEOUT_S). */
export const DOCKET_CARD_TIMEOUT_MS = 75_000;
const DOCKET_SUBMIT_MARGIN_MS = 15_000;

/** A real batch outlives the default request timeout: allow every card its dispatch bound. */
export function docketSubmitTimeoutMs(itemCount: number): number {
  return itemCount * DOCKET_CARD_TIMEOUT_MS + DOCKET_SUBMIT_MARGIN_MS;
}

export async function submitDocket(items: DocketSubmitItem[]): Promise<DocketSubmitResult> {
  const response = await apiFetch<Envelope<DocketSubmitResult>>("/calendar/rsvp-docket/submit", {
    timeoutMs: docketSubmitTimeoutMs(items.length),
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  return response.data;
}

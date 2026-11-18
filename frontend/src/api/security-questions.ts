/**
 * Owner security questions (bu-s11n0s.1 S5c): "was this you?" for one authenticated
 * account-security alert.
 *
 * The server decides everything that matters. The page shows the question, the owner's own
 * channel facts as masked hints, and posts back only fact ids the server listed plus a command
 * id the page minted. Answering yes changes nothing; answering no holds the chosen channels.
 */

import { ApiError, apiFetch } from "@/api/client.ts";

export type HoldReason = "lost" | "stolen" | "replaced" | "disowned_access";

export interface SecurityCandidate {
  fact_id: string;
  kind: "has-email" | "has-handle" | "has-phone";
  /** Masked hint, never the whole identifier. */
  hint: string;
  held: boolean;
}

export interface SecurityQuestion {
  event_id: string;
  provider: string;
  kind: string;
  presentation: "pending" | "presenting" | "presented" | "unavailable" | "ambiguous";
  admitted_at: string;
  answer: "yes" | "no" | null;
  case_recorded: boolean;
  candidates: SecurityCandidate[];
  recovery_door: string | null;
}

export interface SecurityAnswerResult {
  outcome: string;
  answer: "yes" | "no";
}

interface Envelope<T> {
  data: T;
}

const base = (eventId: string) => `/custody/security-questions/${encodeURIComponent(eventId)}`;

export async function getSecurityQuestion(eventId: string): Promise<SecurityQuestion> {
  const response = await apiFetch<Envelope<SecurityQuestion>>(base(eventId));
  return response.data;
}

export async function answerSecurityQuestion(
  eventId: string,
  request:
    | { answer: "yes" }
    | { answer: "no"; command_id: string; fact_ids: string[]; reason: HoldReason },
): Promise<SecurityAnswerResult> {
  const response = await apiFetch<Envelope<SecurityAnswerResult>>(`${base(eventId)}/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  return response.data;
}

/** Whether an answer bound nothing (the server refused the chosen targets). */
export function answerWasRefused(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

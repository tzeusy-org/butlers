// ---------------------------------------------------------------------------
// send-error-utils.ts — non-component send-error classification (bu-o0ab2)
//
// Split from send-error.tsx to satisfy react-refresh/only-export-components:
// fast-refresh requires component-only files; the type + classifier live
// here, the <SendErrorBanner> component lives in send-error.tsx.
//
// Extracted from FloatingChatWidget.tsx so the butler-detail ChatPanel can
// classify SSE send errors identically instead of rendering an inert
// assistant-bubble error message. See the design doc's Error handling
// section (docs/plans/2026-07-03-dashboard-chat-widget-design.md) for the
// three terminal/error states this distinguishes:
//   - `SWITCHBOARD_UNAVAILABLE` (or any unclassified code) -> a retryable
//     "offline" banner that re-sends the same failed text.
//   - `SESSION_TIMEOUT` -> a graceful "no reply yet" banner with a
//     `/sessions/{id}` inspect link (no retry — the session may still be
//     working).
//   - `TURN_OUTCOME_UNKNOWN` -> an ambiguous terminal outcome (no retry — a
//     replay could duplicate work whose prior outcome cannot be proven).
//   - `INGEST_IN_PROGRESS` -> an existing durable handoff or Stop is still
//     settling (no retry — inspect the same conversation again instead).
// ---------------------------------------------------------------------------

import type { ConversationSseErrorData } from "@/api/types.ts";

export type SendError =
  | { kind: "offline"; message: string; failedText: string; messageId: string }
  | { kind: "timeout"; message: string; sessionId: string | null }
  | { kind: "ambiguous"; message: string }
  | { kind: "pending"; message: string }
  | { kind: "generic"; message: string; failedText: string; messageId: string };

export type RetryableSendError = Exclude<
  SendError,
  { kind: "timeout" | "ambiguous" | "pending" }
>;

/** A durable server cancellation is a terminal stream outcome, not a retryable send error. */
export function isConfirmedConversationCancellation(data: unknown): boolean {
  return (
    typeof data === "object" &&
    data !== null &&
    (data as ConversationSseErrorData).code === "SESSION_CANCELLED"
  );
}

export function classifySendError(
  data: unknown,
  failedText: string,
  messageId: string,
): SendError {
  const errData = (typeof data === "object" && data !== null ? data : {}) as ConversationSseErrorData;
  const message =
    errData.message ?? (typeof data === "string" ? data : "Something went wrong.");

  if (errData.code === "SESSION_TIMEOUT") {
    return { kind: "timeout", message, sessionId: errData.session_id ?? null };
  }
  if (errData.code === "TURN_OUTCOME_UNKNOWN") {
    return { kind: "ambiguous", message };
  }
  if (errData.code === "INGEST_IN_PROGRESS") {
    return { kind: "pending", message };
  }
  if (errData.code === "SWITCHBOARD_UNAVAILABLE") {
    return { kind: "offline", message, failedText, messageId };
  }
  return { kind: "generic", message, failedText, messageId };
}

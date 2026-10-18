// ---------------------------------------------------------------------------
// Debrief reply [bu-q7vx1q.63]
//
// A debrief reply is an ordinary owner message to the Relationship butler's
// pinned dashboard conversation, prefixed with the batch number exactly as the
// Telegram prompt numbers it ("1: send Sam the deck by Fri"), which the
// meeting-debrief skill already parses. No classification, no new route.
// ---------------------------------------------------------------------------

import { createConversation } from "@/api/index.ts";
import { consumeSseStream } from "@/components/chat/sse-utils.ts";

export function debriefReplyMessage(number: number | null, text: string): string {
  const trimmed = text.trim();
  return number == null ? trimmed : `${number}: ${trimmed}`;
}

/**
 * Send the reply and resolve with the created conversation id once the server has
 * durably accepted it. Stops reading at `conversation_created`; the turn was already
 * dispatched, so there is no need to hold the stream open.
 */
export async function sendDebriefReply(number: number | null, text: string): Promise<string> {
  const controller = new AbortController();
  const response = await createConversation(
    "relationship",
    { message: debriefReplyMessage(number, text), message_id: crypto.randomUUID() },
    controller.signal,
  );
  if (!response.ok) throw new Error(`HTTP ${response.status}`);

  let conversationId: string | null = null;
  let failure: string | null = null;
  try {
    await consumeSseStream(response, (event) => {
      if (conversationId || failure) return;
      if (event.event === "conversation_created") {
        const data = event.data as { conversation_id?: unknown };
        if (typeof data?.conversation_id === "string") {
          conversationId = data.conversation_id;
          controller.abort();
        }
      } else if (event.event === "error") {
        failure = "The reply was not accepted";
      }
    });
  } catch (err) {
    if (!conversationId) throw err;
  }
  if (!conversationId) throw new Error(failure ?? "The reply was not accepted");
  return conversationId;
}

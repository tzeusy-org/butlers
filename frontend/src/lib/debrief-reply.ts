// ---------------------------------------------------------------------------
// Debrief reply [bu-q7vx1q.63]
//
// A debrief reply is an ordinary owner message to the Relationship butler's
// pinned dashboard conversation. Batch numbers restart at 1 for every prompt
// batch, so the message carries the debrief's stable id; the meeting-debrief
// skill matches by id when present and by number only otherwise. No
// classification, no new route.
// ---------------------------------------------------------------------------

import { createConversation } from "@/api/index.ts";
import type { PendingDebrief } from "@/api/types.ts";
import { consumeSseStream } from "@/components/chat/sse-utils.ts";

function oneLine(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

export function debriefReplyMessage(debrief: PendingDebrief, text: string): string {
  const title = oneLine(debrief.event_title || "Untitled meeting");
  const date = debrief.occurrence_start.slice(0, 10);
  return `[debrief:${debrief.debrief_id}] ${title} (${date}): ${text.trim()}`;
}

/**
 * Send the reply and resolve with the conversation id once the server has durably
 * accepted it. `conversation_created` is emitted before the ingress claim and the
 * Switchboard submission, so it is not proof of delivery; only `dispatch_accepted` is.
 * An `error` event, or a stream that ends first, rejects. Reuse `messageId` across
 * retries of the same text so the server can de-duplicate the submission.
 */
export async function sendDebriefReply(
  debrief: PendingDebrief,
  text: string,
  messageId: string,
): Promise<string> {
  const controller = new AbortController();
  const response = await createConversation(
    "relationship",
    { message: debriefReplyMessage(debrief, text), message_id: messageId },
    controller.signal,
  );
  if (!response.ok) throw new Error(`HTTP ${response.status}`);

  let conversationId: string | null = null;
  let accepted = false;
  let failure: string | null = null;
  try {
    await consumeSseStream(response, (event) => {
      if (accepted || failure) return;
      if (event.event === "conversation_created") {
        const data = event.data as { conversation_id?: unknown };
        if (typeof data?.conversation_id === "string") conversationId = data.conversation_id;
      } else if (event.event === "dispatch_accepted") {
        accepted = true;
        controller.abort();
      } else if (event.event === "error") {
        const data = event.data as { message?: unknown };
        failure = typeof data?.message === "string" ? data.message : "The reply was not accepted";
      }
    });
  } catch (err) {
    if (!accepted) throw err;
  }
  if (failure) throw new Error(failure);
  if (!accepted) throw new Error("The reply was not confirmed as sent");
  return conversationId ?? "";
}

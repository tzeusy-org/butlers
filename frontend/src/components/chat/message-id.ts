/**
 * Generate the UUID identity a dashboard message keeps for all retries.
 *
 * ``crypto.randomUUID`` is available in the browsers we target, but a valid
 * UUIDv4 fallback keeps a send from failing before it reaches the API in an
 * older embedded browser. The API validates ``message_id`` as a UUID.
 */

function fallbackUuid4(): string {
  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }

  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;

  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function createClientMessageId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return fallbackUuid4();
}

/**
 * DOM id for a message bubble anchor. Shared between `MessageThread` (which
 * sets it) and `scrollToMessageAnchor` (which looks it up) so a
 * conversation_recall / message-search jump-to-message result can find its
 * bubble once the conversation's messages have rendered (bu-0ynlk.9).
 */
export function messageAnchorId(messageId: string): string {
  return `message-${messageId}`;
}

/**
 * Scroll a message bubble into view and briefly highlight it.
 *
 * A no-op when the message isn't in the DOM yet (e.g. the conversation's
 * messages haven't finished loading) — callers should retry once loading
 * completes rather than treating a miss as an error.
 */
export function scrollToMessageAnchor(messageId: string): boolean {
  if (typeof document === "undefined") return false;
  const el = document.getElementById(messageAnchorId(messageId));
  if (!el) return false;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("chat-message-highlight");
  window.setTimeout(() => el.classList.remove("chat-message-highlight"), 2000);
  return true;
}

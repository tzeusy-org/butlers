import type { Message } from "@/api/types.ts";

const OPTIMISTIC_USER_MESSAGE_PREFIX = "optimistic-user-";

/**
 * Gives a local optimistic bubble the same stable identity as its eventual
 * server message, without colliding with UUIDs returned by the API.
 */
export function optimisticUserMessageId(messageId: string): string {
  return `${OPTIMISTIC_USER_MESSAGE_PREFIX}${messageId}`;
}

function clientMessageIdForOptimisticUser(message: Message): string | null {
  if (message.role !== "user" || !message.id.startsWith(OPTIMISTIC_USER_MESSAGE_PREFIX)) {
    return null;
  }
  return message.id.slice(OPTIMISTIC_USER_MESSAGE_PREFIX.length);
}

/**
 * Reconciles a server snapshot without discarding user bubbles that have not
 * yet been committed under their stable client message ID. The server remains
 * authoritative once it reports that ID, which also removes the optimistic
 * duplicate after a retry succeeds.
 */
export function reconcileConversationMessages(
  serverMessages: Message[],
  localMessages: Message[],
  activeConversationId: string | null,
): Message[] {
  const committedIds = new Set(serverMessages.map((message) => message.id));
  const localConversationId = activeConversationId ?? "";
  const retainedMessageIds = new Set<string>();
  const uncommittedOptimisticMessages = localMessages.filter((message) => {
    const clientMessageId = clientMessageIdForOptimisticUser(message);
    if (
      !clientMessageId ||
      message.conversation_id !== localConversationId ||
      committedIds.has(clientMessageId)
    ) {
      return false;
    }
    if (retainedMessageIds.has(clientMessageId)) {
      return false;
    }
    retainedMessageIds.add(clientMessageId);
    return true;
  });

  return uncommittedOptimisticMessages.length > 0
    ? [...serverMessages, ...uncommittedOptimisticMessages]
    : serverMessages;
}

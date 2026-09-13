/**
 * Shared conversation "turn" state machine — send/stream/stop/retry.
 *
 * Extracted from the ~250 lines duplicated verbatim between
 * `components/chat/ChatPanel.tsx` (`ChatContent`) and
 * `components/chat/FloatingChatWidget.tsx` (`WidgetPanel`) (bu-0ynlk.11).
 * Both callers now delegate their send/stream/stop/retry logic to this one
 * hook instead of maintaining independent copies of the same reducer.
 *
 * Scope: exactly the turn machine (one active send, its SSE stream, its
 * optimistic local messages, and Stop/retry). Deliberately excludes:
 *   - which conversation is "active" (`activeConversationId` is owned by the
 *     caller — it spans more than one turn: conversation-list selection,
 *     keyboard quick-switch, view-mode toggling all live above this hook)
 *   - fetching the conversation list / message history (`useConversations`,
 *     `useConversationMessages` stay caller-owned so each surface keeps its
 *     own loading/error UI without extra passthrough plumbing)
 *   - cross-posture live-token mirroring (e.g. a dock and the /chat full page
 *     both reflecting one in-flight stream) — each mounted instance of this
 *     hook owns its own local turn state; instances converge once
 *     `message_complete` invalidates the shared TanStack Query cache. True
 *     cross-posture stream mirroring is streaming-architecture territory,
 *     not this move.
 *
 * Rebased onto bu-0ynlk.7's NOTIFY-driven streaming work (merged to main
 * after this extraction): the `phase` SSE event (real-time processing
 * status — classifying/routed/starting_session/thinking/writing, surfaced
 * via `StreamingState.phase`) and the non-abort stream-failure refetch
 * recovery (a dropped SSE connection may still have landed its reply
 * server-side; refetch before showing a spurious error banner) both
 * originated in bu-0ynlk.7's now-duplicated ChatPanel.tsx/FloatingChatWidget.tsx
 * copies and are folded in here rather than re-duplicated.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  cancelConversationMessageTurn,
  createConversation,
  getConversationMessages,
  sendMessage,
} from "@/api/index.ts";
import type {
  ConversationSsePhaseData,
  CreateConversationRequest,
  Message,
} from "@/api/types.ts";
import { consumeSseStream } from "@/components/chat/sse-utils.ts";
import type { StreamingState } from "@/components/chat/MessageThread.tsx";
import {
  classifySendError,
  isConfirmedConversationCancellation,
  type SendError,
} from "@/components/chat/send-error-utils.ts";
import { createClientMessageId } from "@/components/chat/message-id.ts";
import {
  optimisticUserMessageId,
  reconcileConversationMessages,
} from "@/components/chat/message-reconciliation.ts";
import { conversationKeys } from "./use-conversations.ts";
import { usePageContextCapture, type PageContextSnapshot } from "@/lib/page-context.tsx";

/** Builds the outgoing message body for both create and send-follow-up calls. */
function buildMessagePayload(
  message: string,
  messageId: string,
  snapshot: PageContextSnapshot,
  included: boolean,
): CreateConversationRequest {
  if (included && snapshot.context) {
    return { message, message_id: messageId, page_context: snapshot.context };
  }
  return { message, message_id: messageId };
}

export interface UseConversationTurnOptions {
  butlerName: string;
  activeConversationId: string | null;
  setActiveConversationId: (id: string | null) => void;
  /** The caller's own `useConversationMessages(butlerName, activeConversationId)` result data. */
  messagesData: { data: Message[] } | undefined;
}

export interface UseConversationTurnResult {
  streaming: StreamingState | null;
  /** Messages visible for the currently active conversation (empty during a conversation switch). */
  visibleMessages: Message[];
  sendError: SendError | null;
  setSendError: (error: SendError | null) => void;
  isStreaming: boolean;
  hasActiveRuntime: boolean;
  /** Fresh page-context snapshot for the composer's ContextChip label — recomputed every call. */
  contextPreview: PageContextSnapshot;
  includeContext: boolean;
  toggleIncludeContext: () => void;
  sendText: (text: string, retryMessageId?: string) => Promise<void>;
  handleStop: () => Promise<void>;
  /** Aborts any in-flight stream watch without contacting the server (component unmount, conversation switch). */
  abandonCurrentStream: () => void;
  /** Clears local turn state for a fresh "New conversation" / butler switch. Does not touch `activeConversationId`. */
  resetTurnState: () => void;
}

export function useConversationTurn({
  butlerName,
  activeConversationId,
  setActiveConversationId,
  messagesData,
}: UseConversationTurnOptions): UseConversationTurnResult {
  const queryClient = useQueryClient();
  const capturePageContext = usePageContextCapture();
  const contextPreview = capturePageContext();

  const [streaming, setStreaming] = useState<StreamingState | null>(null);
  const [localMessages, setLocalMessages] = useState<Message[]>([]);
  const localMessagesConversationIdRef = useRef<string | null>(null);
  const [sendError, setSendError] = useState<SendError | null>(null);
  // Per-message opt-out for the ContextChip (bu-0ynlk.4) — resets to true
  // after every send so removal only ever applies to the one message it was
  // clicked on.
  const [includeContext, setIncludeContext] = useState(true);

  const abortRef = useRef<AbortController | null>(null);
  const interruptedTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeMessageIdRef = useRef<string | null>(null);
  const confirmedStopMessageIdRef = useRef<string | null>(null);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
      activeMessageIdRef.current = null;
      if (interruptedTimeoutRef.current !== null) {
        clearTimeout(interruptedTimeoutRef.current);
        interruptedTimeoutRef.current = null;
      }
      confirmedStopMessageIdRef.current = null;
    };
  }, []);

  // Sync server messages into local state. Avoid overwriting optimistic/
  // streaming messages while an SSE stream is active. Guards the transient
  // `messagesData === undefined` window TanStack Query passes through while
  // refetching after an `activeConversationId` switch (staleTime: 0 means
  // every switch refetches) — preserves the previous conversation's cached
  // local state for a same-thread retry, but never reconciles it into the
  // newly selected conversation.
  useEffect(() => {
    if (streaming) return;
    if (messagesData?.data) {
      const previousBelongsToActiveConversation =
        localMessagesConversationIdRef.current === activeConversationId;
      localMessagesConversationIdRef.current = activeConversationId;
      setLocalMessages((previous) => {
        const activeMessages = previousBelongsToActiveConversation ? previous : [];
        return reconcileConversationMessages(
          messagesData.data,
          activeMessages,
          activeConversationId,
        );
      });
    }
  }, [activeConversationId, messagesData, streaming]);

  const isStreaming = streaming !== null;
  const hasActiveRuntime = isStreaming && !streaming?.cancelled;

  const confirmStoppedTurn = useCallback(
    (messageId: string, conversationId?: string | null) => {
      if (activeMessageIdRef.current !== messageId) return;
      confirmedStopMessageIdRef.current = messageId;
      // A Stop can win before the first conversation_created SSE event. Keep
      // the persisted thread addressable instead of letting its optimistic
      // bubble disappear with a pending local conversation id.
      void queryClient.invalidateQueries({ queryKey: conversationKeys.all(butlerName) });
      if (conversationId) {
        localMessagesConversationIdRef.current = conversationId;
        setActiveConversationId(conversationId);
        setLocalMessages((prev) =>
          prev.map((message) =>
            message.id === optimisticUserMessageId(messageId)
              ? { ...message, conversation_id: conversationId }
              : message,
          ),
        );
        void queryClient.invalidateQueries({
          queryKey: conversationKeys.messages(butlerName, conversationId),
        });
      }
      abortRef.current?.abort();
      setStreaming((prev) =>
        prev?.messageId === messageId
          ? {
              ...prev,
              conversationId: conversationId ?? prev.conversationId,
              cancelling: false,
              cancelled: true,
              pending: false,
              cancelError: null,
              dispatchReceipt: undefined,
            }
          : prev,
      );
      if (interruptedTimeoutRef.current !== null) {
        clearTimeout(interruptedTimeoutRef.current);
      }
      const timeout = setTimeout(() => {
        if (interruptedTimeoutRef.current !== timeout) return;
        if (activeMessageIdRef.current === messageId) {
          activeMessageIdRef.current = null;
          abortRef.current = null;
          setStreaming((prev) => (prev?.messageId === messageId ? null : prev));
        }
        interruptedTimeoutRef.current = null;
      }, 1500);
      interruptedTimeoutRef.current = timeout;
    },
    [butlerName, queryClient, setActiveConversationId],
  );

  const sendText = useCallback(
    async (text: string, retryMessageId?: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      setSendError(null);
      const isNew = activeConversationId == null;
      const messageId = retryMessageId ?? createClientMessageId();
      if (activeMessageIdRef.current !== null && activeMessageIdRef.current !== messageId) {
        abortRef.current?.abort();
      }
      if (interruptedTimeoutRef.current !== null) {
        clearTimeout(interruptedTimeoutRef.current);
        interruptedTimeoutRef.current = null;
      }
      const controller = new AbortController();
      abortRef.current = controller;
      activeMessageIdRef.current = messageId;
      confirmedStopMessageIdRef.current = null;

      const userMessage: Message = {
        // The backend retry identity also identifies this local optimistic
        // bubble, so retrying one logical message cannot add another bubble.
        id: optimisticUserMessageId(messageId),
        conversation_id: activeConversationId ?? "",
        role: "user",
        content: trimmed,
        tool_calls: null,
        error: null,
        model: null,
        input_tokens: null,
        output_tokens: null,
        duration_ms: null,
        session_id: null,
        request_id: null,
        created_at: new Date().toISOString(),
      };
      const previousBelongsToActiveConversation =
        localMessagesConversationIdRef.current === activeConversationId;
      localMessagesConversationIdRef.current = activeConversationId;
      setLocalMessages((previous) => {
        const activeMessages = previousBelongsToActiveConversation ? previous : [];
        return activeMessages.some((message) => message.id === userMessage.id)
          ? activeMessages
          : [...activeMessages, userMessage];
      });

      let currentConversationId = activeConversationId;

      setStreaming({
        conversationId: currentConversationId ?? "pending",
        messageId,
        content: "",
        pending: true,
        interrupted: false,
        stopReady: false,
      });

      // Snapshot page context NOW, not before — this is the exact moment of
      // send, so a page navigation or usePageSubject().set() call happening
      // after this point never mutates the payload already built below.
      const pageContextSnapshot = capturePageContext();
      const contextIncludedForThisSend = includeContext;
      // The chip's opt-out only ever applies to the message it was clicked
      // on — reset immediately so the next composition defaults back to
      // attached (behavior matrix: "next send re-attaches").
      setIncludeContext(true);

      try {
        const response = isNew
          ? await createConversation(
              butlerName,
              buildMessagePayload(
                trimmed,
                messageId,
                pageContextSnapshot,
                contextIncludedForThisSend,
              ),
              controller.signal,
            )
          : await sendMessage(
              butlerName,
              activeConversationId!,
              buildMessagePayload(
                trimmed,
                messageId,
                pageContextSnapshot,
                contextIncludedForThisSend,
              ),
              controller.signal,
            );

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        // The API creates the durable user-message/turn record before it
        // returns its SSE response. Stop may become actionable now, even
        // though the first conversation_created event can still be pending.
        setStreaming((prev) =>
          prev?.messageId === messageId ? { ...prev, stopReady: true } : prev,
        );

        await consumeSseStream(response, (event) => {
          if (
            activeMessageIdRef.current !== messageId ||
            confirmedStopMessageIdRef.current === messageId
          ) {
            return;
          }
          switch (event.event) {
            case "conversation_created": {
              // Backend emits `conversation_id` (see routers/conversations.py
              // _stream_conversation_response) — NOT `id`.
              const data = event.data as { conversation_id: string; title?: string | null };
              currentConversationId = data.conversation_id;
              setActiveConversationId(data.conversation_id);
              setStreaming((prev) =>
                prev?.messageId === messageId
                  ? { ...prev, conversationId: data.conversation_id }
                  : prev,
              );
              localMessagesConversationIdRef.current = data.conversation_id;
              setLocalMessages((prev) =>
                prev.map((m) =>
                  m.id === userMessage.id ? { ...m, conversation_id: data.conversation_id } : m,
                ),
              );
              break;
            }
            case "dispatch_accepted": {
              const data = event.data as { routed_butler?: unknown };
              const routedButler =
                typeof data.routed_butler === "string" ? data.routed_butler : null;
              setStreaming((prev) =>
                prev ? { ...prev, dispatchReceipt: { routedButler } } : null,
              );
              break;
            }
            case "phase": {
              const data = event.data as ConversationSsePhaseData;
              const phase = typeof data.phase === "string" ? data.phase : null;
              if (!phase) break;
              setStreaming((prev) =>
                prev?.messageId === messageId
                  ? {
                      ...prev,
                      phase: {
                        name: phase,
                        target: typeof data.target === "string" ? data.target : undefined,
                        tool: typeof data.tool === "string" ? data.tool : undefined,
                      },
                    }
                  : prev,
              );
              break;
            }
            case "token": {
              const token =
                typeof event.data === "string"
                  ? event.data
                  : ((event.data as { content?: string })?.content ?? "");
              setStreaming((prev) =>
                prev?.messageId === messageId
                  ? { ...prev, content: prev.content + token, pending: false }
                  : prev,
              );
              break;
            }
            case "message_complete": {
              const cid = currentConversationId;
              if (cid) {
                void queryClient.invalidateQueries({
                  queryKey: conversationKeys.all(butlerName),
                });
                void queryClient.invalidateQueries({
                  queryKey: conversationKeys.messages(butlerName, cid),
                });
              }
              activeMessageIdRef.current = null;
              abortRef.current = null;
              setStreaming((prev) => (prev?.messageId === messageId ? null : prev));
              break;
            }
            case "error": {
              if (isConfirmedConversationCancellation(event.data)) {
                confirmStoppedTurn(messageId, currentConversationId);
                break;
              }
              setSendError(classifySendError(event.data, trimmed, messageId));
              activeMessageIdRef.current = null;
              abortRef.current = null;
              setStreaming((prev) => (prev?.messageId === messageId ? null : prev));
              break;
            }
            case "done":
              activeMessageIdRef.current = null;
              abortRef.current = null;
              setStreaming((prev) => (prev?.messageId === messageId ? null : prev));
              break;
          }
        });
      } catch (err) {
        if (activeMessageIdRef.current !== messageId) return;
        if (err instanceof Error && err.name === "AbortError") {
          if (confirmedStopMessageIdRef.current === messageId) {
            // handleStop already rendered the durable confirmation and owns
            // the short visual handoff; do not overwrite it with a generic
            // client-side "interrupted" state.
            return;
          }
          setStreaming((prev) =>
            prev?.messageId === messageId ? { ...prev, interrupted: true, pending: false } : prev,
          );
          if (interruptedTimeoutRef.current !== null) {
            clearTimeout(interruptedTimeoutRef.current);
          }
          const timeout = setTimeout(() => {
            if (interruptedTimeoutRef.current !== timeout) return;
            if (activeMessageIdRef.current === messageId) {
              activeMessageIdRef.current = null;
              abortRef.current = null;
              setStreaming((prev) => (prev?.messageId === messageId ? null : prev));
            }
            interruptedTimeoutRef.current = null;
          }, 1500);
          interruptedTimeoutRef.current = timeout;
        } else {
          // Non-abort stream failure: the reply may have already landed
          // server-side even though this SSE connection itself failed, so
          // refetch before declaring failure (bu-0ynlk.7) rather than
          // clobbering a completed reply with a spurious error banner.
          const conversationIdForRecovery = currentConversationId ?? activeConversationId;
          let replyLanded = false;
          if (conversationIdForRecovery) {
            try {
              const refetched = await getConversationMessages(
                butlerName,
                conversationIdForRecovery,
              );
              replyLanded = refetched.data.some(
                (m) =>
                  m.role === "assistant" &&
                  new Date(m.created_at).getTime() > new Date(userMessage.created_at).getTime(),
              );
            } catch {
              // Refetch itself failed — fall through to the honest failure path.
            }
          }
          activeMessageIdRef.current = null;
          abortRef.current = null;
          setStreaming((prev) => (prev?.messageId === messageId ? null : prev));
          if (replyLanded && conversationIdForRecovery) {
            void queryClient.invalidateQueries({ queryKey: conversationKeys.all(butlerName) });
            void queryClient.invalidateQueries({
              queryKey: conversationKeys.messages(butlerName, conversationIdForRecovery),
            });
          } else {
            setSendError({
              kind: "generic",
              message: "Failed to send message.",
              failedText: trimmed,
              messageId,
            });
          }
        }
      }
    },
    [
      activeConversationId,
      butlerName,
      capturePageContext,
      confirmStoppedTurn,
      includeContext,
      queryClient,
      setActiveConversationId,
    ],
  );

  const handleStop = useCallback(async () => {
    if (!streaming || !streaming.stopReady || streaming.cancelling) return;
    const messageId = streaming.messageId;
    if (activeMessageIdRef.current !== messageId) return;

    setStreaming((prev) =>
      prev?.messageId === messageId ? { ...prev, cancelling: true, cancelError: null } : prev,
    );
    try {
      const result = await cancelConversationMessageTurn(butlerName, messageId);
      if (activeMessageIdRef.current !== messageId) return;
      if (!result.cancelled) {
        if (result.already_finished) {
          if (confirmedStopMessageIdRef.current === messageId) {
            // The stream already delivered authoritative cancellation for this
            // exact message while the Stop POST was in flight. Keep that
            // confirmation visible through its deliberate handoff window.
            return;
          }
          // The turn already finished on its own — quietly stop watching.
          // Never claim we stopped something that had already ended. Refresh
          // before aborting the SSE: completion can commit just before this
          // status read, while its message_complete event is still buffered.
          const conversationId =
            result.conversation_id ??
            (streaming.conversationId === "pending" ? activeConversationId : streaming.conversationId);
          void queryClient.invalidateQueries({
            queryKey: conversationKeys.all(butlerName),
          });
          if (conversationId) {
            localMessagesConversationIdRef.current = conversationId;
            setActiveConversationId(conversationId);
            setLocalMessages((prev) =>
              prev.map((message) =>
                message.id === optimisticUserMessageId(messageId)
                  ? { ...message, conversation_id: conversationId }
                  : message,
              ),
            );
            void queryClient.invalidateQueries({
              queryKey: conversationKeys.messages(butlerName, conversationId),
            });
          }
          abortRef.current?.abort();
          abortRef.current = null;
          activeMessageIdRef.current = null;
          setStreaming((prev) => (prev?.messageId === messageId ? null : prev));
          return;
        }
        setStreaming((prev) =>
          prev?.messageId === messageId
            ? {
                ...prev,
                cancelling: false,
                pending: false,
                cancelError: result.message ?? "Could not stop. Try again.",
              }
            : prev,
        );
        return;
      }
      confirmStoppedTurn(messageId, result.conversation_id);
    } catch {
      if (activeMessageIdRef.current !== messageId) return;
      setStreaming((prev) =>
        prev?.messageId === messageId
          ? { ...prev, cancelling: false, pending: false, cancelError: "Could not stop. Try again." }
          : prev,
      );
    }
  }, [activeConversationId, butlerName, confirmStoppedTurn, queryClient, setActiveConversationId, streaming]);

  const abandonCurrentStream = useCallback(() => {
    activeMessageIdRef.current = null;
    confirmedStopMessageIdRef.current = null;
    abortRef.current?.abort();
    abortRef.current = null;
    if (interruptedTimeoutRef.current !== null) {
      clearTimeout(interruptedTimeoutRef.current);
      interruptedTimeoutRef.current = null;
    }
    setStreaming(null);
  }, []);

  const resetTurnState = useCallback(() => {
    localMessagesConversationIdRef.current = null;
    setLocalMessages([]);
    setSendError(null);
  }, []);

  const toggleIncludeContext = useCallback(() => {
    setIncludeContext((prev) => !prev);
  }, []);

  const visibleMessages =
    localMessagesConversationIdRef.current === activeConversationId ? localMessages : [];

  return {
    streaming,
    visibleMessages,
    sendError,
    setSendError,
    isStreaming,
    hasActiveRuntime,
    contextPreview,
    includeContext,
    toggleIncludeContext,
    sendText,
    handleStop,
    abandonCurrentStream,
    resetTurnState,
  };
}

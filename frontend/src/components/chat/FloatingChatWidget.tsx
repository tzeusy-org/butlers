/**
 * Global floating chat widget (bu-p6ey8.3) — a compact popover chat panel
 * reachable from every dashboard route, mounted once in RootLayout.tsx.
 *
 * Talks to the Switchboard butler's conversations API (the same
 * POST/GET /api/butlers/switchboard/conversations spine the butler-detail
 * ChatPanel.tsx Sheet uses), so widget conversations are the owner's single
 * "everything I told the system" history — visible on Switchboard's own
 * butler-detail chat panel too (docs/plans/2026-07-03-dashboard-chat-widget-
 * design.md § Storage scope).
 *
 * Differs from ChatPanel.tsx (which renders a wide Sheet with a persistent
 * sidebar + thread split pane) in two ways suited to a small floating
 * footprint:
 *   - Two full-width VIEWS (thread | history) toggled by a header button,
 *     rather than a permanent side-by-side sidebar.
 *   - SSE `error` events are classified by `code` (see
 *     ConversationSseErrorData) into distinct recoverable states — a
 *     retryable "Switchboard offline" banner vs. a graceful "no reply —
 *     inspect session" timeout banner — per the design doc's Error handling
 *     section. The classification + banner rendering live in `./send-error.tsx`
 *     and are shared with ChatPanel.tsx (bu-o0ab2), so both surfaces behave
 *     identically on send failure.
 *
 * Lifecycle: the panel's content only mounts while `open` (mirrors
 * ChatPanel's `{open && <ChatContent/>}` gate), so every reopen re-fetches
 * the conversation list and re-selects the most recently updated *active*
 * conversation (list is server-ordered `updated_at DESC`) — "reopening
 * resumes the most recent open conversation" falls out of that refetch,
 * no extra persistence needed.
 *
 * Page-context capture and the unread badge (bu-p6ey8.4) hang off two seams
 * left by bu-p6ey8.3: `buildMessagePayload()` is the single choke point both
 * `createConversation`/`sendMessage` calls go through, now taking a
 * `PageContext` snapshot (`usePageContextCapture()`, see
 * `@/lib/page-context.tsx`) captured fresh at send time; the trigger button
 * renders a badge driven by `useChatUnreadBadge()` (see
 * `@/hooks/use-chat-unread.ts`).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftIcon,
  HistoryIcon,
  MessageCircleIcon,
  PlusIcon,
  XIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  cancelConversationMessageTurn,
  createConversation,
  sendMessage,
} from "@/api/index.ts";
import type {
  ConversationSummary,
  CreateConversationRequest,
  Message,
  PageContext,
} from "@/api/types.ts";
import { consumeSseStream } from "./sse-utils.ts";
import { ConversationList } from "./ConversationList.tsx";
import { ConversationHeader } from "./ConversationHeader.tsx";
import { ConversationReadError } from "./ConversationReadError.tsx";
import { MessageThread, MessageThreadSkeleton } from "./MessageThread.tsx";
import type { StreamingState } from "./MessageThread.tsx";
import { MessageInput } from "./MessageInput.tsx";
import { SendErrorBanner } from "./send-error.tsx";
import {
  classifySendError,
  isConfirmedConversationCancellation,
  type SendError,
} from "./send-error-utils.ts";
import { createClientMessageId } from "./message-id.ts";
import {
  optimisticUserMessageId,
  reconcileConversationMessages,
} from "./message-reconciliation.ts";
import {
  conversationKeys,
  useConversations,
  useConversationMessages,
} from "@/hooks/use-conversations.ts";
import { usePricingMap } from "@/hooks/use-pricing-map.ts";
import { useChatUnreadBadge } from "@/hooks/use-chat-unread.ts";
import { useModalChoreography } from "@/hooks/use-modal-choreography";
import { usePageContextCapture } from "@/lib/page-context.tsx";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry.tsx";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** The staffer butler that owns dashboard chat-widget conversations (see
 * design doc § Storage scope — all widget threads live under Switchboard's
 * schema; the routed-to domain butler is metadata, not storage location). */
const WIDGET_BUTLER = "switchboard";

// ---------------------------------------------------------------------------
// Send payload builder — the seam bu-p6ey8.4 (page-context capture) extends.
// ---------------------------------------------------------------------------

/**
 * Builds the outgoing message body for both `createConversation` and
 * `sendMessage`. `pageContext` is a snapshot from `usePageContextCapture()`
 * taken at the moment of send (see `sendText` below) — the single choke
 * point the widget uses to submit a message, so no call site needed to
 * change when page-context capture was added.
 */
function buildMessagePayload(
  message: string,
  messageId: string,
  pageContext: PageContext,
): CreateConversationRequest {
  return { message, message_id: messageId, page_context: pageContext };
}

// ---------------------------------------------------------------------------
// WidgetPanel — mounted only while the widget is open
// ---------------------------------------------------------------------------

interface WidgetPanelProps {
  onClose: () => void;
}

function WidgetPanel({ onClose }: WidgetPanelProps) {
  const queryClient = useQueryClient();
  const capturePageContext = usePageContextCapture();
  // This anchored popover deliberately leaves page tab order available, while
  // still following the shared focus-in/Escape/restore choreography.
  const { rootRef, initialFocusRef, onKeyDown } = useModalChoreography<HTMLHeadingElement>({
    onClose,
    trapFocus: false,
  });

  const [viewMode, setViewMode] = useState<"thread" | "history">("thread");
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState("");
  const [streaming, setStreaming] = useState<StreamingState | null>(null);
  const [localMessages, setLocalMessages] = useState<Message[]>([]);
  const localMessagesConversationIdRef = useRef<string | null>(null);
  const [sendError, setSendError] = useState<SendError | null>(null);
  const { data: pricingMapData } = usePricingMap();
  const pricingMap = pricingMapData ?? null;

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

  // Fetch the conversation list once so we can resume the most recently
  // updated *active* (open) conversation on every reopen (list is
  // server-ordered updated_at DESC).
  const { data: conversationsData, isLoading: isLoadingConversations } =
    useConversations(WIDGET_BUTLER);
  const conversations: ConversationSummary[] = useMemo(
    () => conversationsData?.data ?? [],
    [conversationsData],
  );

  const {
    data: messagesData,
    isLoading: isLoadingMessages,
    isError: isMessagesError,
    refetch: refetchMessages,
  } = useConversationMessages(WIDGET_BUTLER, activeConversationId);

  useEffect(() => {
    if (streaming) return;
    // Guard against the transient `messagesData === undefined` window that
    // TanStack Query passes through while refetching after switching
    // `activeConversationId` (staleTime: 0 means every switch refetches).
    // Preserve the previous conversation's cached local state for a
    // same-thread retry, but never reconcile it into the selected conversation.
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

  // Resume the most recent open conversation ONCE per mount (== once per
  // reopen, since WidgetPanel unmounts entirely on close) — gated by
  // hasResumedRef so a later "New conversation" click (which also sets
  // activeConversationId to null) does not get immediately overridden back
  // to the existing thread by this same effect.
  const hasResumedRef = useRef(false);
  useEffect(() => {
    if (hasResumedRef.current) return;
    if (conversations.length === 0) return;
    hasResumedRef.current = true;
    if (activeConversationId == null) {
      setActiveConversationId(conversations[0].id);
    }
  }, [conversations, activeConversationId]);

  const activeConversation = conversations.find((c) => c.id === activeConversationId) ?? null;
  const isStreaming = streaming !== null;
  const hasActiveRuntime = isStreaming && !streaming?.cancelled;

  const confirmStoppedTurn = useCallback(
    (messageId: string, conversationId?: string | null) => {
      if (activeMessageIdRef.current !== messageId) return;
      confirmedStopMessageIdRef.current = messageId;
      // A Stop can win before the first conversation_created SSE event. Keep
      // the persisted thread addressable instead of letting its optimistic
      // bubble disappear with a pending local conversation id.
      void queryClient.invalidateQueries({ queryKey: conversationKeys.all(WIDGET_BUTLER) });
      if (conversationId) {
        setActiveConversationId(conversationId);
        setLocalMessages((prev) =>
          prev.map((message) =>
            message.id === optimisticUserMessageId(messageId)
              ? { ...message, conversation_id: conversationId }
              : message,
          ),
        );
        void queryClient.invalidateQueries({
          queryKey: conversationKeys.messages(WIDGET_BUTLER, conversationId),
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
    [queryClient],
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
      // send, so a page navigation or usePageContext().set() call happening
      // after this point never mutates the payload already built below.
      const pageContext = capturePageContext();

      try {
        const response = isNew
          ? await createConversation(
              WIDGET_BUTLER,
              buildMessagePayload(trimmed, messageId, pageContext),
              controller.signal,
            )
          : await sendMessage(
              WIDGET_BUTLER,
              activeConversationId!,
              buildMessagePayload(trimmed, messageId, pageContext),
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
                  queryKey: conversationKeys.all(WIDGET_BUTLER),
                });
                void queryClient.invalidateQueries({
                  queryKey: conversationKeys.messages(WIDGET_BUTLER, cid),
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
          activeMessageIdRef.current = null;
          abortRef.current = null;
          setStreaming((prev) => (prev?.messageId === messageId ? null : prev));
          setSendError({
            kind: "generic",
            message: "Failed to send message.",
            failedText: trimmed,
            messageId,
          });
        }
      }
    },
    [activeConversationId, capturePageContext, confirmStoppedTurn, queryClient],
  );

  function handleSendClick() {
    const text = inputValue.trim();
    if (!text) return;
    setInputValue("");
    void sendText(text);
  }

  async function handleStop() {
    if (!streaming || !streaming.stopReady || streaming.cancelling) return;
    const messageId = streaming.messageId;
    if (activeMessageIdRef.current !== messageId) return;

    setStreaming((prev) =>
      prev?.messageId === messageId ? { ...prev, cancelling: true, cancelError: null } : prev,
    );
    try {
      const result = await cancelConversationMessageTurn(WIDGET_BUTLER, messageId);
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
            queryKey: conversationKeys.all(WIDGET_BUTLER),
          });
          if (conversationId) {
            setActiveConversationId(conversationId);
            setLocalMessages((prev) =>
              prev.map((message) =>
                message.id === optimisticUserMessageId(messageId)
                  ? { ...message, conversation_id: conversationId }
                  : message,
              ),
            );
            void queryClient.invalidateQueries({
              queryKey: conversationKeys.messages(WIDGET_BUTLER, conversationId),
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
  }

  function abandonCurrentStream() {
    activeMessageIdRef.current = null;
    confirmedStopMessageIdRef.current = null;
    abortRef.current?.abort();
    abortRef.current = null;
    if (interruptedTimeoutRef.current !== null) {
      clearTimeout(interruptedTimeoutRef.current);
      interruptedTimeoutRef.current = null;
    }
    setStreaming(null);
  }

  function handleNewConversation() {
    abandonCurrentStream();
    setActiveConversationId(null);
    localMessagesConversationIdRef.current = null;
    setLocalMessages([]);
    setSendError(null);
    setViewMode("thread");
  }

  function handleCheckAgain() {
    setSendError(null);
    if (activeConversationId) {
      void queryClient.invalidateQueries({
        queryKey: conversationKeys.messages(WIDGET_BUTLER, activeConversationId),
      });
    }
  }

  const visibleMessages =
    localMessagesConversationIdRef.current === activeConversationId ? localMessages : [];

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- role="dialog" + onKeyDown provides the shared Escape/focus choreography; the rule's static role allowlist does not recognize the WAI-ARIA dialog pattern.
    <div
      ref={rootRef}
      className="fixed bottom-20 right-4 z-40 flex h-[min(560px,70vh)] w-[min(380px,calc(100vw-2rem))] flex-col overflow-hidden rounded-lg border bg-card shadow-lg"
      role="dialog"
      aria-labelledby="floating-chat-widget-title"
      data-testid="floating-chat-panel"
      onKeyDown={onKeyDown}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b bg-card/80 px-3 py-2 shrink-0">
        <h2
          ref={initialFocusRef}
          id="floating-chat-widget-title"
          tabIndex={-1}
          className="flex items-center gap-1.5 rounded-sm text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-fg"
        >
          <MessageCircleIcon className="size-4 text-muted-foreground" />
          Talk to Butlers
        </h2>
        <div className="flex items-center gap-0.5">
          {viewMode === "thread" ? (
            <>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => setViewMode("history")}
                aria-label="Conversation history"
                title="History"
                data-testid="chat-widget-history-button"
              >
                <HistoryIcon />
              </Button>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={handleNewConversation}
                aria-label="New conversation"
                title="New conversation"
                data-testid="chat-widget-new-button"
              >
                <PlusIcon />
              </Button>
            </>
          ) : (
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => setViewMode("thread")}
              aria-label="Back to conversation"
              title="Back"
              data-testid="chat-widget-back-button"
            >
              <ArrowLeftIcon />
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={onClose}
            aria-label="Close chat"
            title="Close"
            data-testid="chat-widget-close-button"
          >
            <XIcon />
          </Button>
        </div>
      </div>

      {viewMode === "history" ? (
        <div className="min-h-0 flex-1 overflow-hidden">
          <ConversationList
            butlerName={WIDGET_BUTLER}
            activeConversationId={activeConversationId}
            collapsible={false}
            onSelectConversation={(id) => {
              abandonCurrentStream();
              setActiveConversationId(id);
              setSendError(null);
              setViewMode("thread");
            }}
            onNewConversation={handleNewConversation}
          />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <ConversationHeader
            butlerName={WIDGET_BUTLER}
            conversation={activeConversation}
            messages={visibleMessages}
            pricingMap={pricingMap}
            routedButler={streaming?.dispatchReceipt?.routedButler}
          />

          {isLoadingMessages && activeConversationId && visibleMessages.length === 0 ? (
            <MessageThreadSkeleton />
          ) : (
            <MessageThread
              messages={visibleMessages}
              streaming={streaming}
              pricingMap={pricingMap}
              conversationId={activeConversationId}
              suppressEmptyState={isMessagesError}
            />
          )}

          {isMessagesError && (
            <ConversationReadError
              label="conversation history"
              onRetry={() => void refetchMessages()}
            />
          )}

          {sendError && (
            <SendErrorBanner
              error={sendError}
              onRetry={(error) => void sendText(error.failedText, error.messageId)}
              onCheckAgain={handleCheckAgain}
              onDismiss={() => setSendError(null)}
            />
          )}

          <MessageInput
            value={inputValue}
            onChange={setInputValue}
            onSend={handleSendClick}
            onStop={handleStop}
            stopPending={streaming?.cancelling ?? false}
            stopAvailable={streaming?.stopReady ?? true}
            stopStatus={
              streaming?.cancelling
                ? "Stopping this turn."
                : streaming?.cancelled
                  ? "This turn was stopped."
                  : streaming?.cancelError
                    ? `Could not stop this turn: ${streaming.cancelError}`
                    : null
            }
            disabled={isLoadingConversations}
            isStreaming={hasActiveRuntime}
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FloatingChatWidget — trigger button + panel toggle, mounted in RootLayout
// ---------------------------------------------------------------------------

export function FloatingChatWidget() {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const restoreTriggerFocusRef = useRef(false);

  useEffect(() => {
    if (!open && restoreTriggerFocusRef.current) {
      restoreTriggerFocusRef.current = false;
      triggerRef.current?.focus();
    }
  }, [open]);

  const closeWidget = useCallback(() => {
    // WidgetPanel unmounts while the trigger is absent, so restore after this
    // render mounts the trigger again rather than focusing a detached opener.
    restoreTriggerFocusRef.current = true;
    setOpen(false);
  }, []);

  // Poll for replies that arrive while the panel is closed (bu-p6ey8.4 —
  // "Unread badge"). Always mounted (unlike WidgetPanel, which unmounts on
  // close) so polling continues regardless of open/closed state.
  const hasUnread = useChatUnreadBadge(WIDGET_BUTLER, open);

  // "Talk to Butlers" cmdk command (bu-86c4c.7 command spine) — opens the
  // widget from anywhere, same pattern as GlobalActionsRegistrar.
  const commands = useMemo<PaletteCommand[]>(
    () => [
      {
        id: "talk-to-butlers",
        label: "Talk to Butlers",
        keywords: ["chat", "switchboard", "message", "conversation"],
        perform: () => setOpen(true),
      },
    ],
    [],
  );
  useRegisterCommands(commands);

  return (
    <>
      {!open && (
        <Button
          ref={triggerRef}
          type="button"
          variant="default"
          // bottom-20 (not bottom-4): the "?" keyboard-shortcuts trigger
          // (ShortcutHints, also mounted globally in RootLayout) occupies
          // fixed bottom-4 right-4 z-50 — sitting here too would put its
          // 32px button exactly under this button's click center and
          // intercept every click. Stacking above it avoids the collision
          // entirely; the panel anchors to the same spot when open.
          className="fixed bottom-20 right-4 z-40 size-12 rounded-full p-0 shadow-lg"
          onClick={() => setOpen(true)}
          aria-label={hasUnread ? "Talk to Butlers (new reply)" : "Talk to Butlers"}
          title="Talk to Butlers"
          data-testid="floating-chat-trigger"
        >
          <MessageCircleIcon className="size-5" />
          {hasUnread && (
            <span
              className="absolute right-1 top-1 size-2.5 rounded-full bg-destructive ring-2 ring-background"
              data-testid="chat-widget-unread-badge"
              aria-hidden="true"
            />
          )}
        </Button>
      )}
      {open && <WidgetPanel onClose={closeWidget} />}
    </>
  );
}

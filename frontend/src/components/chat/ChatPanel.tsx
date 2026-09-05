/**
 * Slide-out chat panel for the butler detail page.
 *
 * Renders as a Sheet with:
 * - Left: ConversationList sidebar (collapsible, localStorage-persisted)
 * - Right: MessageThread + ConversationHeader + MessageInput
 *
 * Features:
 * - SSE stream consumption with AbortController cancellation
 * - Keyboard shortcuts: Ctrl+Shift+Up/Down for conversation quick-switch
 * - Classified send-error banners (offline+retry / timeout+inspect-session),
 *   shared with FloatingChatWidget.tsx via ./send-error.tsx (bu-o0ab2) —
 *   see that module for the design doc's Error handling contract.
 * - Loading skeleton while messages fetch
 */

import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { MessageSquareIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

import {
  cancelConversationMessageTurn,
  createConversation,
  sendMessage,
} from "@/api/index.ts";
import type { CreateConversationRequest, Message, ConversationSummary } from "@/api/types.ts";
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
import { useRegisterShortcut, type ShortcutBinding } from "@/hooks/use-register-shortcut";
import { usePageContextCapture, type PageContextSnapshot } from "@/lib/page-context.tsx";

/**
 * Builds the outgoing message body — mirrors FloatingChatWidget.tsx's
 * `buildMessagePayload` (bu-0ynlk.4). `page_context` is omitted entirely
 * (not sent as an empty object) whenever the ContextChip is detached for
 * this message or the route's contextPolicy resolves to "none".
 */
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

// ---------------------------------------------------------------------------
// ChatPanel inner content (mounted once Sheet is open)
// ---------------------------------------------------------------------------

export interface ChatContentProps {
  butlerName: string;
}

export function ChatContent({ butlerName }: ChatContentProps) {
  const queryClient = useQueryClient();

  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState("");
  // Per-message opt-out for the ContextChip (bu-0ynlk.4) — resets to true
  // after every send so removal only ever applies to the one message it was
  // clicked on.
  const [includeContext, setIncludeContext] = useState(true);
  const capturePageContext = usePageContextCapture();
  const contextPreview = capturePageContext();

  // Local streaming state
  const [streaming, setStreaming] = useState<StreamingState | null>(null);
  // Local messages during / after stream (committed messages from cache)
  const [localMessages, setLocalMessages] = useState<Message[]>([]);
  const localMessagesConversationIdRef = useRef<string | null>(null);
  // Classified SSE/transport send error (offline / timeout / generic) —
  // mirrors FloatingChatWidget's sendError seam, see ./send-error.tsx.
  const [sendError, setSendError] = useState<SendError | null>(null);

  // Pricing is optional decoration: keep the existing null behavior while
  // loading or after an error, with a cache shared by both chat surfaces.
  const { data: pricingMapData } = usePricingMap();
  const pricingMap = pricingMapData ?? null;

  // AbortController for the current SSE stream
  const abortRef = useRef<AbortController | null>(null);
  const interruptedTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeMessageIdRef = useRef<string | null>(null);
  const confirmedStopMessageIdRef = useRef<string | null>(null);

  // Abort any in-flight SSE stream when this component unmounts
  useEffect(() => {
    return () => {
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
      activeMessageIdRef.current = null;
      if (interruptedTimeoutRef.current !== null) {
        clearTimeout(interruptedTimeoutRef.current);
        interruptedTimeoutRef.current = null;
      }
      confirmedStopMessageIdRef.current = null;
    };
  }, []);

  // Fetch conversations list
  const { data: conversationsData, isLoading: isLoadingConversations } =
    useConversations(butlerName);
  const conversations: ConversationSummary[] = useMemo(
    () => conversationsData?.data ?? [],
    [conversationsData],
  );

  // Fetch messages for the active conversation
  const {
    data: messagesData,
    isLoading: isLoadingMessages,
    isError: isMessagesError,
    refetch: refetchMessages,
  } = useConversationMessages(butlerName, activeConversationId);

  // Sync server messages into local state
  // Avoid overwriting optimistic/streaming messages while an SSE stream is active.
  useEffect(() => {
    if (streaming) return;
    // A conversation-key switch can briefly expose `messagesData` as undefined
    // before the next query result lands. Preserve the previous conversation's
    // cached local state for a same-thread retry, but never reconcile it into
    // the newly selected conversation.
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

  // Keyboard shortcut: Ctrl+Shift+Up/Down to switch conversations. Migrated
  // onto the shared page-scoped shortcut registry (bu-qvnce.11), which also
  // publishes it to the '?' help sheet's "On this page" section — this chord
  // previously had zero discoverability outside this source file. Both
  // bindings set `allowWhenSuspended` since the chord is meant to work while
  // the owner is mid-message in MessageInput (a modifier chord, so it can't
  // collide with normal typing) — matching this handler's original
  // no-editable-field-guard behavior.
  function switchConversation(direction: 1 | -1) {
    if (conversations.length === 0) return;
    abandonCurrentStream();
    const idx = conversations.findIndex((c) => c.id === activeConversationId);
    if (direction === -1) {
      const prev = idx <= 0 ? conversations.length - 1 : idx - 1;
      setActiveConversationId(conversations[prev].id);
    } else {
      const next = idx < 0 || idx >= conversations.length - 1 ? 0 : idx + 1;
      setActiveConversationId(conversations[next].id);
    }
  }

  const conversationShortcuts = useMemo<ShortcutBinding[]>(
    () => [
      {
        key: "ArrowUp",
        ctrlKey: true,
        shiftKey: true,
        display: ["Ctrl", "Shift", "↑"],
        description: "Previous conversation",
        handler: () => switchConversation(-1),
        allowWhenSuspended: true,
      },
      {
        key: "ArrowDown",
        ctrlKey: true,
        shiftKey: true,
        display: ["Ctrl", "Shift", "↓"],
        description: "Next conversation",
        handler: () => switchConversation(1),
        allowWhenSuspended: true,
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps -- switchConversation closes over conversations/activeConversationId directly; listing those (what it actually depends on) keeps this memo fresh each render.
    [conversations, activeConversationId],
  );
  useRegisterShortcut(conversationShortcuts);

  // Resume the most recent conversation ONCE per mount (== once per Sheet
  // open, since ChatContent unmounts entirely when the Sheet closes via the
  // `{open && <ChatContent />}` gate in ChatPanel below) — gated by
  // hasResumedRef so a later "New conversation" click (which also sets
  // activeConversationId to null) does not get immediately overridden back
  // to the existing thread by this same effect. Mirrors FloatingChatWidget's
  // identical guard.
  const hasResumedRef = useRef(false);
  useEffect(() => {
    if (hasResumedRef.current) return;
    if (conversations.length === 0) return;
    hasResumedRef.current = true;
    if (activeConversationId == null) {
      setActiveConversationId(conversations[0].id);
    }
  }, [conversations, activeConversationId]);

  // Reset per-butler session state when `butlerName` changes while
  // ChatContent stays mounted. The `{open && <ChatContent />}` gate in
  // ChatPanel below only unmounts ChatContent when the Sheet closes — it
  // does NOT unmount/remount on a butler switch that leaves the Sheet open
  // (e.g. jumping to a different butler's detail page via the EntityFinder
  // Cmd+K palette; the header slot hosting ChatPanel survives Page's
  // loading/loaded transitions for the status-board archetype, see
  // ui/page.tsx). Without this reset, hasResumedRef above would stay
  // latched from the previous butler and silently never auto-resume the
  // newly-viewed butler's most recent conversation.
  const previousButlerNameRef = useRef(butlerName);
  useEffect(() => {
    if (previousButlerNameRef.current === butlerName) return;
    previousButlerNameRef.current = butlerName;
    abortRef.current?.abort();
    abortRef.current = null;
    if (interruptedTimeoutRef.current !== null) {
      clearTimeout(interruptedTimeoutRef.current);
      interruptedTimeoutRef.current = null;
    }
    activeMessageIdRef.current = null;
    confirmedStopMessageIdRef.current = null;
    hasResumedRef.current = false;
    setActiveConversationId(null);
    localMessagesConversationIdRef.current = null;
    setLocalMessages([]);
    setStreaming(null);
    setSendError(null);
  }, [butlerName]);

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
    [butlerName, queryClient],
  );

  // ---------------------------------------------------------------------------
  // SSE stream handler
  // ---------------------------------------------------------------------------

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

      // Optimistic user message
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
              // Update optimistic user message with real conversation_id
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
                  : (event.data as { content?: string })?.content ?? "";
              setStreaming((prev) =>
                prev?.messageId === messageId
                  ? { ...prev, content: prev.content + token, pending: false }
                  : prev,
              );
              break;
            }
            case "message_complete": {
              // Invalidate queries to fetch committed messages
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
          // User cancelled — mark as interrupted
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
          // Non-abort error before or during streaming: clear streaming state
          // and surface the same classified banner FloatingChatWidget shows.
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
    [
      activeConversationId,
      butlerName,
      capturePageContext,
      confirmStoppedTurn,
      includeContext,
      queryClient,
    ],
  );

  function handleSend() {
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
  }

  function handleCheckAgain() {
    setSendError(null);
    if (activeConversationId) {
      void queryClient.invalidateQueries({
        queryKey: conversationKeys.messages(butlerName, activeConversationId),
      });
    }
  }

  const visibleMessages =
    localMessagesConversationIdRef.current === activeConversationId ? localMessages : [];
  const visibleDispatchReceipt =
    streaming && !streaming.cancelling && !streaming.cancelled && !streaming.interrupted
      ? streaming.dispatchReceipt
      : undefined;

  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar */}
      <ConversationList
        butlerName={butlerName}
        activeConversationId={activeConversationId}
        onSelectConversation={(id) => {
          abandonCurrentStream();
          setActiveConversationId(id);
          setSendError(null);
        }}
        onNewConversation={handleNewConversation}
      />

      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        <ConversationHeader
          butlerName={butlerName}
          conversation={activeConversation}
          messages={visibleMessages}
          pricingMap={pricingMap}
          routedButler={visibleDispatchReceipt?.routedButler}
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
          onSend={handleSend}
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
          contextChip={{
            label: contextPreview.label,
            policy: contextPreview.policy,
            payload: contextPreview.context,
            included: includeContext,
            onToggleIncluded: () => setIncludeContext((prev) => !prev),
          }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ChatPanel (Sheet wrapper)
// ---------------------------------------------------------------------------

export interface ChatPanelProps {
  butlerName: string;
  triggerClassName?: string;
  triggerLabel?: string;
  showTriggerIcon?: boolean;
}

export function ChatPanel({
  butlerName,
  triggerClassName,
  triggerLabel = "Chat",
  showTriggerIcon = true,
}: ChatPanelProps) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className={triggerClassName ?? "gap-1.5"}
        onClick={() => setOpen(true)}
      >
        {showTriggerIcon ? <MessageSquareIcon className="size-4" /> : null}
        {triggerLabel}
      </Button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent
          side="right"
          showCloseButton={true}
          className="w-full sm:max-w-[480px] p-0 flex flex-col overflow-hidden"
        >
          <SheetHeader className="px-4 py-3 border-b shrink-0">
            <SheetTitle className="text-base">Chat with {butlerName}</SheetTitle>
          </SheetHeader>

          <div className="flex-1 min-h-0 overflow-hidden">
            {open && <ChatContent butlerName={butlerName} />}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}

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

import { cancelConversationTurn, createConversation, sendMessage } from "@/api/index.ts";
import type { Message, ConversationSummary } from "@/api/types.ts";
import { consumeSseStream } from "./sse-utils.ts";
import { ConversationList } from "./ConversationList.tsx";
import { ConversationHeader } from "./ConversationHeader.tsx";
import { ConversationReadError } from "./ConversationReadError.tsx";
import { MessageThread, MessageThreadSkeleton } from "./MessageThread.tsx";
import type { StreamingState } from "./MessageThread.tsx";
import { MessageInput } from "./MessageInput.tsx";
import { SendErrorBanner } from "./send-error.tsx";
import { classifySendError, type SendError } from "./send-error-utils.ts";
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

  // Abort any in-flight SSE stream when this component unmounts
  useEffect(() => {
    return () => {
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
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
    hasResumedRef.current = false;
    setActiveConversationId(null);
    localMessagesConversationIdRef.current = null;
    setLocalMessages([]);
    setStreaming(null);
    setSendError(null);
  }, [butlerName]);

  const activeConversation = conversations.find((c) => c.id === activeConversationId) ?? null;
  const isStreaming = streaming !== null;

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
      const controller = new AbortController();
      abortRef.current = controller;

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
        content: "",
        pending: true,
        interrupted: false,
      });

      try {
        const response = isNew
          ? await createConversation(
              butlerName,
              { message: trimmed, message_id: messageId },
              controller.signal,
            )
          : await sendMessage(
              butlerName,
              activeConversationId!,
              { message: trimmed, message_id: messageId },
              controller.signal,
            );

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        await consumeSseStream(response, (event) => {
          switch (event.event) {
            case "conversation_created": {
              // Backend emits `conversation_id` (see routers/conversations.py
              // _stream_conversation_response) — NOT `id`.
              const data = event.data as { conversation_id: string; title?: string | null };
              currentConversationId = data.conversation_id;
              setActiveConversationId(data.conversation_id);
              setStreaming((prev) =>
                prev ? { ...prev, conversationId: data.conversation_id } : null,
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
                prev ? { ...prev, content: prev.content + token, pending: false } : null,
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
              setStreaming(null);
              break;
            }
            case "error": {
              setSendError(classifySendError(event.data, trimmed, messageId));
              setStreaming(null);
              break;
            }
            case "done":
              setStreaming(null);
              break;
          }
        });
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") {
          // User cancelled — mark as interrupted
          setStreaming((prev) =>
            prev ? { ...prev, interrupted: true, pending: false } : null,
          );
          setTimeout(() => setStreaming(null), 1500);
        } else {
          // Non-abort error before or during streaming: clear streaming state
          // and surface the same classified banner FloatingChatWidget shows.
          setStreaming(null);
          setSendError({
            kind: "generic",
            message: "Failed to send message.",
            failedText: trimmed,
            messageId,
          });
        }
      }
    },
    [activeConversationId, butlerName, queryClient],
  );

  function handleSend() {
    const text = inputValue.trim();
    if (!text) return;
    setInputValue("");
    void sendText(text);
  }

  async function handleStop() {
    if (!streaming || streaming.cancelling) return;
    const conversationId = streaming.conversationId;
    if (!conversationId || conversationId === "pending") {
      // No conversation exists server-side yet — nothing to cancel remotely.
      abortRef.current?.abort();
      return;
    }

    setStreaming((prev) => (prev ? { ...prev, cancelling: true, cancelError: null } : prev));
    try {
      const result = await cancelConversationTurn(butlerName, conversationId);
      if (!result.cancelled) {
        if (result.already_finished) {
          // The turn already finished on its own — quietly stop watching.
          // Never claim we stopped something that had already ended.
          abortRef.current?.abort();
          setStreaming(null);
          return;
        }
        setStreaming((prev) =>
          prev
            ? {
                ...prev,
                cancelling: false,
                cancelError: result.message ?? "Could not stop. Try again.",
              }
            : prev,
        );
        return;
      }
      abortRef.current?.abort();
      setStreaming((prev) => (prev ? { ...prev, cancelling: false, cancelled: true } : prev));
    } catch {
      setStreaming((prev) =>
        prev
          ? { ...prev, cancelling: false, cancelError: "Could not stop. Try again." }
          : prev,
      );
    }
  }

  function handleNewConversation() {
    setActiveConversationId(null);
    localMessagesConversationIdRef.current = null;
    setLocalMessages([]);
    setStreaming(null);
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

  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar */}
      <ConversationList
        butlerName={butlerName}
        activeConversationId={activeConversationId}
        onSelectConversation={(id) => {
          setActiveConversationId(id);
          setStreaming(null);
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
          onSend={handleSend}
          onStop={handleStop}
          stopPending={streaming?.cancelling ?? false}
          disabled={isLoadingConversations}
          isStreaming={isStreaming}
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

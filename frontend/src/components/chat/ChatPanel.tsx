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

import { createConversation, sendMessage } from "@/api/index.ts";
import { fetchPricingMap } from "@/api/client.ts";
import type { Message, ConversationSummary, PricingMap } from "@/api/types.ts";
import { consumeSseStream } from "./sse-utils.ts";
import { ConversationList } from "./ConversationList.tsx";
import { ConversationHeader } from "./ConversationHeader.tsx";
import { MessageThread, MessageThreadSkeleton } from "./MessageThread.tsx";
import type { StreamingState } from "./MessageThread.tsx";
import { MessageInput } from "./MessageInput.tsx";
import { SendErrorBanner } from "./send-error.tsx";
import { classifySendError, type SendError } from "./send-error-utils.ts";
import {
  conversationKeys,
  useConversations,
  useConversationMessages,
} from "@/hooks/use-conversations.ts";

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
  // Classified SSE/transport send error (offline / timeout / generic) —
  // mirrors FloatingChatWidget's sendError seam, see ./send-error.tsx.
  const [sendError, setSendError] = useState<SendError | null>(null);

  // Pricing map for cost estimation
  const [pricingMap, setPricingMap] = useState<PricingMap | null>(null);

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

  // Load pricing map once
  useEffect(() => {
    fetchPricingMap()
      .then((pm) => setPricingMap(pm.data))
      .catch(() => {
        /* pricing is optional */
      });
  }, []);

  // Fetch conversations list
  const { data: conversationsData, isLoading: isLoadingConversations } =
    useConversations(butlerName);
  const conversations: ConversationSummary[] = useMemo(
    () => conversationsData?.data ?? [],
    [conversationsData],
  );

  // Fetch messages for the active conversation
  const { data: messagesData, isLoading: isLoadingMessages } = useConversationMessages(
    butlerName,
    activeConversationId,
  );

  // Sync server messages into local state
  // Avoid overwriting optimistic/streaming messages while an SSE stream is active.
  useEffect(() => {
    if (streaming) return;
    const msgs = messagesData?.data ?? [];
    setLocalMessages(msgs);
  }, [messagesData, streaming]);

  // Keyboard shortcut: Ctrl+Shift+Up/Down to switch conversations
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (!e.ctrlKey || !e.shiftKey) return;
      if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
      e.preventDefault();

      if (conversations.length === 0) return;
      const idx = conversations.findIndex((c) => c.id === activeConversationId);
      if (e.key === "ArrowUp") {
        const prev = idx <= 0 ? conversations.length - 1 : idx - 1;
        setActiveConversationId(conversations[prev].id);
      } else {
        const next = idx < 0 || idx >= conversations.length - 1 ? 0 : idx + 1;
        setActiveConversationId(conversations[next].id);
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [conversations, activeConversationId]);

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
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      setSendError(null);
      const isNew = activeConversationId == null;
      const controller = new AbortController();
      abortRef.current = controller;

      // Optimistic user message
      const userMessage: Message = {
        id: `optimistic-user-${Date.now()}`,
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
      setLocalMessages((prev) => [...prev, userMessage]);

      let currentConversationId = activeConversationId;

      setStreaming({
        conversationId: currentConversationId ?? "pending",
        content: "",
        pending: true,
        interrupted: false,
      });

      try {
        const response = isNew
          ? await createConversation(butlerName, { message: trimmed }, controller.signal)
          : await sendMessage(
              butlerName,
              activeConversationId!,
              { message: trimmed },
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
              setLocalMessages((prev) =>
                prev.map((m) =>
                  m.id === userMessage.id ? { ...m, conversation_id: data.conversation_id } : m,
                ),
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
              setSendError(classifySendError(event.data, trimmed));
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
            message: "There was a problem sending your message. Please try again.",
            failedText: trimmed,
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

  function handleStop() {
    abortRef.current?.abort();
  }

  function handleNewConversation() {
    setActiveConversationId(null);
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
          messages={localMessages}
          pricingMap={pricingMap}
        />

        {isLoadingMessages && activeConversationId ? (
          <MessageThreadSkeleton />
        ) : (
          <MessageThread
            messages={localMessages}
            streaming={streaming}
            pricingMap={pricingMap}
            conversationId={activeConversationId}
          />
        )}

        {sendError && (
          <SendErrorBanner
            error={sendError}
            onRetry={(text) => void sendText(text)}
            onCheckAgain={handleCheckAgain}
            onDismiss={() => setSendError(null)}
          />
        )}

        <MessageInput
          value={inputValue}
          onChange={setInputValue}
          onSend={handleSend}
          onStop={handleStop}
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

/**
 * Slide-out chat panel for the butler detail page.
 *
 * Renders as a Sheet with:
 * - Left: ConversationList sidebar (collapsible)
 * - Right: MessageThread + ConversationHeader + MessageInput
 *
 * SSE streaming is handled inline; messages are appended to local state
 * during streaming and the server's committed message list is refetched
 * after `message_complete`.
 */

import { useState, useCallback, useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { MessageSquareIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";

import { createConversation, sendMessage } from "@/api/index.ts";
import { fetchPricingMap } from "@/api/client.ts";
import type { Message, ConversationSummary, PricingMap } from "@/api/types.ts";
import { consumeSseStream } from "./sse-utils.ts";
import { ConversationList } from "./ConversationList.tsx";
import { ConversationHeader } from "./ConversationHeader.tsx";
import { MessageThread } from "./MessageThread.tsx";
import type { StreamingState } from "./MessageThread.tsx";
import { MessageInput } from "./MessageInput.tsx";
import {
  conversationKeys,
  useConversations,
  useConversationMessages,
} from "@/hooks/use-conversations.ts";

// ---------------------------------------------------------------------------
// ChatPanel inner content (mounted once Sheet is open)
// ---------------------------------------------------------------------------

interface ChatContentProps {
  butlerName: string;
}

function ChatContent({ butlerName }: ChatContentProps) {
  const queryClient = useQueryClient();

  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState("");

  // Local streaming state
  const [streaming, setStreaming] = useState<StreamingState | null>(null);
  // Local messages during / after stream (committed messages from cache)
  const [localMessages, setLocalMessages] = useState<Message[]>([]);

  // Pricing map for cost estimation
  const [pricingMap, setPricingMap] = useState<PricingMap | null>(null);

  // AbortController for the current SSE stream
  const abortRef = useRef<AbortController | null>(null);

  // Load pricing map once
  useEffect(() => {
    fetchPricingMap()
      .then((pm) => setPricingMap(pm.data))
      .catch(() => {/* pricing is optional */});
  }, []);

  // Fetch conversations list
  const { data: conversationsData, isLoading: isLoadingConversations } =
    useConversations(butlerName);
  const conversations: ConversationSummary[] = conversationsData?.data ?? [];

  // Fetch messages for the active conversation
  const { data: messagesData, isLoading: isLoadingMessages } =
    useConversationMessages(butlerName, activeConversationId);

  // Sync server messages into local state
  useEffect(() => {
    const msgs = messagesData?.data ?? [];
    setLocalMessages(msgs);
  }, [messagesData]);

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

  // Auto-select first conversation on initial load
  useEffect(() => {
    if (activeConversationId == null && conversations.length > 0) {
      setActiveConversationId(conversations[0].id);
    }
  }, [conversations, activeConversationId]);

  const activeConversation = conversations.find((c) => c.id === activeConversationId) ?? null;
  const isStreaming = streaming !== null;

  // ---------------------------------------------------------------------------
  // SSE stream handler
  // ---------------------------------------------------------------------------

  const handleSend = useCallback(async () => {
    const text = inputValue.trim();
    if (!text) return;

    setInputValue("");

    const isNew = activeConversationId == null;
    const controller = new AbortController();
    abortRef.current = controller;

    // Optimistic user message
    const userMessage: Message = {
      id: `optimistic-user-${Date.now()}`,
      conversation_id: activeConversationId ?? "",
      role: "user",
      content: text,
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
        ? await createConversation(butlerName, { message: text }, controller.signal)
        : await sendMessage(
            butlerName,
            activeConversationId!,
            { message: text },
            controller.signal,
          );

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      await consumeSseStream(response, (event) => {
        switch (event.event) {
          case "conversation_created": {
            const data = event.data as { id: string; title?: string | null };
            currentConversationId = data.id;
            setActiveConversationId(data.id);
            setStreaming((prev) =>
              prev ? { ...prev, conversationId: data.id } : null,
            );
            // Update optimistic user message with real conversation_id
            setLocalMessages((prev) =>
              prev.map((m) =>
                m.id === userMessage.id
                  ? { ...m, conversation_id: data.id }
                  : m,
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
              prev
                ? { ...prev, content: prev.content + token, pending: false }
                : null,
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
            const errMsg =
              typeof event.data === "string"
                ? event.data
                : (event.data as { message?: string })?.message ?? "Unknown error";
            // Append error assistant message locally
            const errAssistant: Message = {
              id: `optimistic-err-${Date.now()}`,
              conversation_id: currentConversationId ?? "",
              role: "assistant",
              content: "",
              tool_calls: null,
              error: errMsg,
              model: null,
              input_tokens: null,
              output_tokens: null,
              duration_ms: null,
              session_id: null,
              request_id: null,
              created_at: new Date().toISOString(),
            };
            setLocalMessages((prev) => [...prev, errAssistant]);
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
        setStreaming(null);
      }
    }
  }, [inputValue, activeConversationId, butlerName, queryClient]);

  function handleStop() {
    abortRef.current?.abort();
  }

  function handleNewConversation() {
    setActiveConversationId(null);
    setLocalMessages([]);
    setStreaming(null);
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
          <div className="flex-1 p-4 space-y-3">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className={`h-10 ${i % 2 === 0 ? "w-3/4" : "w-1/2 ml-auto"}`} />
            ))}
          </div>
        ) : (
          <MessageThread
            messages={localMessages}
            streaming={streaming}
            pricingMap={pricingMap}
            conversationId={activeConversationId}
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
}

export function ChatPanel({ butlerName }: ChatPanelProps) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="gap-1.5"
        onClick={() => setOpen(true)}
      >
        <MessageSquareIcon className="size-4" />
        Chat
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

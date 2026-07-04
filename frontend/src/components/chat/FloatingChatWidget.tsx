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
 *     section. ChatPanel.tsx does not yet make this distinction; see the
 *     PR description for a follow-up on aligning it.
 *
 * Lifecycle: the panel's content only mounts while `open` (mirrors
 * ChatPanel's `{open && <ChatContent/>}` gate), so every reopen re-fetches
 * the conversation list and re-selects the most recently updated *active*
 * conversation (list is server-ordered `updated_at DESC`) — "reopening
 * resumes the most recent open conversation" falls out of that refetch,
 * no extra persistence needed.
 *
 * Out of scope (sibling bu-p6ey8.4): page-context capture and the unread
 * badge. `buildMessagePayload()` below is the single choke point both
 * `createConversation`/`sendMessage` calls go through — the seam that bead
 * extends with `page_context` without touching call sites.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftIcon,
  ExternalLinkIcon,
  HistoryIcon,
  MessageCircleIcon,
  PlusIcon,
  RefreshCwIcon,
  XIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { createConversation, sendMessage } from "@/api/index.ts";
import { fetchPricingMap } from "@/api/client.ts";
import type {
  ConversationSseErrorData,
  ConversationSummary,
  CreateConversationRequest,
  Message,
  PricingMap,
} from "@/api/types.ts";
import { consumeSseStream } from "./sse-utils.ts";
import { ConversationList } from "./ConversationList.tsx";
import { ConversationHeader } from "./ConversationHeader.tsx";
import { MessageThread, MessageThreadSkeleton } from "./MessageThread.tsx";
import type { StreamingState } from "./MessageThread.tsx";
import { MessageInput } from "./MessageInput.tsx";
import {
  conversationKeys,
  useConversations,
  useConversationMessages,
} from "@/hooks/use-conversations.ts";
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
 * `sendMessage`. This is the single choke point the widget uses to submit a
 * message — bu-p6ey8.4's PageContextProvider attaches `page_context` here
 * (see `CreateConversationRequest`/`SendMessageRequest.page_context` in
 * api/types.ts) without any call site needing to change.
 */
function buildMessagePayload(message: string): CreateConversationRequest {
  return { message };
}

// ---------------------------------------------------------------------------
// Send-error classification (design doc § Error handling)
// ---------------------------------------------------------------------------

type SendError =
  | { kind: "offline"; message: string; failedText: string }
  | { kind: "timeout"; message: string; sessionId: string | null }
  | { kind: "generic"; message: string; failedText: string };

function classifySendError(data: unknown, failedText: string): SendError {
  const errData = (typeof data === "object" && data !== null ? data : {}) as ConversationSseErrorData;
  const message =
    errData.message ?? (typeof data === "string" ? data : "Something went wrong.");

  if (errData.code === "SESSION_TIMEOUT") {
    return { kind: "timeout", message, sessionId: errData.session_id ?? null };
  }
  if (errData.code === "SWITCHBOARD_UNAVAILABLE") {
    return { kind: "offline", message, failedText };
  }
  return { kind: "generic", message, failedText };
}

// ---------------------------------------------------------------------------
// Send-error banner
// ---------------------------------------------------------------------------

interface SendErrorBannerProps {
  error: SendError;
  onRetry: (text: string) => void;
  onCheckAgain: () => void;
  onDismiss: () => void;
}

function SendErrorBanner({ error, onRetry, onCheckAgain, onDismiss }: SendErrorBannerProps) {
  if (error.kind === "timeout") {
    return (
      <div
        className="flex items-center justify-between gap-2 border-t bg-muted/40 px-3 py-2 text-xs"
        data-testid="chat-widget-timeout-banner"
      >
        <span className="text-muted-foreground">{error.message}</span>
        <div className="flex shrink-0 items-center gap-2">
          {error.sessionId && (
            <a
              href={`/sessions/${error.sessionId}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline"
              data-testid="chat-widget-timeout-session-link"
            >
              Inspect session
              <ExternalLinkIcon className="size-3" />
            </a>
          )}
          <Button size="xs" variant="outline" onClick={onCheckAgain}>
            <RefreshCwIcon className="size-3" />
            Check again
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex items-center justify-between gap-2 border-t bg-muted/40 px-3 py-2 text-xs"
      data-testid="chat-widget-error-banner"
    >
      <span className="text-destructive">{error.message}</span>
      <div className="flex shrink-0 items-center gap-2">
        <Button size="xs" variant="outline" onClick={() => onRetry(error.failedText)}>
          Retry
        </Button>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="text-muted-foreground hover:text-foreground"
        >
          <XIcon className="size-3" />
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// WidgetPanel — mounted only while the widget is open
// ---------------------------------------------------------------------------

interface WidgetPanelProps {
  onClose: () => void;
}

function WidgetPanel({ onClose }: WidgetPanelProps) {
  const queryClient = useQueryClient();

  const [viewMode, setViewMode] = useState<"thread" | "history">("thread");
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState("");
  const [streaming, setStreaming] = useState<StreamingState | null>(null);
  const [localMessages, setLocalMessages] = useState<Message[]>([]);
  const [sendError, setSendError] = useState<SendError | null>(null);
  const [pricingMap, setPricingMap] = useState<PricingMap | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const interruptedTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
      if (interruptedTimeoutRef.current !== null) {
        clearTimeout(interruptedTimeoutRef.current);
        interruptedTimeoutRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    fetchPricingMap()
      .then((pm) => setPricingMap(pm.data))
      .catch(() => {
        /* pricing is optional */
      });
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

  const { data: messagesData, isLoading: isLoadingMessages } = useConversationMessages(
    WIDGET_BUTLER,
    activeConversationId,
  );

  useEffect(() => {
    if (streaming) return;
    // Guard against the transient `messagesData === undefined` window that
    // TanStack Query passes through while refetching after switching
    // `activeConversationId` (staleTime: 0 means every switch refetches).
    // Without this guard, the query-loading gap briefly clears
    // `localMessages` to `[]`, flashing an empty thread before the real
    // messages land.
    if (messagesData?.data) {
      setLocalMessages(messagesData.data);
    }
  }, [messagesData, streaming]);

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

  const sendText = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      setSendError(null);
      const isNew = activeConversationId == null;
      const controller = new AbortController();
      abortRef.current = controller;

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
          ? await createConversation(WIDGET_BUTLER, buildMessagePayload(trimmed), controller.signal)
          : await sendMessage(
              WIDGET_BUTLER,
              activeConversationId!,
              buildMessagePayload(trimmed),
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
                  : ((event.data as { content?: string })?.content ?? "");
              setStreaming((prev) =>
                prev ? { ...prev, content: prev.content + token, pending: false } : null,
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
          setStreaming((prev) => (prev ? { ...prev, interrupted: true, pending: false } : null));
          interruptedTimeoutRef.current = setTimeout(() => {
            interruptedTimeoutRef.current = null;
            setStreaming(null);
          }, 1500);
        } else {
          setStreaming(null);
          setSendError({
            kind: "generic",
            message: "There was a problem sending your message. Please try again.",
            failedText: trimmed,
          });
        }
      }
    },
    [activeConversationId, queryClient],
  );

  function handleSendClick() {
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

  return (
    <div
      className="fixed bottom-20 right-4 z-40 flex h-[min(560px,70vh)] w-[min(380px,calc(100vw-2rem))] flex-col overflow-hidden rounded-lg border bg-card shadow-lg"
      role="dialog"
      aria-label="Talk to Butlers"
      data-testid="floating-chat-panel"
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b bg-card/80 px-3 py-2 shrink-0">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <MessageCircleIcon className="size-4 text-muted-foreground" />
          Talk to Butlers
        </div>
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
              setActiveConversationId(id);
              setStreaming(null);
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
            onSend={handleSendClick}
            onStop={handleStop}
            disabled={isLoadingConversations}
            isStreaming={isStreaming}
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
          aria-label="Talk to Butlers"
          title="Talk to Butlers"
          data-testid="floating-chat-trigger"
        >
          <MessageCircleIcon className="size-5" />
        </Button>
      )}
      {open && <WidgetPanel onClose={() => setOpen(false)} />}
    </>
  );
}

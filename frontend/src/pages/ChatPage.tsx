/**
 * Full-page chat posture (bu-0ynlk.11): `/chat` and `/chat/:conversationId`.
 *
 * A deliberately separate composition from `ChatDock.tsx`/`FloatingChatWidget.tsx`
 * — not a copy — because a deep-linked conversation can belong to ANY butler
 * (the copy-link button on every assistant message, wherever it renders,
 * points here), whereas the dock/popover only ever show Switchboard-routed
 * threads. `:conversationId` is resolved cross-butler via
 * `GET /api/conversations/{id}` (`useConversationById`) before the actual
 * per-butler messages fetch/turn hook can start; a bare `/chat` defaults to
 * `WIDGET_BUTLER` (same "start a new Switchboard chat" affordance as the
 * dock/popover's New button).
 *
 * Shares the same `useConversationTurn()` hook as every other chat surface
 * (no separate send/stream/stop state machine here) but does NOT mirror an
 * in-flight turn started in the dock/popover live — each mounted instance
 * owns its own turn state, same as the dock and popover already do with each
 * other. Cross-posture live mirroring is out of scope (streaming-architecture
 * territory, tracked separately).
 *
 * Non-goals here (per the bead's dossier): no message-search box (that is
 * move .9's primitive, already wired into `ConversationList`, deliberately
 * not reused here since it would pull that search box in); no composer
 * rebuild (.14); no markdown rendering (.12).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { PlusIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { cn } from "@/lib/utils";
import type { ConversationSummary } from "@/api/types.ts";
import { ApiError } from "@/api/index.ts";
import { ConversationHeader } from "@/components/chat/ConversationHeader.tsx";
import { ConversationReadError } from "@/components/chat/ConversationReadError.tsx";
import { MessageThread, MessageThreadSkeleton } from "@/components/chat/MessageThread.tsx";
import { MessageInput } from "@/components/chat/MessageInput.tsx";
import { SendErrorBanner } from "@/components/chat/send-error.tsx";
import { WIDGET_BUTLER } from "@/components/chat/chat-constants.ts";
import { scrollToMessageAnchor } from "@/components/chat/message-id.ts";
import {
  useConversations,
  useConversationById,
  useConversationMessages,
} from "@/hooks/use-conversations.ts";
import { usePricingMap } from "@/hooks/use-pricing-map.ts";
import { useConversationTurn } from "@/hooks/use-conversation-turn.ts";

export default function ChatPage() {
  const { conversationId: routeConversationId } = useParams<{ conversationId?: string }>();
  const navigate = useNavigate();
  const routeId = routeConversationId ?? null;

  const [activeConversationId, setActiveConversationId] = useState<string | null>(routeId);

  // A route param change while ChatPage stays mounted (sidebar click, cmdk
  // recall, copy-link navigation) must resync local turn state.
  useEffect(() => {
    setActiveConversationId(routeId);
  }, [routeId]);

  const {
    data: conversationDetail,
    isLoading: isLoadingDetail,
    isError: isDetailError,
    error: detailError,
  } = useConversationById(routeId);

  const notFound =
    routeId != null &&
    isDetailError &&
    detailError instanceof ApiError &&
    detailError.status === 404;

  // WIDGET_BUTLER for a not-yet-created conversation (bare /chat); otherwise
  // whichever butler the cross-butler lookup resolved — any butler's
  // conversation can be deep-linked here, unlike the dock/popover.
  const butlerName = routeId != null ? (conversationDetail?.butler_name ?? null) : WIDGET_BUTLER;

  const [inputValue, setInputValue] = useState("");
  const { data: pricingMapData } = usePricingMap();
  const pricingMap = pricingMapData ?? null;

  const { data: recentConversationsData } = useConversations(WIDGET_BUTLER);
  const recentConversations: ConversationSummary[] = useMemo(
    () => recentConversationsData?.data ?? [],
    [recentConversationsData],
  );

  const {
    data: messagesData,
    isLoading: isLoadingMessages,
    isError: isMessagesError,
    refetch: refetchMessages,
  } = useConversationMessages(butlerName ?? "", activeConversationId);

  const {
    streaming,
    visibleMessages,
    sendError,
    setSendError,
    hasActiveRuntime,
    contextPreview,
    includeContext,
    toggleIncludeContext,
    sendText,
    handleStop,
    abandonCurrentStream,
    resetTurnState,
  } = useConversationTurn({
    butlerName: butlerName ?? WIDGET_BUTLER,
    activeConversationId,
    setActiveConversationId,
    messagesData,
  });

  // A brand-new conversation (bare /chat) gets its id from the first send;
  // reflect it in the URL so a refresh or copy-link keeps working.
  useEffect(() => {
    if (activeConversationId && activeConversationId !== routeId) {
      navigate(`/chat/${activeConversationId}`, { replace: true });
    }
  }, [activeConversationId, routeId, navigate]);

  // Anchor scroll-on-load (#m-{messageId}): focuses once the thread has
  // finished loading. A message that isn't there (deleted, or the fragment
  // is stale) is a silent no-op — the thread still renders normally.
  const scrolledForRef = useRef<string | null>(null);
  useEffect(() => {
    if (isLoadingMessages) return;
    const hash = window.location.hash;
    if (!hash.startsWith("#m-")) return;
    if (scrolledForRef.current === hash) return;
    scrolledForRef.current = hash;
    scrollToMessageAnchor(hash.slice(3));
  }, [isLoadingMessages, visibleMessages]);

  function handleSend() {
    const text = inputValue.trim();
    if (!text) return;
    setInputValue("");
    void sendText(text);
  }

  function handleNewConversation() {
    abandonCurrentStream();
    setActiveConversationId(null);
    resetTurnState();
    navigate("/chat", { replace: true });
  }

  function handleCheckAgain() {
    setSendError(null);
    if (activeConversationId) void refetchMessages();
  }

  const activeConversation =
    conversationDetail ?? recentConversations.find((c) => c.id === activeConversationId) ?? null;

  const visibleDispatchReceipt =
    streaming && !streaming.cancelling && !streaming.cancelled && !streaming.interrupted
      ? streaming.dispatchReceipt
      : undefined;

  if (notFound) {
    return (
      <div className="flex h-full flex-1 items-center justify-center" data-testid="chat-page-not-found">
        <EmptyState
          title="Conversation not found"
          description="This conversation may have been deleted, or the link is incorrect."
          action={
            <Button variant="outline" onClick={() => navigate("/chat")}>
              Start a new conversation
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-1 overflow-hidden" data-testid="chat-page">
      <aside className="hidden w-64 shrink-0 flex-col overflow-y-auto border-r border-border p-2 md:flex">
        <div className="mb-2 flex items-center justify-between px-1">
          <h2 className="text-sm font-medium text-muted-foreground">Recent</h2>
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={handleNewConversation}
            aria-label="New conversation"
            title="New conversation"
          >
            <PlusIcon />
          </Button>
        </div>
        {recentConversations.map((conversation) => (
          <button
            key={conversation.id}
            type="button"
            onClick={() => navigate(`/chat/${conversation.id}`)}
            className={cn(
              "truncate rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted",
              conversation.id === activeConversationId
                ? "bg-accent text-accent-foreground"
                : "text-foreground",
            )}
          >
            {conversation.title ?? "Untitled conversation"}
          </button>
        ))}
      </aside>

      <div className="flex min-h-0 flex-1 flex-col">
        <ConversationHeader
          butlerName={butlerName ?? WIDGET_BUTLER}
          conversation={activeConversation}
          messages={visibleMessages}
          pricingMap={pricingMap}
          routedButler={visibleDispatchReceipt?.routedButler}
        />

        {(isLoadingDetail || (isLoadingMessages && !!activeConversationId)) &&
        visibleMessages.length === 0 ? (
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
          disabled={isLoadingDetail}
          isStreaming={hasActiveRuntime}
          contextChip={{
            label: contextPreview.label,
            policy: contextPreview.policy,
            payload: contextPreview.context,
            included: includeContext,
            onToggleIncluded: toggleIncludeContext,
          }}
        />
      </div>
    </div>
  );
}

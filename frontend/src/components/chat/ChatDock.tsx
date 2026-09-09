/**
 * Docked chat rail (bu-0ynlk.11) — the >= xl default posture for the global
 * "Talk to Butlers" surface. Mounted by RootLayout as Shell's `chatDock`
 * prop (a sibling column of `<main>`, never an overlay — see Shell.tsx).
 *
 * Shares the Switchboard-routed conversation with the popover
 * (FloatingChatWidget.tsx) via the same WIDGET_BUTLER conversations/messages
 * cache and the same `useConversationTurn()` hook shape; each mounted
 * instance manages its own `activeConversationId` and resume-on-mount
 * (mirroring the popover's `hasResumedRef` pattern) since the dock and the
 * popover are never both mounted at once (RootLayout picks exactly one
 * posture per viewport — see `layouts/RootLayout.tsx`).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { MessageCircleIcon, Maximize2Icon, PlusIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ConversationSummary } from "@/api/types.ts";
import { ConversationHeader } from "./ConversationHeader.tsx";
import { ConversationReadError } from "./ConversationReadError.tsx";
import { MessageThread, MessageThreadSkeleton } from "./MessageThread.tsx";
import { MessageInput } from "./MessageInput.tsx";
import { SendErrorBanner } from "./send-error.tsx";
import { useConversations, useConversationMessages } from "@/hooks/use-conversations.ts";
import { usePricingMap } from "@/hooks/use-pricing-map.ts";
import { useConversationTurn } from "@/hooks/use-conversation-turn.ts";
import { readNumberSetting, writeNumberSetting } from "@/lib/local-settings.ts";
import { WIDGET_BUTLER } from "./chat-constants.ts";

const DOCK_WIDTH_KEY = "butlers.chat-dock-width";
const DOCK_MIN_WIDTH = 360;
const DOCK_MAX_WIDTH = 560;
const DOCK_DEFAULT_WIDTH = 400;

function clampWidth(width: number): number {
  return Math.min(DOCK_MAX_WIDTH, Math.max(DOCK_MIN_WIDTH, width));
}

export interface ChatDockProps {
  /** Collapses the dock back to the popover posture (persisted by the caller). */
  onClose: () => void;
}

export function ChatDock({ onClose }: ChatDockProps) {
  const navigate = useNavigate();
  const [width, setWidth] = useState(() => clampWidth(readNumberSetting(DOCK_WIDTH_KEY, DOCK_DEFAULT_WIDTH)));
  const resizingRef = useRef(false);

  useEffect(() => {
    function handlePointerMove(event: PointerEvent) {
      if (!resizingRef.current) return;
      // The dock sits at the right edge of the viewport, so its width is the
      // distance from the pointer to the right edge (dragging left widens it).
      setWidth(clampWidth(window.innerWidth - event.clientX));
    }
    function handlePointerUp() {
      if (!resizingRef.current) return;
      resizingRef.current = false;
      setWidth((current) => {
        writeNumberSetting(DOCK_WIDTH_KEY, current);
        return current;
      });
    }
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };
  }, []);

  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState("");
  const { data: pricingMapData } = usePricingMap();
  const pricingMap = pricingMapData ?? null;

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
    butlerName: WIDGET_BUTLER,
    activeConversationId,
    setActiveConversationId,
    messagesData,
  });

  // Resume the most recently updated open conversation once per mount, same
  // contract as the popover's WidgetPanel.
  const hasResumedRef = useRef(false);
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (hasResumedRef.current) return;
    if (conversations.length === 0) return;
    hasResumedRef.current = true;
    if (activeConversationId == null) {
      setActiveConversationId(conversations[0].id);
    }
  }, [conversations, activeConversationId]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const activeConversation = conversations.find((c) => c.id === activeConversationId) ?? null;

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
  }

  function handleCheckAgain() {
    setSendError(null);
    if (activeConversationId) void refetchMessages();
  }

  function openInFullPage() {
    navigate(activeConversationId ? `/chat/${activeConversationId}` : "/chat");
  }

  const visibleDispatchReceipt =
    streaming && !streaming.cancelling && !streaming.cancelled && !streaming.interrupted
      ? streaming.dispatchReceipt
      : undefined;

  return (
    <div
      className="relative flex h-full flex-col overflow-hidden"
      style={{ width, minWidth: DOCK_MIN_WIDTH, maxWidth: DOCK_MAX_WIDTH }}
      data-testid="chat-dock"
    >
      {/* Resize handle — drag left/right to resize within 360-560px.
          role="separator" used as a resize splitter is a standard WAI-ARIA
          pattern (keyboard-adjustable via arrow keys below); the static rule
          allowlist does not recognize it as interactive. */}
      {/* eslint-disable jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-noninteractive-tabindex */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize chat dock"
        tabIndex={0}
        className="absolute -left-1 top-0 h-full w-2 cursor-col-resize"
        onPointerDown={(event) => {
          resizingRef.current = true;
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") setWidth((w) => clampWidth(w + 16));
          if (event.key === "ArrowRight") setWidth((w) => clampWidth(w - 16));
        }}
      />
      {/* eslint-enable jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-noninteractive-tabindex */}

      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2 shrink-0">
        <h2 className="flex items-center gap-1.5 text-sm font-medium">
          <MessageCircleIcon className="size-4 text-muted-foreground" />
          Talk to Butlers
        </h2>
        <div className="flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={handleNewConversation}
            aria-label="New conversation"
            title="New conversation"
            data-testid="chat-dock-new-button"
          >
            <PlusIcon />
          </Button>
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={openInFullPage}
            aria-label="Open in full page"
            title="Open in full page"
            data-testid="chat-dock-open-full-page"
          >
            <Maximize2Icon />
          </Button>
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={onClose}
            aria-label="Collapse chat dock"
            title="Collapse"
            data-testid="chat-dock-close"
          >
            <XIcon />
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        <ConversationHeader
          butlerName={WIDGET_BUTLER}
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
          <ConversationReadError label="conversation history" onRetry={() => void refetchMessages()} />
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
            onToggleIncluded: toggleIncludeContext,
          }}
        />
      </div>
    </div>
  );
}

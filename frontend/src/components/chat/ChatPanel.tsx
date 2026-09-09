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

import { useState, useEffect, useMemo, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { MessageSquareIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

import type { ConversationSummary } from "@/api/types.ts";
import { ConversationList } from "./ConversationList.tsx";
import { ConversationHeader } from "./ConversationHeader.tsx";
import { ConversationReadError } from "./ConversationReadError.tsx";
import { MessageThread, MessageThreadSkeleton } from "./MessageThread.tsx";
import { MessageInput } from "./MessageInput.tsx";
import { SendErrorBanner } from "./send-error.tsx";
import { scrollToMessageAnchor } from "./message-id.ts";
import {
  conversationKeys,
  useConversations,
  useConversationMessages,
} from "@/hooks/use-conversations.ts";
import { usePricingMap } from "@/hooks/use-pricing-map.ts";
import { useRegisterShortcut, type ShortcutBinding } from "@/hooks/use-register-shortcut";
import { useConversationTurn } from "@/hooks/use-conversation-turn.ts";

// ---------------------------------------------------------------------------
// ChatPanel inner content (mounted once Sheet is open)
// ---------------------------------------------------------------------------

export interface ChatContentProps {
  butlerName: string;
}

export function ChatContent({ butlerName }: ChatContentProps) {
  const queryClient = useQueryClient();

  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  // Set by a message-search jump-to-message result (bu-qaisp, parity with
  // FloatingChatWidget's bu-0ynlk.9) — consumed once that message's bubble
  // has rendered (see the effect below).
  const [pendingScrollMessageId, setPendingScrollMessageId] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState("");

  // Pricing is optional decoration: keep the existing null behavior while
  // loading or after an error, with a cache shared by both chat surfaces.
  const { data: pricingMapData } = usePricingMap();
  const pricingMap = pricingMapData ?? null;

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
    butlerName,
    activeConversationId,
    setActiveConversationId,
    messagesData,
  });

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
    abandonCurrentStream();
    hasResumedRef.current = false;
    setActiveConversationId(null);
    resetTurnState();
  }, [butlerName, abandonCurrentStream, resetTurnState]);

  const activeConversation = conversations.find((c) => c.id === activeConversationId) ?? null;

  // Once the jumped-to conversation's messages have rendered, scroll/focus
  // the anchor message. A miss (bubble not in the DOM yet) just waits for
  // the next render that changes visibleMessages.
  useEffect(() => {
    if (!pendingScrollMessageId) return;
    if (scrollToMessageAnchor(pendingScrollMessageId)) {
      setPendingScrollMessageId(null);
    }
  }, [pendingScrollMessageId, visibleMessages.length]);

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
    if (activeConversationId) {
      void queryClient.invalidateQueries({
        queryKey: conversationKeys.messages(butlerName, activeConversationId),
      });
    }
  }

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
        onSelectConversation={(id, messageId) => {
          abandonCurrentStream();
          setActiveConversationId(id);
          setPendingScrollMessageId(messageId ?? null);
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
            onToggleIncluded: toggleIncludeContext,
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

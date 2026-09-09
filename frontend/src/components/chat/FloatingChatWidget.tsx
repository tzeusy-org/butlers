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
 * left by bu-p6ey8.3: `buildMessagePayload()` (now inside
 * `hooks/use-conversation-turn.ts`, shared with `ChatPanel.tsx` — bu-0ynlk.11)
 * is the single choke point both `createConversation`/`sendMessage` calls go
 * through, taking a `PageContext` snapshot (`usePageContextCapture()`, see
 * `@/lib/page-context.tsx`) captured fresh at send time; the trigger button
 * renders a badge driven by `useChatUnreadBadge()` (see
 * `@/hooks/use-chat-unread.ts`).
 *
 * The send/stream/stop/retry state machine itself lives in
 * `useConversationTurn()` (`@/hooks/use-conversation-turn.ts`), shared
 * verbatim with `ChatPanel.tsx`'s `ChatContent` (bu-0ynlk.11) — this file
 * owns only the popover-specific view (thread/history toggle, jump-to-message
 * scroll) on top of that shared hook.
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
import { useChatUnreadBadge } from "@/hooks/use-chat-unread.ts";
import { useModalChoreography } from "@/hooks/use-modal-choreography";
import { useVisualViewportHeight } from "@/hooks/use-visual-viewport-height.ts";
import { useConversationTurn } from "@/hooks/use-conversation-turn.ts";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry.tsx";
import { OPEN_CHAT_WIDGET_EVENT } from "@/lib/shortcut-help";
import { announce } from "@/lib/shell-announcer";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** The staffer butler that owns dashboard chat-widget conversations (see
 * design doc § Storage scope — all widget threads live under Switchboard's
 * schema; the routed-to domain butler is metadata, not storage location). */
const WIDGET_BUTLER = "switchboard";

// ---------------------------------------------------------------------------
// WidgetPanel — mounted only while the widget is open
// ---------------------------------------------------------------------------

interface WidgetPanelProps {
  onClose: () => void;
}

function WidgetPanel({ onClose }: WidgetPanelProps) {
  const queryClient = useQueryClient();
  // This anchored popover deliberately leaves page tab order available, while
  // still following the shared focus-in/Escape/restore choreography.
  // Initial focus lands on the composer (not the panel title, bu-0ynlk.13's
  // wrong-focus-target fix); `obscureGuard` covers WCAG 2.2 2.4.11 for
  // elements the non-trapping panel could otherwise visually cover.
  const { rootRef, initialFocusRef, onKeyDown } = useModalChoreography<HTMLTextAreaElement>({
    onClose,
    trapFocus: false,
    obscureGuard: true,
  });
  const viewportHeight = useVisualViewportHeight();

  // The global 'c' shortcut (use-keyboard-shortcuts.ts) opens the widget when
  // closed AND, per the behavior matrix, refocuses the composer when it's
  // already open — the mount-triggered focus-in above already covers the
  // "just opened" case, so this listener only needs to matter while mounted.
  useEffect(() => {
    function handleOpenChatWidget() {
      initialFocusRef.current?.focus();
    }
    window.addEventListener(OPEN_CHAT_WIDGET_EVENT, handleOpenChatWidget);
    return () => window.removeEventListener(OPEN_CHAT_WIDGET_EVENT, handleOpenChatWidget);
  }, [initialFocusRef]);

  const [viewMode, setViewMode] = useState<"thread" | "history">("thread");
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  // Set by a message-search jump-to-message result (bu-0ynlk.9); consumed
  // once that message's bubble has rendered (see the effect below).
  const [pendingScrollMessageId, setPendingScrollMessageId] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState("");
  const { data: pricingMapData } = usePricingMap();
  const pricingMap = pricingMapData ?? null;

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

  // Resume the most recent open conversation ONCE per mount (== once per
  // reopen, since WidgetPanel unmounts entirely on close) — gated by
  // hasResumedRef so a later "New conversation" click (which also sets
  // activeConversationId to null) does not get immediately overridden back
  // to the existing thread by this same effect.
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

  function handleSendClick() {
    const text = inputValue.trim();
    if (!text) return;
    setInputValue("");
    void sendText(text);
  }

  function handleNewConversation() {
    abandonCurrentStream();
    setActiveConversationId(null);
    resetTurnState();
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

  // Once the jumped-to conversation's messages have rendered, scroll/focus
  // the anchor message. A miss (bubble not in the DOM yet) just waits for
  // the next render that changes visibleMessages.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!pendingScrollMessageId) return;
    if (scrollToMessageAnchor(pendingScrollMessageId)) {
      setPendingScrollMessageId(null);
    }
  }, [pendingScrollMessageId, visibleMessages.length]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const visibleDispatchReceipt =
    streaming && !streaming.cancelling && !streaming.cancelled && !streaming.interrupted
      ? streaming.dispatchReceipt
      : undefined;

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- role="dialog" + onKeyDown provides the shared Escape/focus choreography; the rule's static role allowlist does not recognize the WAI-ARIA dialog pattern.
    <div
      ref={rootRef}
      className="fixed bottom-20 right-4 z-40 flex h-[min(560px,80dvh)] w-[min(380px,calc(100vw-2rem))] flex-col overflow-hidden rounded-lg border bg-card shadow-lg"
      style={viewportHeight != null ? { maxHeight: Math.min(560, viewportHeight - 96) } : undefined}
      role="dialog"
      aria-labelledby="floating-chat-widget-title"
      data-testid="floating-chat-panel"
      onKeyDown={onKeyDown}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b bg-card/80 px-3 py-2 shrink-0">
        <h2
          id="floating-chat-widget-title"
          className="flex items-center gap-1.5 rounded-sm text-sm font-medium"
        >
          <MessageCircleIcon className="size-4 text-muted-foreground" />
          Talk to Butlers
        </h2>
        <div className="flex items-center gap-1.5">
          {viewMode === "thread" ? (
            <>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => setViewMode("history")}
                aria-label="Conversation history"
                title="History"
                data-testid="chat-widget-history-button"
              >
                <HistoryIcon />
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
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
              size="icon-sm"
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
            size="icon-sm"
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
            onSelectConversation={(id, messageId) => {
              abandonCurrentStream();
              setActiveConversationId(id);
              setPendingScrollMessageId(messageId ?? null);
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
            ref={initialFocusRef}
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
            contextChip={{
              label: contextPreview.label,
              policy: contextPreview.policy,
              payload: contextPreview.context,
              included: includeContext,
              onToggleIncluded: toggleIncludeContext,
            }}
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FloatingChatWidget — trigger button + panel toggle, mounted in RootLayout
// ---------------------------------------------------------------------------

export interface FloatingChatWidgetProps {
  /**
   * When set (>= xl viewport, dock collapsed — see RootLayout's posture
   * host), the trigger reopens the docked rail instead of the popover, so
   * closing the dock is never a one-way trip back to a settings toggle.
   */
  onExpandDock?: () => void;
}

export function FloatingChatWidget({ onExpandDock }: FloatingChatWidgetProps = {}) {
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

  // Route the unread badge through the shell announcer (bu-0ynlk.13) so a
  // screen-reader user hears about a reply that arrived while the panel was
  // closed, not just a visual dot. Announce only on the false->true edge —
  // the badge stays true across every subsequent ~60s poll, and re-announcing
  // identical text is silent to screen readers anyway (see shell-announcer's
  // `announce`), but this keeps the intent explicit: once per new reply.
  const hadUnreadRef = useRef(false);
  useEffect(() => {
    if (hasUnread && !hadUnreadRef.current) {
      announce("New reply from Butlers");
    }
    hadUnreadRef.current = hasUnread;
  }, [hasUnread]);

  // Global 'c' shortcut (use-keyboard-shortcuts.ts): open the widget if it's
  // closed. WidgetPanel's own listener (mounted only while open) handles
  // refocusing the composer when it's already open.
  useEffect(() => {
    function handleOpenChatWidget() {
      setOpen(true);
    }
    window.addEventListener(OPEN_CHAT_WIDGET_EVENT, handleOpenChatWidget);
    return () => window.removeEventListener(OPEN_CHAT_WIDGET_EVENT, handleOpenChatWidget);
  }, []);

  // "Talk to Butlers" cmdk command (bu-86c4c.7 command spine) — opens the
  // widget from anywhere, same pattern as GlobalActionsRegistrar.
  const commands = useMemo<PaletteCommand[]>(
    () => [
      {
        id: "talk-to-butlers",
        label: "Talk to Butlers",
        keywords: ["chat", "switchboard", "message", "conversation"],
        perform: () => (onExpandDock ? onExpandDock() : setOpen(true)),
      },
    ],
    [onExpandDock],
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
          onClick={() => (onExpandDock ? onExpandDock() : setOpen(true))}
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

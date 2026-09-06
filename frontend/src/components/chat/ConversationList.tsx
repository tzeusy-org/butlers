/**
 * Sidebar conversation list with search, new conversation button,
 * and collapsible mode.
 *
 * Features:
 * - localStorage persistence for collapse state
 * - Loading skeleton
 * - Empty state with call-to-action
 * - Search with debounce
 */

import type { ReactNode } from "react";
import { useState } from "react";
import { PlusIcon, PanelLeftCloseIcon, PanelLeftOpenIcon, SearchIcon, XIcon } from "lucide-react";
import { Time } from "@/components/ui/time";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { Tip } from "@/components/ui/tip";
import { Row } from "@/components/ui/Row";
import { ButlerMark } from "@/components/ui/ButlerMark";
import { useConversations, useConversationSearch, useMessageSearch } from "@/hooks/use-conversations.ts";
import { useDebounce } from "@/hooks/use-debounce.ts";
import type { ConversationSummary, MessageSearchResult } from "@/api/types.ts";
import { ConversationReadError } from "./ConversationReadError.tsx";

// ---------------------------------------------------------------------------
// Storage key for sidebar collapse state
// ---------------------------------------------------------------------------

const SIDEBAR_COLLAPSED_KEY = "butlers:chat-sidebar-collapsed";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// ConversationItem
// ---------------------------------------------------------------------------

interface ConversationItemProps {
  conversation: ConversationSummary;
  isActive: boolean;
  collapsed: boolean;
  onClick: () => void;
}

function ConversationItem({
  conversation,
  isActive,
  collapsed,
  onClick,
}: ConversationItemProps) {
  const title = conversation.title ?? "Untitled conversation";
  const initial = title.charAt(0).toUpperCase();

  if (collapsed) {
    return (
      <Tip content={title}>
        <button
          type="button"
          onClick={onClick}
          aria-label={title}
          className={cn(
            "flex items-center justify-center size-9 rounded-lg text-sm font-medium transition-colors",
            isActive
              ? "bg-accent text-accent-foreground"
              : "hover:bg-muted text-muted-foreground hover:text-foreground",
          )}
        >
          {initial}
        </button>
      </Tip>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full text-left px-2 py-2 rounded-lg transition-colors",
        isActive
          ? "bg-accent text-accent-foreground"
          : "hover:bg-muted text-muted-foreground hover:text-foreground",
      )}
    >
      <p className="text-sm font-medium line-clamp-2 leading-tight">{title}</p>
      <div className="flex items-center gap-1.5 mt-0.5">
        <p className="text-xs text-muted-foreground">
          <Time value={conversation.updated_at} mode="relative" />
        </p>
        {conversation.routed_butler && (
          <Badge
            variant="outline"
            className="text-[10px] h-4 px-1 font-mono"
            data-testid="conversation-routed-butler"
          >
            {conversation.routed_butler}
          </Badge>
        )}
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// MessageSearchResultRow — owner-scoped cross-butler message hit (bu-0ynlk.9)
// ---------------------------------------------------------------------------

/** Render `snippet` with each `highlight_ranges` pair wrapped in a `<mark>`. */
function HighlightedSnippet({
  snippet,
  ranges,
}: {
  snippet: string;
  ranges: MessageSearchResult["highlight_ranges"];
}) {
  if (ranges.length === 0) return <>{snippet}</>;

  const nodes: ReactNode[] = [];
  let cursor = 0;
  ranges.forEach(([start, end], index) => {
    if (start > cursor) nodes.push(snippet.slice(cursor, start));
    nodes.push(
      <mark key={index} className="bg-accent text-accent-foreground rounded-sm px-0.5">
        {snippet.slice(start, end)}
      </mark>,
    );
    cursor = end;
  });
  if (cursor < snippet.length) nodes.push(snippet.slice(cursor));
  return <>{nodes}</>;
}

interface MessageSearchResultRowProps {
  result: MessageSearchResult;
  /** The panel's own fixed butler context (e.g. the widget's WIDGET_BUTLER). */
  panelButlerName: string;
  /** Called for a same-butler hit — the panel can open it and jump in-place. */
  onJumpToMessage: (conversationId: string, messageId: string) => void;
}

function MessageSearchResultRow({
  result,
  panelButlerName,
  onJumpToMessage,
}: MessageSearchResultRowProps) {
  const content = (
    <Row
      mark={<ButlerMark name={result.butler_name} size={16} />}
      meta={<Time value={result.created_at} mode="relative" />}
      interactive
      density="scan"
    >
      <p className="text-sm line-clamp-2 leading-tight">
        <HighlightedSnippet snippet={result.snippet} ranges={result.highlight_ranges} />
      </p>
    </Row>
  );

  // Same-butler hit: this panel already shows that butler's conversations,
  // so jump in place (select + scroll/focus the anchor message). A
  // different-butler hit has no shared history view yet (the full /chat page
  // is bu-0ynlk.11), so it opens the message's own deep_link route instead —
  // same "open elsewhere" convention as the session/lineage links in
  // MessageThread.tsx.
  if (result.butler_name === panelButlerName) {
    return (
      <button
        type="button"
        className="w-full text-left"
        data-testid="message-search-result"
        onClick={() => onJumpToMessage(result.conversation_id, result.message_id)}
      >
        {content}
      </button>
    );
  }

  return (
    <a
      href={result.deep_link}
      target="_blank"
      rel="noopener noreferrer"
      className="block"
      data-testid="message-search-result"
    >
      {content}
    </a>
  );
}

// ---------------------------------------------------------------------------
// ConversationList
// ---------------------------------------------------------------------------

export interface ConversationListProps {
  butlerName: string;
  activeConversationId: string | null;
  /**
   * `messageId` is passed for a jump-to-message message-search result
   * (bu-0ynlk.9) — a caller that wants scroll/focus-anchor behavior should
   * pass it through to `scrollToMessageAnchor` (`./message-id.ts`) once that
   * conversation's messages have rendered.
   */
  onSelectConversation: (conversationId: string, messageId?: string) => void;
  onNewConversation: () => void;
  /**
   * When `false`, the collapse toggle is hidden and the list always renders
   * expanded at full width with no `localStorage` persistence — used when
   * this component is reused as a standalone full-width history view (e.g.
   * the floating chat widget's history panel, bu-p6ey8.3) rather than the
   * collapsible sidebar beside the butler-detail Sheet's thread pane.
   * @default true
   */
  collapsible?: boolean;
}

export function ConversationList({
  butlerName,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  collapsible = true,
}: ConversationListProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedQuery = useDebounce(searchQuery, 300);

  const [collapsedState, setCollapsedState] = useState(() => {
    if (!collapsible) return false;
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
    } catch {
      return false;
    }
  });
  const collapsed = collapsible && collapsedState;

  const {
    data: conversationsData,
    isLoading,
    isError: isConversationsError,
    refetch: refetchConversations,
  } = useConversations(butlerName);
  const {
    data: searchData,
    isLoading: isSearching,
    isError: isSearchError,
    refetch: refetchSearch,
  } = useConversationSearch(butlerName, debouncedQuery);
  const {
    data: messageSearchData,
    isLoading: isMessageSearching,
    isError: isMessageSearchError,
    refetch: refetchMessageSearch,
  } = useMessageSearch(debouncedQuery);

  function toggleCollapse() {
    const next = !collapsed;
    setCollapsedState(next);
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
    } catch {
      // ignore storage errors
    }
  }

  const isSearchActive = debouncedQuery.trim().length > 0;
  const conversations: ConversationSummary[] = isSearchActive
    ? (searchData?.data ?? [])
    : (conversationsData?.data ?? []);
  const loading = isSearchActive ? isSearching : isLoading;
  const readError = isSearchActive ? isSearchError : isConversationsError;
  const retryRead = isSearchActive ? refetchSearch : refetchConversations;
  const errorLabel = isSearchActive ? "conversation search results" : "conversations";
  const messageSearchResults: MessageSearchResult[] = messageSearchData?.data ?? [];

  // Collapse strategy: width switches INSTANTLY (no width transition) so no
  // layout property is animated. Expandable content (search, list labels) is
  // conditionally rendered (instant show/hide). This satisfies AC#5.
  return (
    <div
      className={cn(
        "flex flex-col bg-muted/20",
        collapsible && "border-r",
        collapsed ? "w-12" : collapsible ? "w-[200px]" : "w-full",
      )}
    >
      {/* Header */}
      <div
        className={cn(
          "flex items-center border-b p-2 gap-1",
          collapsed ? "flex-col" : "flex-row",
        )}
      >
        {!collapsed && (
          <Button
            variant="ghost"
            size="sm"
            className="flex-1 justify-start gap-1.5 h-8 text-xs font-medium"
            onClick={onNewConversation}
          >
            <PlusIcon className="size-3.5" />
            New
          </Button>
        )}
        {collapsed && (
          <Tip content="New conversation">
            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              onClick={onNewConversation}
              aria-label="New conversation"
            >
              <PlusIcon className="size-4" />
            </Button>
          </Tip>
        )}
        {collapsible && (
          <Tip content={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            <Button
              variant="ghost"
              size="icon"
              className="size-8 shrink-0"
              onClick={toggleCollapse}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {collapsed ? (
                <PanelLeftOpenIcon className="size-4" />
              ) : (
                <PanelLeftCloseIcon className="size-4" />
              )}
            </Button>
          </Tip>
        )}
      </div>

      {/* Search (expanded mode only) */}
      {!collapsed && (
        <div className="px-2 pt-2 pb-1">
          <div className="relative">
            <SearchIcon className="absolute left-2 top-1/2 -translate-y-1/2 size-3 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search..."
              className="h-7 pl-6 pr-6 text-xs"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                aria-label="Clear search"
                title="Clear search"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <XIcon className="size-3" />
              </button>
            )}
          </div>
        </div>
      )}

      {/* List */}
      <div
        className={cn("flex-1 overflow-y-auto py-1", collapsed ? "px-1.5" : "px-2 space-y-0.5")}
      >
        {readError && (
          <ConversationReadError
            label={errorLabel}
            onRetry={() => void retryRead()}
            compact={collapsed}
          />
        )}
        {loading && !readError ? (
          collapsed ? null : (
            <div className="space-y-1 px-1">
              {Array.from({ length: 4 }, (_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-lg" />
              ))}
            </div>
          )
        ) : conversations.length === 0 ? (
          readError || collapsed ? null : (
            <EmptyState
              variant="page"
              title="No conversations yet."
              description={
                isSearchActive
                  ? "No results found."
                  : "Start a conversation below."
              }
              action={
                !isSearchActive ? (
                  <Button size="sm" onClick={onNewConversation}>
                    Start a conversation
                  </Button>
                ) : undefined
              }
            />
          )
        ) : (
          conversations.map((conv) => (
            <ConversationItem
              key={conv.id}
              conversation={conv}
              isActive={conv.id === activeConversationId}
              collapsed={collapsed}
              onClick={() => onSelectConversation(conv.id)}
            />
          ))
        )}

        {/* Owner-scoped cross-butler message search (bu-0ynlk.9) — separate
            from the conversation-level results above: one row per matching
            message, across every butler the owner has talked to. */}
        {!collapsed &&
          isSearchActive &&
          (isMessageSearching || isMessageSearchError || messageSearchResults.length > 0) && (
            <div className="mt-2 pt-2 border-t">
              <p className="px-1 pb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                Messages
              </p>
              {isMessageSearchError ? (
                <ConversationReadError
                  label="message search results"
                  onRetry={() => void refetchMessageSearch()}
                />
              ) : isMessageSearching ? (
                <div className="space-y-1 px-1">
                  {Array.from({ length: 2 }, (_, i) => (
                    <Skeleton key={i} className="h-10 w-full rounded-lg" />
                  ))}
                </div>
              ) : (
                messageSearchResults.map((result) => (
                  <MessageSearchResultRow
                    key={result.message_id}
                    result={result}
                    panelButlerName={butlerName}
                    onJumpToMessage={onSelectConversation}
                  />
                ))
              )}
            </div>
          )}
      </div>
    </div>
  );
}

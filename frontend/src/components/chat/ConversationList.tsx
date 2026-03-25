/**
 * Sidebar conversation list with search, new conversation button,
 * and collapsible mode.
 */

import { useState } from "react";
import { PlusIcon, PanelLeftCloseIcon, PanelLeftOpenIcon, SearchIcon, XIcon } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { useConversations, useConversationSearch } from "@/hooks/use-conversations.ts";
import { useDebounce } from "@/hooks/use-debounce.ts";
import type { ConversationSummary } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Storage key for sidebar collapse state
// ---------------------------------------------------------------------------

const SIDEBAR_COLLAPSED_KEY = "butlers:chat-sidebar-collapsed";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(iso: string): string {
  try {
    return formatDistanceToNow(new Date(iso), { addSuffix: true });
  } catch {
    return "";
  }
}

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
      <button
        type="button"
        onClick={onClick}
        title={title}
        className={cn(
          "flex items-center justify-center size-9 rounded-lg text-sm font-medium transition-colors",
          isActive
            ? "bg-accent text-accent-foreground"
            : "hover:bg-muted text-muted-foreground hover:text-foreground",
        )}
      >
        {initial}
      </button>
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
      <p className="text-xs text-muted-foreground mt-0.5">
        {relativeTime(conversation.updated_at)}
      </p>
    </button>
  );
}

// ---------------------------------------------------------------------------
// ConversationList
// ---------------------------------------------------------------------------

export interface ConversationListProps {
  butlerName: string;
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
}

export function ConversationList({
  butlerName,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
}: ConversationListProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedQuery = useDebounce(searchQuery, 300);

  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
    } catch {
      return false;
    }
  });

  const { data: conversationsData, isLoading } = useConversations(butlerName);
  const { data: searchData, isLoading: isSearching } = useConversationSearch(
    butlerName,
    debouncedQuery,
  );

  function toggleCollapse() {
    const next = !collapsed;
    setCollapsed(next);
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

  return (
    <div
      className={cn(
        "flex flex-col border-r bg-muted/20 transition-all duration-200",
        collapsed ? "w-12" : "w-[200px]",
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
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            onClick={onNewConversation}
            title="New conversation"
          >
            <PlusIcon className="size-4" />
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="size-8 shrink-0"
          onClick={toggleCollapse}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <PanelLeftOpenIcon className="size-4" />
          ) : (
            <PanelLeftCloseIcon className="size-4" />
          )}
        </Button>
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
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <XIcon className="size-3" />
              </button>
            )}
          </div>
        </div>
      )}

      {/* List */}
      <div className={cn("flex-1 overflow-y-auto py-1", collapsed ? "px-1.5" : "px-2 space-y-0.5")}>
        {loading ? (
          collapsed ? null : (
            <div className="space-y-1 px-1">
              {Array.from({ length: 4 }, (_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-lg" />
              ))}
            </div>
          )
        ) : conversations.length === 0 ? (
          collapsed ? null : (
            <EmptyState
              title="No conversations yet"
              description={
                isSearchActive
                  ? "No results found."
                  : "Start a conversation to get started."
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
      </div>
    </div>
  );
}

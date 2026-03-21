/**
 * Global command palette / search overlay.
 *
 * Triggered by a global "open-search" event (keyboard shortcut + header button).
 * Uses the debounced useSearch hook to fetch grouped results from the API.
 *
 * Client-side (instant): navigation pages and butler identities.
 * Server-side (debounced): entities, contacts, sessions, state.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { Clock, Search } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useSearch } from "@/hooks/use-search";
import { useButlers } from "@/hooks/use-butlers";
import type { SearchResult } from "@/api/index.ts";
import { RECENT_SEARCHES_KEY } from "@/lib/local-settings";
import { OPEN_COMMAND_PALETTE_EVENT } from "@/lib/command-palette";
import { navSections, type NavItem } from "@/components/layout/nav-config";

const MAX_RECENT = 5;

// ---------------------------------------------------------------------------
// Recent searches helpers (localStorage)
// ---------------------------------------------------------------------------

function getRecentSearches(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_SEARCHES_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function saveRecentSearch(query: string) {
  try {
    const recent = getRecentSearches().filter((s) => s !== query);
    recent.unshift(query);
    localStorage.setItem(
      RECENT_SEARCHES_KEY,
      JSON.stringify(recent.slice(0, MAX_RECENT)),
    );
  } catch {
    // Silently ignore localStorage errors.
  }
}

// ---------------------------------------------------------------------------
// Category display helpers
// ---------------------------------------------------------------------------

/** Capitalise and pluralise a category key for display. */
function categoryLabel(key: string): string {
  const labels: Record<string, string> = {
    pages: "Pages",
    butlers: "Butlers",
    entities: "Entities",
    contacts: "Contacts",
    sessions: "Sessions",
    state: "State",
  };
  return labels[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
}

// ---------------------------------------------------------------------------
// Flatten sidebar nav items into searchable page list
// ---------------------------------------------------------------------------

interface PageEntry {
  label: string;
  path: string;
  section: string;
}

function flattenNavItems(items: NavItem[], section: string): PageEntry[] {
  const result: PageEntry[] = [];
  for (const item of items) {
    if (item.kind === "group") {
      for (const child of item.children) {
        result.push({ label: child.label, path: child.path, section });
      }
    } else {
      result.push({ label: item.label, path: item.path, section });
    }
  }
  return result;
}

const allPages: PageEntry[] = navSections.flatMap((s) =>
  flattenNavItems(s.items, s.title),
);

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const [selectedIndex, setSelectedIndex] = useState(0);

  const { data, isLoading, isError } = useSearch(query);
  const { data: butlersResponse } = useButlers();

  // -----------------------------------------------------------------------
  // Client-side: instant page matches
  // -----------------------------------------------------------------------
  const pageResults: SearchResult[] = useMemo(() => {
    const q = query.toLowerCase().trim();
    if (q.length < 1) return [];
    return allPages
      .filter(
        (p) =>
          p.label.toLowerCase().includes(q) ||
          p.path.toLowerCase().includes(q),
      )
      .slice(0, 8)
      .map((p) => ({
        id: `page:${p.path}`,
        butler: "navigation",
        type: "page",
        title: p.label,
        snippet: p.section,
        url: p.path,
      }));
  }, [query]);

  // -----------------------------------------------------------------------
  // Client-side: instant butler matches
  // -----------------------------------------------------------------------
  const butlerResults: SearchResult[] = useMemo(() => {
    const q = query.toLowerCase().trim();
    if (q.length < 1 || !butlersResponse?.data) return [];
    return butlersResponse.data
      .filter((b) => b.name.toLowerCase().includes(q))
      .slice(0, 8)
      .map((b) => ({
        id: `butler:${b.name}`,
        butler: b.name,
        type: "butler",
        title: b.name,
        snippet: b.status,
        url: `/butlers/${b.name}`,
      }));
  }, [query, butlersResponse]);

  // -----------------------------------------------------------------------
  // Build a flat list of results for keyboard navigation
  // -----------------------------------------------------------------------
  const groupedResults: { category: string; results: SearchResult[] }[] =
    useMemo(() => {
      const groups: { category: string; results: SearchResult[] }[] = [];

      // Client-side results first (instant)
      if (pageResults.length > 0) {
        groups.push({ category: "pages", results: pageResults });
      }
      if (butlerResults.length > 0) {
        groups.push({ category: "butlers", results: butlerResults });
      }

      // Server-side results (debounced)
      if (data?.data) {
        for (const category of [
          "entities",
          "contacts",
          "sessions",
          "state",
        ]) {
          const results = data.data[category];
          if (results && results.length > 0) {
            groups.push({ category, results });
          }
        }
      }

      return groups;
    }, [pageResults, butlerResults, data]);

  const flatResults = useMemo(
    () => groupedResults.flatMap((g) => g.results),
    [groupedResults],
  );

  // -----------------------------------------------------------------------
  // Open event bridge (shared by keyboard shortcuts and header icon)
  // -----------------------------------------------------------------------
  useEffect(() => {
    function handleOpenCommandPalette() {
      setOpen(true);
      requestAnimationFrame(() => inputRef.current?.focus());
    }

    window.addEventListener(OPEN_COMMAND_PALETTE_EVENT, handleOpenCommandPalette);
    return () => window.removeEventListener(OPEN_COMMAND_PALETTE_EVENT, handleOpenCommandPalette);
  }, []);

  // -----------------------------------------------------------------------
  // Reset state when dialog opens/closes
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (open) {
      setQuery("");
      setSelectedIndex(0);
      // Focus the input after the dialog transition
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // Reset selection when results change
  useEffect(() => {
    setSelectedIndex(0);
  }, [groupedResults]);

  // -----------------------------------------------------------------------
  // Navigate to a result
  // -----------------------------------------------------------------------
  const navigateToResult = useCallback(
    (result: SearchResult) => {
      saveRecentSearch(query);
      setOpen(false);
      navigate(result.url);
    },
    [navigate, query],
  );

  // -----------------------------------------------------------------------
  // Keyboard navigation within results
  // -----------------------------------------------------------------------
  function handleInputKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => Math.min(prev + 1, flatResults.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) => Math.max(prev - 1, 0));
    } else if (e.key === "Enter" && flatResults.length > 0) {
      e.preventDefault();
      navigateToResult(flatResults[selectedIndex]);
    }
  }

  // -----------------------------------------------------------------------
  // Render helpers
  // -----------------------------------------------------------------------
  const recentSearches = getRecentSearches();
  const hasClientResults = pageResults.length > 0 || butlerResults.length > 0;
  const showRecent = query.length < 1 && recentSearches.length > 0;
  const showLoading = isLoading && query.length >= 2 && !hasClientResults;
  const showEmpty =
    !isLoading && query.length >= 1 && flatResults.length === 0 && !isError;

  let flatIndex = -1;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        showCloseButton={false}
        className="top-[20%] translate-y-0 gap-0 p-0 sm:max-w-xl"
      >
        <DialogTitle className="sr-only">Search</DialogTitle>

        {/* Search input */}
        <div className="flex items-center border-b border-border px-4">
          <Search className="mr-2 size-4 shrink-0 text-muted-foreground" />
          <Input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleInputKeyDown}
            placeholder="Search pages, butlers, entities, contacts..."
            className="h-12 border-0 shadow-none focus-visible:ring-0"
          />
          <kbd className="ml-2 hidden shrink-0 rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground sm:inline-block">
            ESC
          </kbd>
        </div>

        {/* Results area */}
        <div className="max-h-[300px] overflow-y-auto">
          {/* Recent searches */}
          {showRecent && (
            <div className="p-2">
              <p className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
                Recent searches
              </p>
              {recentSearches.map((term) => (
                <button
                  key={term}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm text-foreground hover:bg-accent"
                  onClick={() => setQuery(term)}
                >
                  <Clock className="size-3.5 text-muted-foreground" />
                  {term}
                </button>
              ))}
            </div>
          )}

          {/* Loading skeleton (only shown when no client-side results) */}
          {showLoading && (
            <div className="space-y-2 p-4">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-10 w-full" />
            </div>
          )}

          {/* Error state */}
          {isError && query.length >= 2 && (
            <p className="p-4 text-center text-sm text-destructive">
              Search failed. Please try again.
            </p>
          )}

          {/* Empty state */}
          {showEmpty && (
            <p className="p-4 text-center text-sm text-muted-foreground">
              No results found
            </p>
          )}

          {/* Grouped results */}
          {groupedResults.map((group) => (
            <div key={group.category} className="p-2">
              <p className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
                {categoryLabel(group.category)}
              </p>
              {group.results.map((result) => {
                flatIndex++;
                const idx = flatIndex;
                return (
                  <button
                    key={result.id}
                    className={`flex w-full items-center gap-3 rounded-md px-2 py-2 text-left text-sm transition-colors ${
                      idx === selectedIndex
                        ? "bg-accent text-accent-foreground"
                        : "text-foreground hover:bg-accent/50"
                    }`}
                    onClick={() => navigateToResult(result)}
                    onMouseEnter={() => setSelectedIndex(idx)}
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{result.title}</p>
                      {result.snippet && (
                        <p className="truncate text-xs text-muted-foreground">
                          {result.snippet}
                        </p>
                      )}
                    </div>
                    <Badge variant="secondary" className="shrink-0 text-[10px]">
                      {result.butler}
                    </Badge>
                  </button>
                );
              })}
            </div>
          ))}
        </div>

        {/* Footer hint */}
        {flatResults.length > 0 && (
          <div className="flex items-center gap-3 border-t border-border px-4 py-2 text-xs text-muted-foreground">
            <span>
              <kbd className="rounded border border-border bg-muted px-1 py-0.5 font-mono text-[10px]">
                &uarr;
              </kbd>{" "}
              <kbd className="rounded border border-border bg-muted px-1 py-0.5 font-mono text-[10px]">
                &darr;
              </kbd>{" "}
              to navigate
            </span>
            <span>
              <kbd className="rounded border border-border bg-muted px-1 py-0.5 font-mono text-[10px]">
                Enter
              </kbd>{" "}
              to open
            </span>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

/**
 * Global entity-first Cmd-K finder (bu-xfjwk).
 *
 * Uses the `cmdk` 1.1.1 library wired to
 * GET /api/butlers/relationship/entities/search.
 *
 * Result ordering: entity group is rendered FIRST (highest-scored results
 * from the relationship search endpoint), followed by navigation pages.
 * This fulfils Brief §5 Open Question 14 (entity-first reordering) and
 * Brief §6b Amendment 15 (deterministic Finder — no LLM, no embeddings).
 *
 * Keyboard shortcuts:
 *   Cmd/Ctrl+K   — open (global, any focused element)
 *   /            — open (when no input/textarea is focused)
 *   Esc          — close
 *   ↑ / ↓        — step through results
 *   Enter        — open result page
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Command } from "cmdk";
import { useNavigate } from "react-router";

import { useEntityFinderSearch } from "@/hooks/use-entities";
import { OPEN_ENTITY_FINDER_EVENT } from "@/lib/entity-finder";
import { navSections, type NavItem } from "@/components/layout/nav-config";
import type { EntityFinderSearchResult } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Nav pages — client-side instant matches
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

const ALL_PAGES: PageEntry[] = navSections.flatMap((s) =>
  flattenNavItems(s.items, s.title),
);

// ---------------------------------------------------------------------------
// Entity mark glyph — single-letter type badge
// ---------------------------------------------------------------------------

function entityTypeGlyph(entityType: string): string {
  switch (entityType?.toLowerCase()) {
    case "person":
      return "P";
    case "organization":
      return "O";
    case "place":
      return "L";
    default:
      return "E";
  }
}

// ---------------------------------------------------------------------------
// Match kind label — human-readable hint shown in the result caption
// ---------------------------------------------------------------------------

function matchKindLabel(kind: EntityFinderSearchResult["match_kind"]): string {
  switch (kind) {
    case "prefix":
      return "name";
    case "contact_fact":
      return "contact";
    case "substring":
      return "alias";
    case "predicate":
      return "relation";
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function EntityFinder() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const { data: searchData, isLoading } = useEntityFinderSearch(query, {
    limit: 8,
  });

  // -------------------------------------------------------------------------
  // Open via custom event — reset query and focus input on open
  // -------------------------------------------------------------------------
  useEffect(() => {
    function handleOpen() {
      setOpen(true);
      setQuery("");
      requestAnimationFrame(() => inputRef.current?.focus());
    }
    window.addEventListener(OPEN_ENTITY_FINDER_EVENT, handleOpen);
    return () =>
      window.removeEventListener(OPEN_ENTITY_FINDER_EVENT, handleOpen);
  }, []);

  // -------------------------------------------------------------------------
  // Navigate to a result and close
  // -------------------------------------------------------------------------
  const openEntity = useCallback(
    (entityId: string) => {
      setOpen(false);
      navigate(`/butlers/relationship/entities/${encodeURIComponent(entityId)}`);
    },
    [navigate],
  );

  const openPage = useCallback(
    (path: string) => {
      setOpen(false);
      navigate(path);
    },
    [navigate],
  );

  // -------------------------------------------------------------------------
  // Client-side page filtering (instant, no debounce)
  // -------------------------------------------------------------------------
  const trimmedQuery = query.trim().toLowerCase();
  const pageMatches: PageEntry[] =
    trimmedQuery.length >= 1
      ? ALL_PAGES.filter(
          (p) =>
            p.label.toLowerCase().includes(trimmedQuery) ||
            p.path.toLowerCase().includes(trimmedQuery),
        ).slice(0, 5)
      : [];

  const entityResults: EntityFinderSearchResult[] =
    searchData?.results ?? [];

  const hasResults = entityResults.length > 0 || pageMatches.length > 0;

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 pt-[15vh]"
      onClick={() => setOpen(false)}
      data-testid="entity-finder-backdrop"
    >
      <Command
        label="Entity Finder"
        className="relative mx-auto w-full max-w-2xl overflow-hidden rounded-xl border border-border bg-background shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            setOpen(false);
          }
        }}
        shouldFilter={false}
      >
        {/* Input row */}
        <div className="flex items-center border-b border-border px-4">
          <span className="mr-2 shrink-0 font-mono text-xs text-muted-foreground">/</span>
          <Command.Input
            ref={inputRef}
            value={query}
            onValueChange={setQuery}
            placeholder="Search entities, pages…"
            className="h-12 w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
            data-testid="entity-finder-input"
          />
          <kbd className="ml-2 hidden shrink-0 rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline-block">
            ESC
          </kbd>
        </div>

        <Command.List className="max-h-[420px] overflow-y-auto p-2">
          {/* Empty state */}
          {!isLoading && query.trim().length > 0 && !hasResults && (
            <Command.Empty className="py-6 text-center text-sm text-muted-foreground">
              No results for &ldquo;{query}&rdquo;
            </Command.Empty>
          )}

          {/* Loading indicator */}
          {isLoading && query.trim().length > 0 && (
            <div className="py-4 text-center text-xs text-muted-foreground">
              Searching…
            </div>
          )}

          {/* ---------------------------------------------------------------
           * ENTITY GROUP — rendered FIRST (entity-first ordering per Brief §5
           * Open Question 14 and bu-xfjwk acceptance criteria).
           * Results are pre-ranked by the server: prefix (100) > contact_fact
           * (70) > substring (50) > predicate (30).
           * --------------------------------------------------------------- */}
          {entityResults.length > 0 && (
            <Command.Group
              heading="Entities"
              className="mb-1"
              data-testid="entity-finder-entity-group"
            >
              {entityResults.map((result) => (
                <Command.Item
                  key={result.entity_id}
                  value={`entity:${result.entity_id}:${result.canonical_name}`}
                  onSelect={() => openEntity(result.entity_id)}
                  className="flex cursor-pointer select-none items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground aria-selected:bg-accent aria-selected:text-accent-foreground"
                  data-testid="entity-finder-entity-item"
                >
                  {/* Entity type glyph */}
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded bg-muted font-mono text-xs font-semibold text-muted-foreground">
                    {entityTypeGlyph(result.entity_type)}
                  </span>

                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{result.canonical_name}</p>
                    <p className="truncate font-mono text-[10px] uppercase text-muted-foreground">
                      matched on {matchKindLabel(result.match_kind)}
                    </p>
                  </div>

                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                    score {result.score}
                  </span>
                </Command.Item>
              ))}
            </Command.Group>
          )}

          {/* ---------------------------------------------------------------
           * PAGES GROUP — navigation links, shown after entities
           * --------------------------------------------------------------- */}
          {pageMatches.length > 0 && (
            <Command.Group heading="Pages">
              {pageMatches.map((page) => (
                <Command.Item
                  key={page.path}
                  value={`page:${page.path}:${page.label}`}
                  onSelect={() => openPage(page.path)}
                  className="flex cursor-pointer select-none items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground aria-selected:bg-accent aria-selected:text-accent-foreground"
                  data-testid="entity-finder-page-item"
                >
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded bg-muted font-mono text-xs font-semibold text-muted-foreground">
                    ↗
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{page.label}</p>
                    <p className="truncate font-mono text-[10px] uppercase text-muted-foreground">
                      {page.section}
                    </p>
                  </div>
                </Command.Item>
              ))}
            </Command.Group>
          )}
        </Command.List>

        {/* Keyboard footer */}
        <div className="flex items-center gap-4 border-t border-border px-4 py-2 font-mono text-[10px] uppercase text-muted-foreground">
          <span>
            <kbd className="rounded border border-border bg-muted px-1 py-0.5">↑</kbd>
            {" "}
            <kbd className="rounded border border-border bg-muted px-1 py-0.5">↓</kbd>
            {" "}step
          </span>
          <span>
            <kbd className="rounded border border-border bg-muted px-1 py-0.5">↵</kbd>
            {" "}open
          </span>
          <span>
            <kbd className="rounded border border-border bg-muted px-1 py-0.5">esc</kbd>
            {" "}close
          </span>
        </div>
      </Command>
    </div>
  );
}

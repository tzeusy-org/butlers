/**
 * The unified command menu (bu-86c4c.7 "one command spine"; originally
 * bu-xfjwk's entity-first Cmd-K finder, extended for entity-v3 bu-rru9g).
 *
 * Before bu-86c4c.7 this component only searched entities and pages, while a
 * second, separately-mounted CommandPalette handled butlers/sessions/state
 * search and was opened by a *different* set of triggers (the header button
 * and '/', while Cmd+K opened this one) — a split-brain command layer with
 * no verbs. This component now absorbs that surface: it is the ONE thing
 * opened by Cmd+K, '/', and the header button, and additionally exposes an
 * Actions group backed by a per-page command registration API
 * (src/lib/command-registry.tsx) so pages can contribute verbs (e.g.
 * ApprovalsPage's "Approve next") without this component knowing about them.
 *
 * Uses the `cmdk` 1.1.1 library. Entity search wired to
 * GET /api/butlers/relationship/entities/search; butlers/sessions/state
 * search wired to the shared GET /api/search endpoint (useSearch).
 *
 * Result ordering: entity group is rendered FIRST (highest-scored results
 * from the relationship search endpoint), followed by navigation pages, then
 * butlers/sessions/state, then Actions. This fulfils Brief §5 Open Question
 * 14 (entity-first reordering) and Brief §6b Amendment 15 (deterministic
 * Finder — no LLM, no embeddings).
 *
 * entity-v3 additions (spec: dashboard-relationship "Finder preview pane and
 * Tab-to-hop", "Finder empty-query state — owner-pinned set", MODIFIED
 * "App-wide Cmd-K Finder"):
 *   - Right-hand preview pane for the active result (entity mark, name,
 *     type/tier, canned gloss, top-5 relations). Inert — no links. Sourced from
 *     the search response plus at most ONE debounced
 *     GET /entities/{id}/neighbours per active-row change.
 *   - Tab = "hop into": close the Finder and center the Plex (/entities?center=<id>).
 *   - Empty query renders the owner-pinned set (owner's top-8 neighbours by
 *     summed weight), via the same ranked /neighbours endpoint.
 *
 * Keyboard shortcuts:
 *   Cmd/Ctrl+K   — open (global, works even while an input is focused)
 *   /            — open (when no input/textarea is focused)
 *   ↑ / ↓        — step through results
 *   Enter        — open result detail / run action
 *   Tab          — hop into the active result (/entities?center=<id>)
 *   Esc          — close
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Command } from "cmdk";
import { useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";

import {
  useEntityFinderSearch,
  useEntityNeighbours,
} from "@/hooks/use-entities";
import { getOwnerSetupStatus } from "@/api/index";
import {
  OPEN_ENTITY_FINDER_EVENT,
  aggregateOwnerPinned,
} from "@/lib/entity-finder";
import { ALL_ROUTES } from "@/lib/route-registry";
import { useCommandMenuActions, type PaletteCommand } from "@/lib/command-registry";
import { fuzzyFilter } from "@/lib/fuzzy-match";
import { addRecent, getRecents, type RecentEntry } from "@/lib/recents-store";
import { useSearch } from "@/hooks/use-search";
import { useButlers } from "@/hooks/use-butlers";
import { useModalChoreography } from "@/hooks/use-modal-choreography";
import { usePrefetchOnIntent } from "@/hooks/use-prefetch-on-intent";
import { EntityMark } from "@/components/ui/EntityMark";
import { FetchingDim } from "@/components/ui/fetching-dim";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { KbMono } from "@/components/ui/KbMono";
import type {
  EntityFinderSearchResult,
  NeighbourEntry,
} from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Pages group — client-side instant matches, sourced from the single route
// registry (src/lib/route-registry.ts) so every route (including ones not
// promoted to the sidebar, e.g. /entities/circles, health sub-pages) is
// reachable here.
// ---------------------------------------------------------------------------

interface PageEntry {
  label: string;
  path: string;
  section: string;
}

const ALL_PAGES: PageEntry[] = ALL_ROUTES.map((r) => ({
  label: r.label,
  path: r.path,
  section: r.section,
}));

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
// Gloss helpers
//
// The search/neighbours payloads do not carry the Dunbar tier or curation
// state. The preview pane therefore omits the tier/state gloss entirely —
// showing a fabricated "Meaningful contact. Active in the network." (or similar)
// for an entity whose real tier and health are unknown is misleading. Only the
// entity type label (derived from the search payload) is shown.
// ---------------------------------------------------------------------------

/** Human-readable type label for the preview header. */
function typeLabel(entityType: string): string {
  return entityType?.toLowerCase() || "entity";
}

// ---------------------------------------------------------------------------
// Active-row preview pane
// ---------------------------------------------------------------------------

interface PreviewPaneProps {
  /** The active result, or null when nothing is highlighted. */
  active: { entity_id: string; canonical_name: string; entity_type: string } | null;
}

function PreviewPane({ active }: PreviewPaneProps) {
  // Debounce the active entity id so arrowing quickly through results issues at
  // most one neighbours call per settled active row (spec: "at most one
  // debounced GET /entities/{id}/neighbours call for the active row").
  const [debouncedId, setDebouncedId] = useState<string | undefined>(
    active?.entity_id,
  );

  useEffect(() => {
    const id = active?.entity_id;
    const timer = setTimeout(() => setDebouncedId(id), 180);
    return () => clearTimeout(timer);
  }, [active?.entity_id]);

  const { data: neighboursData } = useEntityNeighbours(debouncedId, {
    rank: "weight",
    per_predicate: 5,
  });

  // Top-5 relations across predicates, ranked by edge weight.
  const topRelations = useMemo<NeighbourEntry[]>(() => {
    const groups = neighboursData?.neighbours;
    if (!groups) return [];
    const flat: NeighbourEntry[] = [];
    for (const entries of Object.values(groups)) flat.push(...entries);
    return flat
      .slice()
      .sort((a, b) => (b.weight ?? 1) - (a.weight ?? 1))
      .slice(0, 5);
  }, [neighboursData]);

  if (!active) {
    return (
      <div
        className="hidden w-64 shrink-0 border-l border-border p-4 text-xs text-muted-foreground sm:block"
        data-testid="entity-finder-preview-empty"
        aria-hidden="true"
      >
        Select a result to preview.
      </div>
    );
  }

  return (
    <div
      className="hidden w-64 shrink-0 flex-col gap-3 border-l border-border p-4 sm:flex"
      data-testid="entity-finder-preview"
      aria-hidden="true"
    >
      {/* Header: mark + name + type */}
      <div className="flex items-start gap-2">
        <EntityMark
          name={active.canonical_name}
          entityType={active.entity_type}
          tone="fill"
          size={28}
        />
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-foreground">
            {active.canonical_name}
          </p>
          <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
            {typeLabel(active.entity_type)}
          </p>
        </div>
      </div>

      {/* Gloss is intentionally omitted: the search payload carries no tier or
          curation state, so any gloss here would use fabricated defaults. Open
          the entity detail page to see the authoritative gloss. */}

      {/* Top-5 relations — inert (no links) */}
      <div className="flex flex-col gap-1" data-testid="entity-finder-preview-relations">
        <p className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
          Top relations
        </p>
        {topRelations.length === 0 ? (
          <p className="text-xs text-muted-foreground">No relations.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {topRelations.map((rel) => (
              <li
                key={rel.entity_id}
                className="flex items-center gap-2 text-xs text-foreground"
                data-testid="entity-finder-preview-relation"
              >
                <EntityMark
                  name={rel.canonical_name || rel.entity_id}
                  entityType="person"
                  size={16}
                />
                <span className="truncate">
                  {rel.canonical_name || rel.entity_id}
                </span>
                {rel.weight != null && (
                  <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
                    {rel.weight}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function EntityFinder() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  // cmdk's currently-highlighted item value (drives the preview pane).
  const [activeValue, setActiveValue] = useState("");
  const navigate = useNavigate();

  // One overlay contract (bu-qvnce.10): focus-in, Tab trap, Escape, and
  // focus-restore. EntityFinder stays mounted and toggles its own visibility
  // (returns `null` while closed) rather than being conditionally mounted by
  // a parent, so `active: open` is required — see the hook's doc comment.
  const { rootRef, initialFocusRef: inputRef, onKeyDown: choreographyKeyDown } =
    useModalChoreography<HTMLInputElement>({
      onClose: () => setOpen(false),
      active: open,
    });

  const {
    data: searchData,
    isLoading,
    isFetching: entityFetching,
    isError,
    refetch: refetchEntitySearch,
  } = useEntityFinderSearch(query, {
    limit: 8,
  });

  const trimmedQuery = query.trim();
  const isEmptyQuery = trimmedQuery.length === 0;

  // -------------------------------------------------------------------------
  // Empty-query owner-pinned set: owner's top neighbours by summed weight.
  // Only resolved while the Finder is open AND the query is empty.
  // -------------------------------------------------------------------------
  const { data: ownerStatus } = useQuery({
    queryKey: ["owner-setup-status"],
    queryFn: getOwnerSetupStatus,
    enabled: open && isEmptyQuery,
  });
  const ownerId = ownerStatus?.entity_id ?? undefined;

  const { data: ownerNeighbours } = useEntityNeighbours(
    open && isEmptyQuery ? ownerId : undefined,
    { rank: "weight" },
  );

  const pinned = useMemo(
    () => aggregateOwnerPinned(ownerNeighbours?.neighbours, ownerId, 8),
    [ownerNeighbours, ownerId],
  );

  // -------------------------------------------------------------------------
  // Open via custom event — reset query and focus input on open
  // -------------------------------------------------------------------------
  useEffect(() => {
    function handleOpen() {
      setOpen(true);
      setQuery("");
      setActiveValue("");
      // Focus-in is handled by useModalChoreography's `active: open` effect
      // once this state flip commits — no rAF needed, useEffect already
      // defers to after the DOM update.
    }
    window.addEventListener(OPEN_ENTITY_FINDER_EVENT, handleOpen);
    return () =>
      window.removeEventListener(OPEN_ENTITY_FINDER_EVENT, handleOpen);
  }, []);

  // -------------------------------------------------------------------------
  // Navigation
  // -------------------------------------------------------------------------
  const openEntity = useCallback(
    (entityId: string, canonicalName?: string, entityType?: string) => {
      setOpen(false);
      if (canonicalName) {
        addRecent({ id: entityId, kind: "entity", label: canonicalName, entityType });
      }
      navigate(`/entities/${encodeURIComponent(entityId)}`);
    },
    [navigate],
  );

  const hopEntity = useCallback(
    (entityId: string) => {
      setOpen(false);
      navigate(`/entities?center=${encodeURIComponent(entityId)}`);
    },
    [navigate],
  );

  const openPage = useCallback(
    (path: string, label?: string) => {
      setOpen(false);
      if (label) addRecent({ id: path, kind: "page", label });
      navigate(path);
    },
    [navigate],
  );

  // -------------------------------------------------------------------------
  // Client-side page filtering (instant, no debounce) — shared fuzzy scorer
  // (bu-qvnce.11) instead of `.includes()`, so e.g. "iss" ranks "Issues"
  // (prefix match) above a coincidental substring hit elsewhere.
  // -------------------------------------------------------------------------
  const lowerQuery = trimmedQuery.toLowerCase();
  const pageMatches: PageEntry[] =
    lowerQuery.length >= 1
      ? fuzzyFilter(trimmedQuery, ALL_PAGES, {
          getLabel: (p) => p.label,
          getKeywords: (p) => [p.path],
          limit: 8,
        })
      : [];

  const entityResults: EntityFinderSearchResult[] = useMemo(
    () => searchData?.results ?? [],
    [searchData],
  );

  // -------------------------------------------------------------------------
  // Butlers group — client-side instant match, absorbed from the legacy
  // CommandPalette (bu-86c4c.7). Navigates to the butler detail page.
  // -------------------------------------------------------------------------
  const { data: butlersResponse } = useButlers();
  const butlerMatches =
    lowerQuery.length >= 1 && butlersResponse?.data
      ? fuzzyFilter(trimmedQuery, butlersResponse.data, { getLabel: (b) => b.name, limit: 5 })
      : [];

  // -------------------------------------------------------------------------
  // Sessions / State groups — server-side debounced search, absorbed from the
  // legacy CommandPalette (bu-86c4c.7). The "entities" and "contacts"
  // categories from this endpoint are intentionally NOT surfaced here: the
  // dedicated entity search above already covers both (contact-fact matches
  // included, see matchKindLabel's "contact_fact" case) and a second,
  // differently-ranked entity list would just be a confusing duplicate.
  // -------------------------------------------------------------------------
  const { data: genericSearchData, isFetching: genericFetching } = useSearch(trimmedQuery);
  // useMemo (not a bare `?? []`) so a stable empty-array reference doesn't
  // churn the highlightedPath useMemo's deps below on every render.
  const sessionMatches = useMemo(
    () => genericSearchData?.data?.sessions ?? [],
    [genericSearchData],
  );
  const stateMatches = useMemo(
    () => genericSearchData?.data?.state ?? [],
    [genericSearchData],
  );

  // -------------------------------------------------------------------------
  // Palette-highlight prefetch (bu-qvnce.14 slice 4, deferred from PR #2927).
  // cmdk's own aria-selected highlight (arrow keys or mouse-over a row) is
  // this surface's "intent" signal — the same idea as RowLink/DisclosureRow's
  // hover/focus, just driven by `activeValue` instead of pointer events since
  // cmdk owns row hover itself (no separate pointerenter to hook). Only
  // Pages/Sessions/State rows resolve to a route-registry target; Entities
  // (already covered by PreviewPane's own neighbours prefetch above),
  // Butlers, Recents, and Actions rows resolve to `null` and no-op.
  // -------------------------------------------------------------------------
  const highlightedPath = useMemo<string | null>(() => {
    if (!activeValue) return null;
    if (activeValue.startsWith("page:")) {
      const rest = activeValue.slice("page:".length);
      const sep = rest.indexOf(":");
      return sep >= 0 ? rest.slice(0, sep) : rest;
    }
    if (activeValue.startsWith("session:")) {
      const id = activeValue.slice("session:".length).split(":")[0];
      return sessionMatches.find((s) => s.id === id)?.url ?? null;
    }
    if (activeValue.startsWith("state:")) {
      const id = activeValue.slice("state:".length).split(":")[0];
      return stateMatches.find((s) => s.id === id)?.url ?? null;
    }
    return null;
  }, [activeValue, sessionMatches, stateMatches]);

  const highlightPrefetch = usePrefetchOnIntent(highlightedPath);
  useEffect(() => {
    highlightPrefetch.schedule();
    return highlightPrefetch.cancel;
    // highlightPrefetch reads its target via a ref (stable identity across
    // `to` changes) -- only the resolved path itself should retrigger this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightedPath]);

  // -------------------------------------------------------------------------
  // Actions group — per-page command registration API (bu-86c4c.7). Shown at
  // empty query too (bu-qvnce.11 — previously verbs were invisible until the
  // first keystroke, the exact "palette-mute" finding the JARVIS audit named
  // against EntityFinder); a non-empty query re-ranks via the shared fuzzy
  // scorer instead of `.includes()`.
  // -------------------------------------------------------------------------
  const allActions = useCommandMenuActions();
  const actionMatches = isEmptyQuery
    ? allActions.slice(0, 8)
    : fuzzyFilter(trimmedQuery, allActions, {
        getLabel: (a) => a.label,
        getKeywords: (a) => a.keywords,
        limit: 8,
      });

  const runAction = useCallback(
    (action: PaletteCommand) => {
      setOpen(false);
      addRecent({ id: action.id, kind: "action", label: action.label });
      action.perform();
    },
    [],
  );

  // -------------------------------------------------------------------------
  // Recents group — last few opened entities/pages/actions (bu-qvnce.11),
  // shown at empty query alongside the owner-pinned entity set so the
  // palette is browsable before typing a single character. Recomputed each
  // time the Finder opens (not on every keystroke) so a selection made just
  // before closing shows up next time. A stale action recent (registered by
  // a page that's no longer mounted) is dropped rather than rendered dead.
  // -------------------------------------------------------------------------
  const actionById = useMemo(() => new Map(allActions.map((a) => [a.id, a])), [allActions]);
  const recentEntries = useMemo<RecentEntry[]>(() => (open ? getRecents() : []), [open]);
  const recentRows = useMemo(() => {
    if (!isEmptyQuery) return [];
    return recentEntries
      .map((r) => {
        if (r.kind === "action") {
          const action = actionById.get(r.id);
          return action ? { ...r, run: () => runAction(action) } : null;
        }
        if (r.kind === "page") return { ...r, run: () => openPage(r.id, r.label) };
        return { ...r, run: () => openEntity(r.id, r.label, r.entityType) };
      })
      .filter((row): row is RecentEntry & { run: () => void } => row != null)
      .slice(0, 5);
  }, [recentEntries, isEmptyQuery, actionById, runAction, openPage, openEntity]);

  // The active result the preview pane mirrors. cmdk highlights the first item
  // by default and encodes the highlighted item via its `value`
  // (`entity:<id>:<name>` for entity/pinned rows). When cmdk has not yet
  // reported a value, fall back to the first entity in the active list so the
  // preview mirrors cmdk's default highlight.
  const activeResult = useMemo(() => {
    const fromList = isEmptyQuery ? pinned : entityResults;
    if (fromList.length === 0) return null;

    let id: string | null = null;
    if (activeValue.startsWith("entity:")) {
      // value === `entity:${entity_id}:${canonical_name}`
      const rest = activeValue.slice("entity:".length);
      const sep = rest.indexOf(":");
      id = sep >= 0 ? rest.slice(0, sep) : rest;
    }

    if (isEmptyQuery) {
      const p = (id && pinned.find((x) => x.entity_id === id)) || pinned[0];
      return p
        ? { entity_id: p.entity_id, canonical_name: p.canonical_name, entity_type: p.entity_type }
        : null;
    }
    const r =
      (id && entityResults.find((x) => x.entity_id === id)) || entityResults[0];
    return r
      ? { entity_id: r.entity_id, canonical_name: r.canonical_name, entity_type: r.entity_type }
      : null;
  }, [activeValue, isEmptyQuery, pinned, entityResults]);

  const hasResults =
    entityResults.length > 0 ||
    pageMatches.length > 0 ||
    pinned.length > 0 ||
    recentRows.length > 0 ||
    butlerMatches.length > 0 ||
    sessionMatches.length > 0 ||
    stateMatches.length > 0 ||
    actionMatches.length > 0;

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------
  if (!open) return null;

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions -- backdrop-dismiss is a mouse-only convenience; Escape (handled on the Command panel below) is the real keyboard equivalent, and the overlay is not a focusable target.
    <div
      // eslint-disable-next-line no-restricted-syntax -- the scrim itself; the actual dialog (Command below) is wired through useModalChoreography (rootRef/onKeyDown) — one overlay contract, not a hand-rolled one (bu-qvnce.10).
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 pt-[15vh]"
      onClick={() => setOpen(false)}
      data-testid="entity-finder-backdrop"
    >
      <Command
        ref={rootRef}
        role="dialog"
        aria-modal="true"
        label="Command Menu"
        aria-label="Command Menu"
        onValueChange={setActiveValue}
        className="relative mx-auto flex w-full max-w-3xl overflow-hidden rounded-xl border border-border bg-background shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          // Tab = hop into the active result when a real entity row is
          // active (cmdk does not consume Tab, so we claim it here first).
          // Only when there is NO active result does Tab fall through to the
          // choreography's generic trap — this is what fixes the
          // activeResult-null Tab leak (Pages/Butlers/Sessions/State/Actions
          // rows, or a truly empty result set, used to let Tab escape to
          // whatever rendered after the overlay in the DOM, e.g. the
          // floating chat widget button).
          if (e.key === "Tab" && activeResult) {
            e.preventDefault();
            hopEntity(activeResult.entity_id);
            return;
          }
          choreographyKeyDown(e);
        }}
        shouldFilter={false}
      >
        {/* Left column: input + list + footer */}
        <div className="flex min-w-0 flex-1 flex-col">
          {/* Input row */}
          <div className="flex items-center border-b border-border px-4">
            <span className="mr-2 shrink-0 font-mono text-xs text-muted-foreground">
              /
            </span>
            <Command.Input
              ref={inputRef}
              value={query}
              onValueChange={setQuery}
              placeholder="Search entities, pages, butlers, actions…"
              className="h-12 w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
              data-testid="entity-finder-input"
            />
            <kbd className="ml-2 hidden shrink-0 rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline-block">
              ESC
            </kbd>
          </div>

          <Command.List className="max-h-[420px] flex-1 overflow-y-auto p-2">
            {/* Search error, no fallback data — a failed entity search with
             * nothing cached must surface as an error, not collapse into the
             * "no results" empty copy. Client-side page matches (which never
             * hit the network) still render below. When stale rows ARE
             * present (see the SourceDegradedNote next to the Entities group
             * below), we do NOT take over the whole list here — EntityFinder
             * composes several independent sources (pages/butlers/sessions
             * /state/actions), so blanking the entire palette over one
             * source's error would be too heavy-handed. */}
            {!isLoading && !isEmptyQuery && isError && entityResults.length === 0 && (
              <div
                className="py-6 text-center text-sm text-destructive"
                role="alert"
                data-testid="entity-finder-search-error"
              >
                Search failed. Try again in a moment.
              </div>
            )}

            {/* Empty state */}
            {!isLoading && !isEmptyQuery && !isError && !hasResults && (
              <Command.Empty className="py-6 text-center text-sm text-muted-foreground">
                No results for &ldquo;{query}&rdquo;
              </Command.Empty>
            )}

            {/* Loading indicator */}
            {isLoading && !isEmptyQuery && (
              <div className="py-4 text-center text-xs text-muted-foreground">
                Searching…
              </div>
            )}

            {/* ---------------------------------------------------------------
             * RECENTS — last few entities/pages/actions opened via this
             * Finder (bu-qvnce.11), so the palette is browsable before typing
             * a single character, not just after. Shown ahead of the static
             * owner-pinned set since it reflects what THIS owner actually
             * just did.
             * --------------------------------------------------------------- */}
            {isEmptyQuery && recentRows.length > 0 && (
              <Command.Group
                heading="Recents"
                className="mb-1"
                data-testid="entity-finder-recents-group"
              >
                {recentRows.map((r) => (
                  <Command.Item
                    key={`${r.kind}:${r.id}`}
                    value={`recent:${r.kind}:${r.id}:${r.label}`}
                    onSelect={r.run}
                    className="flex cursor-pointer select-none items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground aria-selected:bg-accent aria-selected:text-accent-foreground"
                    data-testid="entity-finder-recent-item"
                  >
                    {r.kind === "entity" ? (
                      <EntityMark name={r.label} entityType={r.entityType ?? "person"} size={28} />
                    ) : (
                      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded bg-muted font-mono text-xs font-semibold text-muted-foreground">
                        {r.kind === "page" ? "↗" : "⚡"}
                      </span>
                    )}
                    <p className="min-w-0 flex-1 truncate font-medium">{r.label}</p>
                  </Command.Item>
                ))}
              </Command.Group>
            )}

            {/* ---------------------------------------------------------------
             * EMPTY-QUERY OWNER-PINNED SET — the owner's inner circle, top-8
             * neighbours by summed weight (spec: "Finder empty-query state").
             * Typing replaces this set with search results.
             * --------------------------------------------------------------- */}
            {isEmptyQuery && pinned.length > 0 && (
              <Command.Group
                heading="Pinned"
                className="mb-1"
                data-testid="entity-finder-pinned-group"
              >
                {pinned.map((p) => (
                  <Command.Item
                    key={p.entity_id}
                    value={`entity:${p.entity_id}:${p.canonical_name}`}
                    onSelect={() => openEntity(p.entity_id, p.canonical_name, p.entity_type)}
                    className="flex cursor-pointer select-none items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground aria-selected:bg-accent aria-selected:text-accent-foreground"
                    data-testid="entity-finder-pinned-item"
                  >
                    <EntityMark
                      name={p.canonical_name}
                      entityType={p.entity_type}
                      size={28}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{p.canonical_name}</p>
                      <p className="truncate font-mono text-[10px] uppercase text-muted-foreground">
                        inner circle
                      </p>
                    </div>
                    <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
                      {p.weight}
                    </span>
                  </Command.Item>
                ))}
              </Command.Group>
            )}

            {/* ---------------------------------------------------------------
             * ENTITY GROUP — rendered FIRST (entity-first ordering per Brief §5
             * Open Question 14 and bu-xfjwk acceptance criteria).
             * Results are pre-ranked by the server: prefix (100) > contact_fact
             * (70) > substring (50) > predicate (30).
             * --------------------------------------------------------------- */}
            {entityResults.length > 0 && (
              // Never-blank floor (bu-nhcp5): useEntityFinderSearch already
              // pairs placeholderData with the debounced query, so isLoading
              // stays false after the first keystroke — but nothing signalled
              // that a keystroke was re-searching. Dim the stale-but-visible
              // rows for the duration of that background fetch.
              <FetchingDim isFetching={entityFetching && !isLoading}>
                {/* bu-1ukzt: a background refetch on a query that previously
                 * succeeded (placeholderData keeps entityResults populated)
                 * must never render as a truthful all-clear. Surface the
                 * degraded source inline instead of silently keeping the
                 * stale rows with zero error indication. */}
                {!isLoading && isError && (
                  <div data-testid="entity-finder-search-degraded">
                    <SourceDegradedNote
                      label="Entities"
                      detail="search failed — showing previous results"
                      className="mb-1"
                      onRetry={() => refetchEntitySearch()}
                    />
                  </div>
                )}
                <Command.Group
                  heading="Entities"
                  className="mb-1"
                  data-testid="entity-finder-entity-group"
                >
                  {entityResults.map((result) => (
                    <Command.Item
                      key={result.entity_id}
                      value={`entity:${result.entity_id}:${result.canonical_name}`}
                      onSelect={() => openEntity(result.entity_id, result.canonical_name, result.entity_type)}
                      className="flex cursor-pointer select-none items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground aria-selected:bg-accent aria-selected:text-accent-foreground"
                      data-testid="entity-finder-entity-item"
                    >
                      <EntityMark
                        name={result.canonical_name}
                        entityType={result.entity_type}
                        size={28}
                      />

                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium">
                          {result.canonical_name}
                        </p>
                        <p className="truncate font-mono text-[10px] uppercase text-muted-foreground">
                          matched on {matchKindLabel(result.match_kind)}
                        </p>
                      </div>

                      <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
                        score {result.score}
                      </span>
                    </Command.Item>
                  ))}
                </Command.Group>
              </FetchingDim>
            )}

            {/* ---------------------------------------------------------------
             * PAGES GROUP — navigation links, shown after entities. Sourced
             * from the single route registry (route-registry.ts): every
             * route is indexed here, not just the ones promoted to the
             * sidebar (bu-86c4c.7 — fixes /costs, /groups, /approvals/rules,
             * and the health sub-pages being unreachable from the palette).
             * --------------------------------------------------------------- */}
            {pageMatches.length > 0 && (
              <Command.Group heading="Pages">
                {pageMatches.map((page) => (
                  <Command.Item
                    key={page.path}
                    value={`page:${page.path}:${page.label}`}
                    onSelect={() => openPage(page.path, page.label)}
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

            {/* ---------------------------------------------------------------
             * BUTLERS GROUP — absorbed from the legacy CommandPalette.
             * --------------------------------------------------------------- */}
            {butlerMatches.length > 0 && (
              <Command.Group heading="Butlers">
                {butlerMatches.map((b) => (
                  <Command.Item
                    key={b.name}
                    value={`butler:${b.name}`}
                    onSelect={() => openPage(`/butlers/${encodeURIComponent(b.name)}`, b.name)}
                    className="flex cursor-pointer select-none items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground aria-selected:bg-accent aria-selected:text-accent-foreground"
                    data-testid="entity-finder-butler-item"
                  >
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded bg-muted font-mono text-xs font-semibold text-muted-foreground">
                      ⚙
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{b.name}</p>
                      <p className="truncate font-mono text-[10px] uppercase text-muted-foreground">
                        {b.status}
                      </p>
                    </div>
                  </Command.Item>
                ))}
              </Command.Group>
            )}

            {/* ---------------------------------------------------------------
             * SESSIONS / STATE GROUPS — absorbed from the legacy
             * CommandPalette (GET /api/search, debounced).
             * --------------------------------------------------------------- */}
            {/* Never-blank floor (bu-nhcp5): useSearch now pairs
             * placeholderData with its debounced query key, so these groups
             * keep the previous keystroke's rows on screen instead of
             * blanking between debounce-settle and fetch-resolve. Dim them
             * while the new fetch is in flight. */}
            {(sessionMatches.length > 0 || stateMatches.length > 0) && (
              <FetchingDim isFetching={genericFetching}>
                {sessionMatches.length > 0 && (
                  <Command.Group heading="Sessions">
                    {sessionMatches.map((s) => (
                      <Command.Item
                        key={s.id}
                        value={`session:${s.id}:${s.title}`}
                        onSelect={() => openPage(s.url, s.title)}
                        className="flex cursor-pointer select-none items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground aria-selected:bg-accent aria-selected:text-accent-foreground"
                        data-testid="entity-finder-session-item"
                      >
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium">{s.title}</p>
                          {s.snippet && (
                            <p className="truncate font-mono text-[10px] uppercase text-muted-foreground">
                              {s.snippet}
                            </p>
                          )}
                        </div>
                      </Command.Item>
                    ))}
                  </Command.Group>
                )}

                {stateMatches.length > 0 && (
                  <Command.Group heading="State">
                    {stateMatches.map((s) => (
                      <Command.Item
                        key={s.id}
                        value={`state:${s.id}:${s.title}`}
                        onSelect={() => openPage(s.url, s.title)}
                        className="flex cursor-pointer select-none items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground aria-selected:bg-accent aria-selected:text-accent-foreground"
                        data-testid="entity-finder-state-item"
                      >
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium">{s.title}</p>
                          {s.snippet && (
                            <p className="truncate font-mono text-[10px] uppercase text-muted-foreground">
                              {s.snippet}
                            </p>
                          )}
                        </div>
                      </Command.Item>
                    ))}
                  </Command.Group>
                )}
              </FetchingDim>
            )}

            {/* ---------------------------------------------------------------
             * ACTIONS GROUP — per-page command registration API
             * (src/lib/command-registry.tsx). Any mounted component can
             * contribute a command here for as long as it stays mounted.
             * --------------------------------------------------------------- */}
            {actionMatches.length > 0 && (
              <Command.Group heading="Actions">
                {actionMatches.map((action) => (
                  <Command.Item
                    key={action.id}
                    value={`action:${action.id}:${action.label}`}
                    onSelect={() => runAction(action)}
                    className="flex cursor-pointer select-none items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground aria-selected:bg-accent aria-selected:text-accent-foreground"
                    data-testid="entity-finder-action-item"
                  >
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded bg-muted font-mono text-xs font-semibold text-muted-foreground">
                      ⚡
                    </span>
                    <p className="min-w-0 flex-1 truncate font-medium">{action.label}</p>
                    {/* Binding column (bu-qvnce.11) — display-only kbd hint
                        for actions that pair with a page-scoped
                        useRegisterShortcut binding (see PaletteCommand.binding). */}
                    {action.binding && action.binding.length > 0 && (
                      <span
                        className="ml-auto flex shrink-0 items-center gap-1"
                        data-testid="entity-finder-action-binding"
                      >
                        {action.binding.map((key, kidx) => (
                          <KbMono key={kidx}>{key}</KbMono>
                        ))}
                      </span>
                    )}
                  </Command.Item>
                ))}
              </Command.Group>
            )}
          </Command.List>

          {/* Keyboard footer — ↑↓ · ↵ open · ⇥ hop · esc */}
          <div className="flex items-center gap-4 border-t border-border px-4 py-2 font-mono text-[10px] uppercase text-muted-foreground">
            <span className="flex items-center gap-1">
              <KbMono>↑</KbMono>
              <KbMono>↓</KbMono>
            </span>
            <span className="flex items-center gap-1">
              <KbMono>↵</KbMono>
              open
            </span>
            <span className="flex items-center gap-1">
              <KbMono>⇥</KbMono>
              hop
            </span>
            <span className="flex items-center gap-1">
              <KbMono>esc</KbMono>
            </span>
          </div>
        </div>

        {/* Right column: inert preview pane for the active result */}
        <PreviewPane active={activeResult} />
      </Command>
    </div>
  );
}

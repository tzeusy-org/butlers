/**
 * Global entity-first Cmd-K finder (bu-xfjwk, extended for entity-v3 bu-rru9g).
 *
 * Uses the `cmdk` 1.1.1 library wired to
 * GET /api/butlers/relationship/entities/search.
 *
 * Result ordering: entity group is rendered FIRST (highest-scored results
 * from the relationship search endpoint), followed by navigation pages.
 * This fulfils Brief §5 Open Question 14 (entity-first reordering) and
 * Brief §6b Amendment 15 (deterministic Finder — no LLM, no embeddings).
 *
 * entity-v3 additions (spec: dashboard-relationship "Finder preview pane and
 * Tab-to-hop", "Finder empty-query state — owner-pinned set", MODIFIED
 * "App-wide Cmd-K Finder"):
 *   - Right-hand preview pane for the active result (entity mark, name,
 *     type/tier, canned gloss, top-5 relations). Inert — no links. Sourced from
 *     the search response plus at most ONE debounced
 *     GET /entities/{id}/neighbours per active-row change.
 *   - Tab = "hop into": close the Finder and navigate /entities/hop?center=<id>.
 *   - Empty query renders the owner-pinned set (owner's top-8 neighbours by
 *     summed weight), via the same ranked /neighbours endpoint.
 *
 * Keyboard shortcuts:
 *   Cmd/Ctrl+K   — open (global, any focused element)
 *   /            — open (when no input/textarea is focused)
 *   ↑ / ↓        — step through results
 *   Enter        — open result detail
 *   Tab          — hop into the active result (/entities/hop?center=<id>)
 *   Esc          — close
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { navSections, type NavItem } from "@/components/layout/nav-config";
import {
  getEntityGloss,
  type DunbarTier,
  type EntityState,
  type EntityType,
} from "@/lib/entity-glosses";
import { EntityMark } from "@/components/ui/EntityMark";
import { KbMono } from "@/components/ui/KbMono";
import type {
  EntityFinderSearchResult,
  NeighbourEntry,
} from "@/api/index.ts";

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
// state, so the inert preview gloss orients on the entity TYPE only and uses a
// neutral (meaningful / healthy) tier+state baseline. The gloss is a
// non-authoritative hint, never an action surface.
// ---------------------------------------------------------------------------

const PREVIEW_DEFAULT_TIER: DunbarTier = 150;
const PREVIEW_DEFAULT_STATE: EntityState = "healthy";

const ENTITY_TYPE_ALIASES: Record<string, EntityType> = {
  person: "person",
  organization: "organization",
  place: "place",
  location: "place",
  product: "product",
  account: "account",
  email: "account",
  event: "event",
  group: "group",
  other: "other",
};

/** Normalize a raw API entity_type string to a gloss EntityType category. */
function glossCategory(entityType: string): EntityType {
  return ENTITY_TYPE_ALIASES[entityType?.toLowerCase()] ?? "other";
}

/** Human-readable type label for the preview header. */
function typeLabel(entityType: string): string {
  const t = entityType?.toLowerCase() ?? "";
  if (t === "location") return "place";
  if (t === "email") return "account";
  return t || "entity";
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

  const gloss = getEntityGloss({
    tier: PREVIEW_DEFAULT_TIER,
    state: PREVIEW_DEFAULT_STATE,
    category: glossCategory(active.entity_type),
  });

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

      {/* Canned gloss — serif, inert orientation hint */}
      <p
        className="text-xs italic leading-relaxed text-muted-foreground"
        style={{ fontFamily: "var(--font-serif)" }}
        data-testid="entity-finder-preview-gloss"
      >
        {gloss}
      </p>

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
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const { data: searchData, isLoading } = useEntityFinderSearch(query, {
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
      requestAnimationFrame(() => inputRef.current?.focus());
    }
    window.addEventListener(OPEN_ENTITY_FINDER_EVENT, handleOpen);
    return () =>
      window.removeEventListener(OPEN_ENTITY_FINDER_EVENT, handleOpen);
  }, []);

  // -------------------------------------------------------------------------
  // Navigation
  // -------------------------------------------------------------------------
  const openEntity = useCallback(
    (entityId: string) => {
      setOpen(false);
      navigate(`/entities/${encodeURIComponent(entityId)}`);
    },
    [navigate],
  );

  const hopEntity = useCallback(
    (entityId: string) => {
      setOpen(false);
      navigate(`/entities/hop?center=${encodeURIComponent(entityId)}`);
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
  const lowerQuery = trimmedQuery.toLowerCase();
  const pageMatches: PageEntry[] =
    lowerQuery.length >= 1
      ? ALL_PAGES.filter(
          (p) =>
            p.label.toLowerCase().includes(lowerQuery) ||
            p.path.toLowerCase().includes(lowerQuery),
        ).slice(0, 5)
      : [];

  const entityResults: EntityFinderSearchResult[] = useMemo(
    () => searchData?.results ?? [],
    [searchData],
  );

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
    entityResults.length > 0 || pageMatches.length > 0 || pinned.length > 0;

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
        onValueChange={setActiveValue}
        className="relative mx-auto flex w-full max-w-3xl overflow-hidden rounded-xl border border-border bg-background shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            setOpen(false);
            return;
          }
          // Tab = hop into the active result. cmdk does not consume Tab, so we
          // claim it here (only when a real entity row is active).
          if (e.key === "Tab" && activeResult) {
            e.preventDefault();
            hopEntity(activeResult.entity_id);
          }
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
              placeholder="Search entities, pages…"
              className="h-12 w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
              data-testid="entity-finder-input"
            />
            <kbd className="ml-2 hidden shrink-0 rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline-block">
              ESC
            </kbd>
          </div>

          <Command.List className="max-h-[420px] flex-1 overflow-y-auto p-2">
            {/* Empty state */}
            {!isLoading && !isEmptyQuery && !hasResults && (
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
                    onSelect={() => openEntity(p.entity_id)}
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

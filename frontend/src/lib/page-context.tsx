/**
 * Page-context capture (bu-p6ey8.4 — "Page context capture").
 *
 * A lightweight React context, mounted once in RootLayout.tsx, that lets the
 * floating chat widget attach `page_context` to outgoing messages so the
 * routed butler receives grounded context for the owner's statement (e.g.
 * "the entity currently in view was Alice" when correcting a fact from
 * `/entities/concentration?predicate=child-of`).
 *
 * Two halves:
 *   - `usePageContext().set({ entity_ref })` — pages call this (in an effect)
 *     to enrich the context with an entity/subject reference. Route path and
 *     query params are captured automatically; pages never need to set those.
 *   - `usePageContextCapture()` — returns a `capture()` function the widget
 *     calls at send time. It reads the CURRENT route/query (via
 *     `useLocation`) and whatever enrichment the mounted page contributed,
 *     and returns a plain snapshot object. Because the snapshot is a fresh
 *     object built at call time, later mutations to the route or the
 *     enrichment ref never retroactively change an already-sent payload.
 *
 * Enrichment ownership: only one page enriches at a time in practice (one
 * route is mounted at once), but ownership is still tracked via a per-caller
 * token so an old page's unmount-cleanup can never clobber a newer page's
 * enrichment if effect cleanup/mount ordering ever overlaps.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";
import { useLocation } from "react-router";

import type { PageContext } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Enrichment a page can contribute on top of the auto-captured route/query. */
export interface PageContextEnrichment {
  entity_ref?: string | null;
}

interface EnrichmentSlot {
  enrichment: PageContextEnrichment;
  /** Identifies which `usePageContext()` caller currently owns the slot. */
  ownerToken: number;
}

interface PageContextInternalValue {
  slotRef: React.MutableRefObject<EnrichmentSlot>;
}

const EMPTY_SLOT: EnrichmentSlot = { enrichment: {}, ownerToken: 0 };

const PageContextInternalContext = createContext<PageContextInternalValue | null>(null);

let ownerTokenCounter = 0;

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function PageContextProvider({ children }: { children: ReactNode }) {
  const slotRef = useRef<EnrichmentSlot>({ ...EMPTY_SLOT });
  const value = useMemo<PageContextInternalValue>(() => ({ slotRef }), []);

  return (
    <PageContextInternalContext.Provider value={value}>
      {children}
    </PageContextInternalContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// usePageContext — pages call `.set()` to enrich
// ---------------------------------------------------------------------------

export interface UsePageContextResult {
  /** Merge enrichment (currently just `entity_ref`) into the page context. */
  set: (enrichment: PageContextEnrichment) => void;
}

/**
 * Lets the calling page contribute enrichment (e.g. `entity_ref`) to the
 * page context the chat widget snapshots at send time. Automatically
 * releases its enrichment on unmount so navigating away from an enriched
 * page never leaves a stale `entity_ref` attached to messages sent from a
 * later, unrelated page.
 */
export function usePageContext(): UsePageContextResult {
  const ctx = useContext(PageContextInternalContext);
  const ownerTokenRef = useRef<number | null>(null);

  const set = useCallback(
    (enrichment: PageContextEnrichment) => {
      if (!ctx) return;
      if (ownerTokenRef.current === null) {
        ownerTokenRef.current = ++ownerTokenCounter;
      }
      ctx.slotRef.current = { enrichment, ownerToken: ownerTokenRef.current };
    },
    [ctx],
  );

  useEffect(() => {
    return () => {
      if (!ctx || ownerTokenRef.current === null) return;
      // Only clear if this caller still owns the slot — guards against a
      // stale unmount clobbering enrichment a newer page already set.
      if (ctx.slotRef.current.ownerToken === ownerTokenRef.current) {
        ctx.slotRef.current = { ...EMPTY_SLOT };
      }
    };
  }, [ctx]);

  return useMemo(() => ({ set }), [set]);
}

// ---------------------------------------------------------------------------
// usePageContextCapture — the widget's send-time snapshot
// ---------------------------------------------------------------------------

/**
 * Returns a `capture()` function that builds a `PageContext` snapshot from
 * the CURRENT route/query plus whatever enrichment is active right now.
 * Call it at send time, not before — the widget must call this fresh for
 * every message so a page navigation or `set()` call between two sends is
 * reflected, while a change AFTER a given send never mutates that message's
 * already-built payload (the return value is a new plain object each call).
 *
 * Deliberately NOT wrapped in `useCallback`: the whole point is that
 * `ctx.slotRef.current` is read fresh on every invocation rather than
 * closed over, since enrichment changes (`usePageContext().set(...)`) do
 * not themselves trigger a re-render here. A memoized closure would either
 * go stale between renders or need `.current` in its dependency array,
 * which defeats the ref's purpose.
 */
export function usePageContextCapture(): () => PageContext {
  const ctx = useContext(PageContextInternalContext);
  const location = useLocation();

  return (): PageContext => {
    const params = new URLSearchParams(location.search);
    const query_params = Object.fromEntries(params.entries());
    const entity_ref = ctx?.slotRef.current.enrichment.entity_ref ?? null;

    const snapshot: PageContext = { route: location.pathname };
    if (Object.keys(query_params).length > 0) {
      snapshot.query_params = query_params;
    }
    if (entity_ref != null) {
      snapshot.entity_ref = entity_ref;
    }
    return snapshot;
  };
}

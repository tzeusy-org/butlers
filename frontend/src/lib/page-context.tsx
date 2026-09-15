/**
 * Page-context capture (bu-p6ey8.4 — "Page context capture"; typed
 * visible_resource + per-route contextPolicy added by bu-0ynlk.4).
 *
 * A lightweight React context, mounted once in RootLayout.tsx, that lets the
 * dashboard's chat surfaces (FloatingChatWidget, ChatPanel) attach
 * `page_context` to outgoing messages so the routed butler receives
 * grounded context for the owner's statement (e.g. "the entity currently in
 * view was Alice" when correcting a fact from
 * `/entities/concentration?predicate=child-of`).
 *
 * Three halves:
 *   - `usePageSubject().set({ visible_resource, visible_summary, entity_ref })`
 *     — pages call this (in an effect) to enrich the context with a typed
 *     resource pointer. Route path and query params are captured
 *     automatically; pages never need to set those.
 *   - `usePageContextCapture()` — returns a `capture()` function the chat
 *     surfaces call at send time (and the ContextChip calls on every render
 *     to preview what would be sent). It reads the CURRENT route/query (via
 *     `useLocation`), the mounted page's enrichment, and the route's
 *     `contextPolicy` from the registry, and returns a `PageContextSnapshot`.
 *     Because the snapshot is a fresh object built at call time, later
 *     mutations to the route or the enrichment ref never retroactively
 *     change an already-sent payload.
 *   - `ContextPolicy` re-exported from the registry so callers (ContextChip,
 *     the chat surfaces) don't need a second import.
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

import type { PageContext, PageContextVisibleResource } from "@/api/types.ts";
import {
  resolvePageContextDescriptor,
  type ContextPolicy,
} from "@/lib/page-context-registry.ts";

export type { ContextPolicy } from "@/lib/page-context-registry.ts";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Enrichment a page can contribute on top of the auto-captured route/query. */
export interface PageContextEnrichment {
  entity_ref?: string | null;
  visible_resource?: PageContextVisibleResource | null;
  visible_summary?: string | null;
}

interface EnrichmentSlot {
  enrichment: PageContextEnrichment;
  /** Identifies which `usePageSubject()` caller currently owns the slot. */
  ownerToken: number;
}

interface PageContextInternalValue {
  slotRef: React.MutableRefObject<EnrichmentSlot>;
}

/** A resolved page-context snapshot, ready to preview (ContextChip) or send. */
export interface PageContextSnapshot {
  policy: ContextPolicy;
  /** Fallback label (registry default, or the page's own `visible_summary`). */
  label: string;
  /** `null` when `policy` is `"none"`; otherwise the exact payload to send. */
  context: PageContext | null;
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
// usePageSubject — pages call `.set()` to enrich
// ---------------------------------------------------------------------------

export interface UsePageSubjectResult {
  /** Merge enrichment into the page context. */
  set: (enrichment: PageContextEnrichment) => void;
}

/**
 * Lets the calling page contribute enrichment (`visible_resource`,
 * `visible_summary`, or the legacy `entity_ref`) to the page context the
 * chat surfaces snapshot at send time. Automatically releases its
 * enrichment on unmount so navigating away from an enriched page never
 * leaves stale state attached to messages sent from a later, unrelated
 * page.
 */
export function usePageSubject(): UsePageSubjectResult {
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
// usePageContextCapture — the chat surfaces' send-time (and chip preview) snapshot
// ---------------------------------------------------------------------------

/**
 * Returns a `capture()` function that builds a `PageContextSnapshot` from
 * the CURRENT route/query, whatever enrichment is active right now, and the
 * route's registry `contextPolicy`. Call it at send time, not before — the
 * caller must invoke this fresh for every message so a page navigation or
 * `set()` call between two sends is reflected, while a change AFTER a given
 * send never mutates that message's already-built payload (the return
 * value is a new plain object each call). The ContextChip calls it on every
 * render instead, for a live pre-send preview.
 *
 * Deliberately NOT wrapped in `useCallback`: the whole point is that
 * `ctx.slotRef.current` is read fresh on every invocation rather than
 * closed over, since enrichment changes (`usePageSubject().set(...)`) do
 * not themselves trigger a re-render here. A memoized closure would either
 * go stale between renders or need `.current` in its dependency array,
 * which defeats the ref's purpose.
 */
export function usePageContextCapture(): () => PageContextSnapshot {
  const ctx = useContext(PageContextInternalContext);
  const location = useLocation();

  return (): PageContextSnapshot => {
    const descriptor = resolvePageContextDescriptor(location.pathname);
    const enrichment = ctx?.slotRef.current.enrichment ?? {};
    const label = enrichment.visible_summary ?? descriptor.summary;

    if (descriptor.policy === "none") {
      return { policy: "none", label, context: null };
    }

    if (descriptor.policy === "ref-only") {
      return { policy: "ref-only", label, context: { route: location.pathname } };
    }

    const params = new URLSearchParams(location.search);
    const query_params = Object.fromEntries(params.entries());

    const snapshot: PageContext = { route: location.pathname };
    if (Object.keys(query_params).length > 0) {
      snapshot.query_params = query_params;
    }
    if (enrichment.entity_ref != null) {
      snapshot.entity_ref = enrichment.entity_ref;
    }
    if (enrichment.visible_resource != null) {
      snapshot.visible_resource = enrichment.visible_resource;
    }
    if (enrichment.visible_summary != null) {
      snapshot.visible_summary = enrichment.visible_summary;
    }
    return { policy: "snapshot", label, context: snapshot };
  };
}

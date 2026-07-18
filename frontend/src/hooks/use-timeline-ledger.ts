/**
 * useTimelineLedger — live-tail + pagination state for the fleet chronicle
 * (/timeline, bu-86c4c.10 — "One Timeline").
 *
 * The head page (no `before` cursor) is kept perpetually fresh via
 * useTimeline's poll (see use-timeline.ts) plus WS-driven invalidation (the
 * `["timeline"]` query key is invalidated by event-cache-registry.ts on
 * "session"/"notification" fleet events). While the owner is pinned to now,
 * the freshest head page is rendered directly. The moment they page into
 * history (Load older), the view is "committed" to a fixed snapshot so
 * loading older pages doesn't fight with the live head refetching out from
 * under them — new arrivals are counted and surfaced as an "N new events"
 * pill instead of silently reordering the list underneath a scrolled reader.
 *
 * Spec: docs/redesigns/2026-07-03-jarvis-audit.md §"7. One Timeline"
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { getTimeline } from "@/api/index.ts";
import type { TimelineEvent, TimelineHeartbeatRollup } from "@/api/types.ts";
import { useTimeline } from "./use-timeline";

const PAGE_SIZE = 50;
/** Head page poll interval — WS invalidation (event-cache-registry) fires
 *  sooner in the common case; this is the honest floor when the socket is
 *  down or the fleet event bus hasn't emitted a matching type. */
const HEAD_POLL_MS = 15_000;

export interface TimelineLedgerFilters {
  butler?: string[];
  event_type?: string[];
  trace?: string;
}

export interface UseTimelineLedgerResult {
  /** Events currently rendered, newest first. */
  events: TimelineEvent[];
  /** True only on the very first fetch with no data to show yet. */
  isLoading: boolean;
  /** A head-page refresh is in flight while the existing ledger stays visible. */
  isFetching: boolean;
  /** A fetch failure with nothing usable to show — render the error state. */
  isError: boolean;
  /**
   * True whenever the live head poll is currently failing, even if stale
   * cached events are still on screen. Without this, a dead API after the
   * first successful paint looks identical to a genuinely quiet fleet —
   * both just stop showing new events (bu-qvnce.2).
   */
  isLiveFeedDown: boolean;
  refetch: () => void;
  /** True when there is an older page beyond what's currently loaded. */
  hasMore: boolean;
  loadMore: () => void;
  isLoadingMore: boolean;
  /** True while following the live head (no older pages loaded). */
  pinned: boolean;
  /** Count of newly-arrived events not yet shown, while unpinned. */
  newCount: number;
  /** Jump back to the live head, discarding accumulated older pages. */
  showNewEvents: () => void;
  degradedSources: string[];
  heartbeatRollup: TimelineHeartbeatRollup;
}

const EMPTY_ROLLUP: TimelineHeartbeatRollup = { ticks: 0, butlers: 0, failed: 0 };

export function useTimelineLedger(filters: TimelineLedgerFilters): UseTimelineLedgerResult {
  const qc = useQueryClient();
  const filtersKey = JSON.stringify(filters);

  const head = useTimeline({ ...filters, limit: PAGE_SIZE }, { refetchInterval: HEAD_POLL_MS });

  const [pinned, setPinned] = useState(true);
  const [committed, setCommitted] = useState<TimelineEvent[] | null>(null);
  const [committedCursor, setCommittedCursor] = useState<string | undefined>(undefined);
  const [isLoadingMore, setIsLoadingMore] = useState(false);

  // A filter change invalidates any accumulated history — start over, live.
  const prevFiltersKeyRef = useRef(filtersKey);
  useEffect(() => {
    if (prevFiltersKeyRef.current === filtersKey) return;
    prevFiltersKeyRef.current = filtersKey;
    setPinned(true);
    setCommitted(null);
    setCommittedCursor(undefined);
  }, [filtersKey]);

  const headData = head.data?.data;
  const headEvents = useMemo(() => headData ?? [], [headData]);
  const committedIds = useMemo(
    () => (committed ? new Set(committed.map((e) => e.id)) : null),
    [committed],
  );

  // Newly-arrived head events not yet reflected in the committed snapshot —
  // only meaningful while unpinned (pinned view already renders them).
  const newCount = useMemo(() => {
    if (pinned || !committedIds) return 0;
    return headEvents.filter((e) => !committedIds.has(e.id)).length;
  }, [pinned, committedIds, headEvents]);

  const events = pinned ? headEvents : (committed ?? headEvents);

  const hasMore = pinned ? (head.data?.meta.has_more ?? false) : committedCursor !== undefined;

  const loadMore = useCallback(() => {
    const baseline = committed ?? headEvents;
    const baselineCursor = committed !== null ? committedCursor : head.data?.meta.cursor;
    if (!baselineCursor) return;

    setPinned(false);
    setIsLoadingMore(true);
    const params = { ...filters, limit: PAGE_SIZE, before: baselineCursor };
    qc.fetchQuery({
      queryKey: ["timeline", params],
      queryFn: () => getTimeline(params),
    })
      .then((page) => {
        setCommitted([...baseline, ...page.data]);
        setCommittedCursor(page.meta.has_more ? (page.meta.cursor ?? undefined) : undefined);
      })
      .catch((err) => {
        console.error("Failed to load older timeline events:", err);
      })
      .finally(() => setIsLoadingMore(false));
    // filters is a fresh object each render; filtersKey is the stable dep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [committed, committedCursor, headEvents, head.data, qc, filtersKey]);

  const showNewEvents = useCallback(() => {
    setPinned(true);
    setCommitted(null);
    setCommittedCursor(undefined);
  }, []);

  return {
    events,
    isLoading: head.isLoading && events.length === 0,
    isFetching: head.isFetching && events.length > 0,
    isError: !!head.isError && events.length === 0,
    isLiveFeedDown: !!head.isError,
    refetch: () => void head.refetch(),
    hasMore,
    loadMore,
    isLoadingMore,
    pinned,
    newCount,
    showNewEvents,
    degradedSources: head.data?.meta.degraded_sources ?? [],
    heartbeatRollup: head.data?.meta.heartbeat_rollup ?? EMPTY_ROLLUP,
  };
}

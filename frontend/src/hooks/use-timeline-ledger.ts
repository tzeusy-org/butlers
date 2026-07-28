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
  /** A failed older-page request; retained rows/cursor remain retryable. */
  loadMoreError: boolean;
  /** Reissues the exact retained older-page cursor after a failed request. */
  retryLoadMore: () => void;
  isLoadingMore: boolean;
  /** True while following the live head (no older pages loaded). */
  pinned: boolean;
  /** Count of newly-arrived events not yet shown, while unpinned. */
  newCount: number;
  /** Jump back to the live head, discarding accumulated older pages. */
  showNewEvents: () => void;
  degradedSources: string[];
  /** Named session pools unavailable within the currently rendered snapshot. */
  degradedButlers: string[];
  heartbeatRollup: TimelineHeartbeatRollup;
}

const EMPTY_ROLLUP: TimelineHeartbeatRollup = { ticks: 0, butlers: 0, failed: 0 };

function mergeDistinct(left: string[], right: string[] | undefined): string[] {
  return [...new Set([...left, ...(right ?? [])])];
}

export function useTimelineLedger(filters: TimelineLedgerFilters): UseTimelineLedgerResult {
  const qc = useQueryClient();
  const filtersKey = JSON.stringify(filters);

  const head = useTimeline({ ...filters, limit: PAGE_SIZE }, { refetchInterval: HEAD_POLL_MS });

  const [pinned, setPinned] = useState(true);
  const [committed, setCommitted] = useState<TimelineEvent[] | null>(null);
  const [committedCursor, setCommittedCursor] = useState<string | undefined>(undefined);
  const [committedDegradedSources, setCommittedDegradedSources] = useState<string[] | null>(null);
  const [committedDegradedButlers, setCommittedDegradedButlers] = useState<string[] | null>(null);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState(false);
  const paginationRequestRef = useRef(0);

  // A filter change invalidates any accumulated history — start over, live.
  const prevFiltersKeyRef = useRef(filtersKey);
  useEffect(() => {
    if (prevFiltersKeyRef.current === filtersKey) return;
    prevFiltersKeyRef.current = filtersKey;
    paginationRequestRef.current += 1;
    setPinned(true);
    setCommitted(null);
    setCommittedCursor(undefined);
    setCommittedDegradedSources(null);
    setCommittedDegradedButlers(null);
    setLoadMoreError(false);
    setIsLoadingMore(false);
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
    if (isLoadingMore) return;
    const baseline = committed ?? headEvents;
    const baselineCursor = committed !== null ? committedCursor : head.data?.meta.cursor;
    if (!baselineCursor) return;
    const baselineDegradedSources =
      committed !== null
        ? (committedDegradedSources ?? [])
        : (head.data?.meta.degraded_sources ?? []);
    const baselineDegradedButlers =
      committed !== null
        ? (committedDegradedButlers ?? [])
        : (head.data?.meta.degraded_butlers ?? []);
    const requestId = ++paginationRequestRef.current;

    setPinned(false);
    // Commit before fetching so a failed older-page request neither lets a
    // live-head refresh rewrite history nor drops the exact cursor to retry.
    setCommitted(baseline);
    setCommittedCursor(baselineCursor);
    setCommittedDegradedSources(baselineDegradedSources);
    setCommittedDegradedButlers(baselineDegradedButlers);
    setLoadMoreError(false);
    setIsLoadingMore(true);
    const params = { ...filters, limit: PAGE_SIZE, before: baselineCursor };
    qc.fetchQuery({
      queryKey: ["timeline", params],
      queryFn: () => getTimeline(params),
    })
      .then((page) => {
        if (paginationRequestRef.current !== requestId) return;
        setCommitted([...baseline, ...page.data]);
        setCommittedCursor(page.meta.has_more ? (page.meta.cursor ?? undefined) : undefined);
        setCommittedDegradedSources(
          mergeDistinct(baselineDegradedSources, page.meta.degraded_sources),
        );
        setCommittedDegradedButlers(
          mergeDistinct(baselineDegradedButlers, page.meta.degraded_butlers),
        );
      })
      .catch((err) => {
        if (paginationRequestRef.current !== requestId) return;
        console.error("Failed to load older timeline events:", err);
        setLoadMoreError(true);
      })
      .finally(() => {
        if (paginationRequestRef.current === requestId) setIsLoadingMore(false);
      });
    // filters is a fresh object each render; filtersKey is the stable dep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    committed,
    committedCursor,
    committedDegradedButlers,
    committedDegradedSources,
    headEvents,
    head.data,
    isLoadingMore,
    qc,
    filtersKey,
  ]);

  const showNewEvents = useCallback(() => {
    paginationRequestRef.current += 1;
    setPinned(true);
    setCommitted(null);
    setCommittedCursor(undefined);
    setCommittedDegradedSources(null);
    setCommittedDegradedButlers(null);
    setLoadMoreError(false);
    setIsLoadingMore(false);
  }, []);

  const degradedSources = pinned
    ? (head.data?.meta.degraded_sources ?? [])
    : (committedDegradedSources ?? head.data?.meta.degraded_sources ?? []);
  const degradedButlers = pinned
    ? (head.data?.meta.degraded_butlers ?? [])
    : (committedDegradedButlers ?? head.data?.meta.degraded_butlers ?? []);

  return {
    events,
    isLoading: head.isLoading && events.length === 0,
    isFetching: head.isFetching && events.length > 0,
    isError: !!head.isError && events.length === 0,
    isLiveFeedDown: !!head.isError,
    refetch: () => void head.refetch(),
    hasMore,
    loadMore,
    loadMoreError,
    retryLoadMore: loadMore,
    isLoadingMore,
    pinned,
    newCount,
    showNewEvents,
    degradedSources,
    degradedButlers,
    heartbeatRollup: head.data?.meta.heartbeat_rollup ?? EMPTY_ROLLUP,
  };
}

import { useEffect, useLayoutEffect, useRef } from "react";
import { useLocation, useNavigationType } from "react-router";

import { useShellScrollContainerRef } from "@/components/layout/ShellScrollContext";

/**
 * Routes that deliberately own an inner scroll surface instead of the shell's
 * persistent `<main>` (the calendar time grid and chat message thread).
 *
 * The data attribute is also available to future route roots that are not
 * known by this shared hook yet. It makes the ownership decision explicit at
 * the route boundary rather than relying on a page-specific scroll effect.
 */
export const SHELL_SCROLL_OWNER_ATTRIBUTE = "data-shell-scroll-owner";
export const SHELL_SCROLL_OWNER_VALUE = "route";

const HISTORY_SCROLL_STATE_KEY = "__butlers_shell_scroll_memory";
const scrollPositions = new Map<string, number>();

interface HistoryScrollState {
  [key: string]: unknown;
}

interface PendingRestore {
  key: string;
  offset: number;
  retried: boolean;
  applied: boolean;
}

function isRecord(value: unknown): value is HistoryScrollState {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function historyScrollPositions(): Record<string, number> {
  if (typeof window === "undefined" || !isRecord(window.history.state))
    return {};
  const stored = window.history.state[HISTORY_SCROLL_STATE_KEY];
  if (!isRecord(stored)) return {};

  const positions: Record<string, number> = {};
  for (const [key, value] of Object.entries(stored)) {
    if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
      positions[key] = value;
    }
  }
  return positions;
}

function rememberScrollPosition(locationKey: string, offset: number): void {
  if (!Number.isFinite(offset) || offset < 0) return;
  scrollPositions.set(locationKey, offset);

  if (typeof window === "undefined") return;
  const state = isRecord(window.history.state) ? window.history.state : {};
  const positions = {
    ...historyScrollPositions(),
    [locationKey]: offset,
  };
  try {
    window.history.replaceState(
      { ...state, [HISTORY_SCROLL_STATE_KEY]: positions },
      "",
      window.location.href,
    );
  } catch {
    // A browser-hosted app can always replace its own entry. Isolated embeds
    // and test harnesses may forbid it; the in-memory map still restores POP.
  }
}

function rememberedScrollPosition(locationKey: string): number | null {
  const inMemory = scrollPositions.get(locationKey);
  if (inMemory !== undefined) return inMemory;
  const fromHistory = historyScrollPositions()[locationKey];
  return fromHistory === undefined ? null : fromHistory;
}

function routeOwnsInnerScroll(
  pathname: string,
  main: HTMLElement | null,
): boolean {
  if (
    pathname === "/calendar" ||
    pathname === "/chat" ||
    pathname.startsWith("/chat/")
  ) {
    return true;
  }

  const routeRoot = main?.firstElementChild;
  return (
    routeRoot?.getAttribute(SHELL_SCROLL_OWNER_ATTRIBUTE) ===
    SHELL_SCROLL_OWNER_VALUE
  );
}

function requestFrame(callback: FrameRequestCallback): number {
  if (typeof window.requestAnimationFrame === "function") {
    return window.requestAnimationFrame(callback);
  }
  return window.setTimeout(() => callback(performance.now()), 0);
}

function cancelFrame(frame: number): void {
  if (typeof window.cancelAnimationFrame === "function") {
    window.cancelAnimationFrame(frame);
  } else {
    window.clearTimeout(frame);
  }
}

function writeScrollTop(container: HTMLElement, offset: number): void {
  container.scrollTop = offset;
}

/**
 * Remember and restore the shell's one persistent scroll surface at history
 * boundaries.
 *
 * The first mount is intentionally treated as a reload: browser history may
 * retain the location key, but the route's data and layout can be different,
 * so the shell always starts at the top. Scroll positions are mirrored into
 * history.state while the app is alive and kept in a module map so a PUSH can
 * save the outgoing entry before the router creates the next browser entry.
 */
export function useShellScrollMemory(enabled = true): void {
  const mainScrollContainerRef = useShellScrollContainerRef();
  const location = useLocation();
  const navigationType = useNavigationType();
  const activeLocationKeyRef = useRef(location.key);
  const previousLocationRef = useRef<{
    key: string;
    ownsInnerScroll: boolean;
  } | null>(null);
  const ownsInnerScrollRef = useRef(false);
  const pendingRestoreRef = useRef<PendingRestore | null>(null);
  const pendingFrameRef = useRef<number | null>(null);

  useLayoutEffect(() => {
    const main = mainScrollContainerRef?.current;
    if (!main || !enabled) return;

    if (pendingFrameRef.current !== null) {
      cancelFrame(pendingFrameRef.current);
      pendingFrameRef.current = null;
    }
    pendingRestoreRef.current = null;

    const ownsInnerScroll = routeOwnsInnerScroll(location.pathname, main);
    ownsInnerScrollRef.current = ownsInnerScroll;

    const previousLocation = previousLocationRef.current;
    if (previousLocation === null) {
      // A fresh document load must not inherit a stale browser scroll offset.
      activeLocationKeyRef.current = location.key;
      previousLocationRef.current = { key: location.key, ownsInnerScroll };
      if (!ownsInnerScroll) writeScrollTop(main, 0);
      return;
    }

    if (previousLocation.key === location.key) return;

    // The scroll listener normally records this before navigation. Keep this
    // boundary write as a fallback for programmatic scrollTop changes.
    activeLocationKeyRef.current = location.key;
    if (!previousLocation.ownsInnerScroll) {
      rememberScrollPosition(previousLocation.key, main.scrollTop);
    }

    previousLocationRef.current = { key: location.key, ownsInnerScroll };
    if (ownsInnerScroll) return;

    if (navigationType !== "POP") {
      pendingRestoreRef.current = null;
      writeScrollTop(main, 0);
      return;
    }

    const offset = rememberedScrollPosition(location.key);
    if (offset === null) {
      writeScrollTop(main, 0);
      return;
    }
    pendingRestoreRef.current = {
      key: location.key,
      offset,
      retried: false,
      applied: false,
    };
  }, [
    enabled,
    location.key,
    location.pathname,
    mainScrollContainerRef,
    navigationType,
  ]);

  useEffect(() => {
    if (!enabled || !mainScrollContainerRef) return;
    const main = mainScrollContainerRef.current;
    if (!main) return;

    const onScroll = () => {
      if (ownsInnerScrollRef.current) return;
      rememberScrollPosition(activeLocationKeyRef.current, main.scrollTop);
    };
    main.addEventListener("scroll", onScroll, { passive: true });
    return () => main.removeEventListener("scroll", onScroll);
  }, [enabled, mainScrollContainerRef]);

  useEffect(() => {
    if (!enabled || !mainScrollContainerRef) return;
    const pendingRestore = pendingRestoreRef.current;
    if (!pendingRestore || pendingRestore.key !== location.key) return;

    const main = mainScrollContainerRef.current;
    if (!main) return;

    let cancelled = false;
    const applyRestore = () => {
      if (cancelled || pendingRestore.applied) return;
      const maxReachableOffset = Math.max(0, main.scrollHeight - main.clientHeight);
      if (maxReachableOffset >= pendingRestore.offset) {
        writeScrollTop(main, pendingRestore.offset);
        pendingRestore.applied = true;
        pendingRestoreRef.current = null;
        return;
      }

      // The route may have painted its shared skeleton first. Give it exactly
      // one more frame to reveal real content, then leave the offset alone if
      // the route still cannot accommodate it.
      if (pendingRestore.retried) {
        pendingRestoreRef.current = null;
        return;
      }
      pendingRestore.retried = true;
      pendingFrameRef.current = requestFrame(() => {
        pendingFrameRef.current = null;
        applyRestore();
      });
    };

    applyRestore();
    return () => {
      cancelled = true;
      if (pendingFrameRef.current !== null) {
        cancelFrame(pendingFrameRef.current);
        pendingFrameRef.current = null;
      }
    };
  }, [enabled, location.key, mainScrollContainerRef]);
}

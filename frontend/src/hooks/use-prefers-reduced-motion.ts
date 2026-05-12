/**
 * usePrefersReducedMotion
 *
 * Returns true when the user's OS/browser has requested reduced motion via the
 * `prefers-reduced-motion: reduce` media query.  Components should disable or
 * minimise animations when this is true.
 *
 * Uses matchMedia with an event listener so the value updates in real time if
 * the user toggles their OS accessibility setting while the page is open.
 */

import { useEffect, useState } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

export function usePrefersReducedMotion(): boolean {
  const [prefersReduced, setPrefersReduced] = useState<boolean>(
    () => typeof window !== "undefined" && window.matchMedia(QUERY).matches,
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia(QUERY);
    const handler = (e: MediaQueryListEvent) => setPrefersReduced(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  return prefersReduced;
}

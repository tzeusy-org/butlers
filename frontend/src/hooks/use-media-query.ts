/**
 * useMediaQuery — reactive `window.matchMedia` subscription.
 *
 * Same shape as `usePrefersReducedMotion` (`./use-prefers-reduced-motion.ts`),
 * generalized to an arbitrary query so the chat posture host (bu-0ynlk.11)
 * can react to the `xl` breakpoint crossing.
 */

import { useEffect, useState } from "react";

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(
    () => typeof window !== "undefined" && window.matchMedia(query).matches,
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia(query);
    const handler = (e: MediaQueryListEvent) => setMatches(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [query]);

  return matches;
}

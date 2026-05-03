/**
 * useViewport -- returns the current window inner width and a boolean indicating
 * whether the viewport is considered "mobile" (≤640px).
 *
 * Why 640px: six concentric rings at the minimum readable node size (≥8px radius)
 * require a stage diameter of roughly 400px. Below 640px the rings become too
 * cramped to be readable, so we switch to the horizontal-strata layout.
 *
 * Uses matchMedia for the boolean check (single listener, no debounce needed)
 * and a resize listener for the raw width (used by SocialMapPage to pass to
 * whichever canvas is active).
 */

import { useEffect, useState } from "react";

const MOBILE_BREAKPOINT = 640;

export function useViewport() {
  const [width, setWidth] = useState(() => window.innerWidth);
  const [isMobile, setIsMobile] = useState(
    () => window.innerWidth <= MOBILE_BREAKPOINT,
  );

  useEffect(() => {
    function handleResize() {
      const w = window.innerWidth;
      setWidth(w);
      setIsMobile(w <= MOBILE_BREAKPOINT);
    }
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  return { width, isMobile };
}

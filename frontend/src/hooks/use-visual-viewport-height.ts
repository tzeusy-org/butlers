/**
 * useVisualViewportHeight
 *
 * Tracks `window.visualViewport`'s height so a fixed-position panel (the
 * floating chat widget) can shrink to stay above an on-screen mobile
 * keyboard. `100dvh`/`70vh` alone do not react to the keyboard on iOS/Android
 * browsers — only the visualViewport resizes when the keyboard opens.
 *
 * Returns `null` when `visualViewport` is unavailable (older browsers, SSR,
 * jsdom in tests) so callers can fall back to a CSS-only height.
 */

import { useEffect, useState } from "react";

export function useVisualViewportHeight(): number | null {
  const [height, setHeight] = useState<number | null>(() =>
    typeof window !== "undefined" && window.visualViewport ? window.visualViewport.height : null,
  );

  useEffect(() => {
    if (typeof window === "undefined" || !window.visualViewport) return;
    const vv = window.visualViewport;
    function handleResize() {
      setHeight(vv.height);
    }
    vv.addEventListener("resize", handleResize);
    return () => vv.removeEventListener("resize", handleResize);
  }, []);

  return height;
}

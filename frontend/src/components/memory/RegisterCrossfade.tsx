// ---------------------------------------------------------------------------
// RegisterCrossfade — opacity-only cross-fade between browse registers (bu-9qekw)
//
// MEMORY_LANGUAGE.md §8 permits exactly one addition to the Dispatch motion
// table for the register pills:
//
//   | Register cross-fade on pill switch | 200ms | cubic-bezier(0.22, 1, 0.36, 1) |
//
// and §8 closes with "Numerals never count up." So the cross-fade is:
//   - opacity-only — NO scale, NO slide (no transform of any kind);
//   - 200ms, cubic-bezier(0.22, 1, 0.36, 1);
//   - must NOT re-mount the focused register's subtree on an unrelated re-render
//     (a remount would re-run the register's mount effects; numerals must stay put).
//
// Mechanism: keep at most two layers — the currently-shown register and, while a
// switch is in flight, the outgoing one. The incoming layer fades 0 → 1; the
// outgoing layer fades 1 → 0 and is dropped after the transition. Children are
// keyed by `activeKey`, so React preserves a register's subtree across re-renders
// with the same key (no spurious remount / numeral re-animation).
//
// Under `prefers-reduced-motion`, the transition is dropped and the active layer
// renders at full opacity immediately (instant swap), per the project's
// usePrefersReducedMotion convention.
//
// Binding doc: (memory house-ledger redesign, graduated) MEMORY_LANGUAGE.md §8
// ---------------------------------------------------------------------------

import { useEffect, useRef, useState, type ReactNode } from "react";

import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";

/** §8 cross-fade duration, in milliseconds. */
export const REGISTER_CROSSFADE_DURATION_MS = 200;
/** §8 cross-fade easing. */
export const REGISTER_CROSSFADE_EASING = "cubic-bezier(0.22, 1, 0.36, 1)";

interface RegisterCrossfadeProps {
  /**
   * Identity of the currently-active register. A change triggers the cross-fade;
   * an unchanged value preserves the existing subtree (no remount).
   */
  activeKey: string;
  /** The focused register to render for `activeKey`. */
  children: ReactNode;
}

interface Layer {
  key: string;
  node: ReactNode;
}

/**
 * Opacity-only cross-fade wrapper for the /memory browse registers.
 *
 * Renders the active register; on `activeKey` change, briefly overlays the
 * outgoing register (fading out) beneath the incoming one (fading in).
 */
export default function RegisterCrossfade({ activeKey, children }: RegisterCrossfadeProps) {
  const prefersReducedMotion = usePrefersReducedMotion();

  // The layer currently being shown (its content stays fresh from props).
  const [current, setCurrent] = useState<Layer>({ key: activeKey, node: children });
  // The previous layer, kept only while it fades out after a switch.
  const [previous, setPrevious] = useState<Layer | null>(null);

  // Drives the incoming layer's first paint at opacity 0 so the transition runs.
  const [entered, setEntered] = useState(true);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Keep the current layer's node in sync with the latest children whenever the
  // key is unchanged — this updates the subtree in place WITHOUT remounting it.
  if (activeKey === current.key && current.node !== children) {
    // setState during render is safe here: it's a same-key content refresh and
    // converges in one extra pass (React's documented "adjusting state on prop
    // change" pattern). It must not run on a key change (handled in the effect).
    setCurrent({ key: activeKey, node: children });
  }

  useEffect(() => {
    if (activeKey === current.key) return;

    if (prefersReducedMotion) {
      // Instant swap: no overlap, no transition.
      setPrevious(null);
      setCurrent({ key: activeKey, node: children });
      setEntered(true);
      return;
    }

    // Start a cross-fade: the old current becomes the outgoing layer, the new
    // children become the current layer, mounted at opacity 0 then raised to 1.
    setPrevious(current);
    setCurrent({ key: activeKey, node: children });
    setEntered(false);

    const raf = requestAnimationFrame(() => setEntered(true));

    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      setPrevious(null);
    }, REGISTER_CROSSFADE_DURATION_MS);

    return () => cancelAnimationFrame(raf);
    // current.* and children are read once per key change; keying on activeKey +
    // prefersReducedMotion captures every transition trigger we care about.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey, prefersReducedMotion]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const transition = prefersReducedMotion
    ? undefined
    : {
        transitionProperty: "opacity",
        transitionDuration: `${REGISTER_CROSSFADE_DURATION_MS}ms`,
        transitionTimingFunction: REGISTER_CROSSFADE_EASING,
      };

  return (
    <div className="relative">
      {previous && (
        <div
          key={previous.key}
          data-crossfade-layer="outgoing"
          aria-hidden="true"
          // Outgoing layer is absolutely positioned so the incoming layer drives
          // the box's height; it fades to 0 and is removed after the duration.
          className="pointer-events-none absolute inset-0"
          style={{ ...transition, opacity: 0 }}
        >
          {previous.node}
        </div>
      )}
      <div
        key={current.key}
        data-crossfade-layer="current"
        style={{ ...transition, opacity: entered ? 1 : 0 }}
      >
        {current.node}
      </div>
    </div>
  );
}

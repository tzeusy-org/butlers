/**
 * useModalChoreography — the one overlay/disclosure focus contract
 * (bu-qvnce.10, JARVIS pursuit move 10).
 *
 * Extracted from the ingestion EventDrawer's hand-rolled focus choreography
 * (EventDrawer.tsx: `headingRef` focus-on-mount, `handleDrawerKeyDown`'s
 * Escape-to-close, and the local `role="status" aria-live="polite"` sr-only
 * span) plus two pieces EventDrawer didn't need because it is an inline
 * disclosure panel rather than a scrim-covering dialog: a focus-restore on
 * close, and an optional Tab trap.
 *
 * Before this hook, every overlay in the dashboard hand-rolled its own subset
 * of this behavior, inconsistently:
 *   - EntityFinder: a raw `<div>` scrim with no `role="dialog"`, no focus
 *     trap (Tab could escape to whatever rendered after it in the DOM —
 *     the floating chat widget button — once the highlighted row's active
 *     result went null), and no focus-restore to the trigger on close.
 *   - CalendarAgendaView: `role="dialog" aria-modal="true"` with NO trap at
 *     all — arguably worse than no ARIA, since assistive tech now promises a
 *     modal contract the component doesn't keep.
 *   - ButlerManagementTab's ModalBackdrop: Escape-to-close only, no
 *     focus-in, no trap, no restore.
 *   - TimelineEventDrawer: no keyboard handling whatsoever (no Escape).
 *
 * Usage:
 *   const { rootRef, initialFocusRef, onKeyDown } = useModalChoreography({
 *     onClose: () => setOpen(false),
 *   });
 *   <div ref={rootRef} role="dialog" aria-modal="true" onKeyDown={onKeyDown}>
 *     <h2 ref={initialFocusRef} tabIndex={-1}>...</h2>
 *     ...
 *   </div>
 *
 * For an inline disclosure panel that intentionally does NOT cover the page
 * (EventDrawer, TimelineEventDrawer — no scrim, Tab should stay free to
 * leave), pass `trapFocus: false` and drop `aria-modal`/`role="dialog"`.
 */

import { useEffect, useRef, type KeyboardEvent, type RefObject } from "react";

export interface UseModalChoreographyOptions {
  /** Called when Escape is pressed. */
  onClose: () => void;
  /**
   * Trap Tab/Shift+Tab within the root's focusable descendants, and swallow
   * Tab entirely when there is nothing focusable to land on (the
   * EntityFinder bug this hook was extracted to fix: an empty/actionless
   * result list let Tab leak focus to whatever rendered after the overlay in
   * the DOM). Set `false` for inline disclosure panels that render alongside
   * page content rather than over a scrim. Default `true`.
   */
  trapFocus?: boolean;
  /**
   * Whether the choreography is active. Most overlays only ever mount while
   * open (a parent renders them conditionally), so the default `true` is
   * correct and the mount/unmount lifecycle alone drives focus-in and
   * focus-restore. Pass the open/closed boolean explicitly for a component
   * that stays mounted and toggles its own visibility instead (e.g.
   * EntityFinder, which returns `null` while closed but is never unmounted
   * by its parent) — the effect re-runs its focus-in/restore dance on every
   * `active` transition, not just on the component's own mount/unmount.
   */
  active?: boolean;
  /**
   * Focus (and Tab-trap-boundary) the root element itself, via the SAME
   * `rootRef` this hook returns, instead of a separate `initialFocusRef`
   * target. Use this when there is no single natural focus target inside
   * the dialog content — e.g. a generic backdrop wrapping arbitrary
   * `children` it doesn't own the internals of. Requires the root element to
   * carry `tabIndex={-1}` itself. When set, `initialFocusRef` in the
   * returned object IS `rootRef` (attach `ref` only once). Default `false`.
   */
  focusRoot?: boolean;
}

export interface UseModalChoreographyResult<TFocus extends HTMLElement> {
  /** Attach to the dialog/panel root element. Used to compute the Tab-trap's
   *  focusable descendants and to detect focus leaving the root. */
  rootRef: RefObject<HTMLDivElement>;
  /** Attach to the element that should receive focus once active (a heading,
   *  the search input, ...). Add `tabIndex={-1}` if it is not natively
   *  focusable. */
  initialFocusRef: RefObject<TFocus>;
  /** Attach to the root's `onKeyDown`: Escape calls `onClose`; Tab is
   *  trapped within the root's focusable descendants when `trapFocus` is
   *  enabled. */
  onKeyDown: (e: KeyboardEvent) => void;
}

function isRendered(el: HTMLElement): boolean {
  // offsetParent is null for display:none elements (and unattached ones);
  // the active element itself may briefly fail this check during a reflow,
  // so it's allowed through explicitly.
  return el.offsetParent !== null || el === document.activeElement;
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function getFocusable(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(isRendered);
}

export function useModalChoreography<TFocus extends HTMLElement = HTMLElement>({
  onClose,
  trapFocus = true,
  active = true,
  focusRoot = false,
}: UseModalChoreographyOptions): UseModalChoreographyResult<TFocus> {
  const rootRef = useRef<HTMLDivElement>(null);
  // Only ever read/written by this hook — never returned directly when
  // `focusRoot` is set (rootRef is returned as `initialFocusRef` instead), so
  // no consumer ever needs to write to a ref this hook returned (that pattern
  // trips react-hooks/immutability: "modifying a value returned from a hook
  // is not allowed").
  const ownFocusRef = useRef<TFocus>(null);
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!active) return;
    // Remember the trigger so it can reclaim focus on close, then move focus
    // into the dialog. Re-running on every `active` transition (rather than
    // only on true mount/unmount) is what lets a component that toggles its
    // own visibility in place — EntityFinder — get the same choreography as
    // one a parent mounts/unmounts conditionally.
    previouslyFocusedRef.current = document.activeElement as HTMLElement | null;
    if (focusRoot) {
      rootRef.current?.focus();
    } else {
      ownFocusRef.current?.focus();
    }
    return () => {
      const prev = previouslyFocusedRef.current;
      if (prev && document.contains(prev)) prev.focus();
    };
    // Deliberately keyed on `active`/`focusRoot` only — onClose is not part
    // of the focus-in/restore choreography (only onKeyDown's Escape branch
    // reads it).
  }, [active, focusRoot]);

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    if (!trapFocus || e.key !== "Tab") return;

    const root = rootRef.current;
    if (!root) return;
    const focusable = getFocusable(root);
    if (focusable.length === 0) {
      // Nothing to land on — swallow Tab rather than let it leak to whatever
      // renders after this overlay in the DOM.
      e.preventDefault();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const activeEl = document.activeElement as HTMLElement | null;
    const activeInside = !!activeEl && root.contains(activeEl);

    if (e.shiftKey) {
      if (!activeInside || activeEl === first) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (!activeInside || activeEl === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  return {
    rootRef,
    initialFocusRef: focusRoot ? (rootRef as unknown as RefObject<TFocus>) : ownFocusRef,
    onKeyDown,
  };
}

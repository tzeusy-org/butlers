/**
 * Page-scoped keyboard shortcut registry (bu-qvnce.11 — "the first use needs
 * no docs, the tenth needs no mouse").
 *
 * Before this hook, every page hand-rolled its own `window.addEventListener
 * ("keydown", ...)` block (ApprovalsPage's j/k/a/d/x, ChatPanel's
 * Ctrl+Shift+Up/Down, MemorySearch's now-deleted colliding `/` handler) —
 * each with its own subtly different editable-field guard, none of them
 * discoverable anywhere in the product (zero hints for the approvals
 * triage keys, the fleet's best interaction).
 *
 * `useRegisterShortcut` is the one hook that both:
 *   1. installs the actual key handling for as long as the calling component
 *      stays mounted, with a single shared guard (editable fields, `<select>`,
 *      contentEditable, and — new here, subsuming bu-5o22a — any open
 *      modal/dialog overlay); and
 *   2. publishes the binding's description + display keys to the '?' help
 *      sheet's "On this page" section (see `components/ui/shortcut-hints.tsx`)
 *      via `useShortcutHintEntries`.
 *
 * It mirrors `command-registry.tsx`'s `useRegisterCommands` shape
 * deliberately (same split-context pattern, same scope-id bookkeeping) so
 * the two registries read as one family. Pairing a shortcut with a palette
 * command is a separate, explicit step: give the `PaletteCommand` a
 * `binding` (see command-registry.tsx) matching this hook's `display` — nothing
 * here auto-generates palette entries, since not every shortcut (e.g. j/k
 * roving focus) makes sense as a searchable command.
 *
 * Usage:
 *
 *   useRegisterShortcut([
 *     {
 *       key: "a",
 *       display: ["a"],
 *       description: "Approve selected",
 *       handler: () => scheduleDecision(id, "approve", () => approveMut.mutate(id)),
 *     },
 *   ]);
 *
 * Pass a freshly-built array each render (memoize with `useMemo` when the
 * bindings are cheap to keep stable across renders — not required for
 * correctness, since the hook always dispatches through the latest bindings
 * via a ref, only for avoiding unnecessary registry churn).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export interface ShortcutBinding {
  /** `KeyboardEvent.key` to match, case-sensitive (e.g. "a", "j", "ArrowUp", "?"). */
  key: string;
  /** Require Ctrl held. Default false (i.e. the shortcut requires Ctrl NOT be held). */
  ctrlKey?: boolean;
  /** Require Meta/Cmd held. Default false. */
  metaKey?: boolean;
  /** Require Shift held. Default false. */
  shiftKey?: boolean;
  /** Require Alt held. Default false. */
  altKey?: boolean;
  /** Human-readable key combo for display, e.g. `["j"]` or `["Ctrl", "Shift", "↑"]`. */
  display: string[];
  /** One-line description shown in the '?' help sheet's "On this page" section. */
  description: string;
  /** Invoked when the chord matches and the shortcut isn't suspended. */
  handler: () => void;
  /**
   * Fire even while focus is in an editable field, a `<select>`, or a
   * modal/dialog is open. Default false — almost every page-scoped
   * single-key shortcut collides with normal typing and must stay
   * suspended in those contexts.
   */
  allowWhenSuspended?: boolean;
}

interface ShortcutRegistryContextValue {
  register: (scopeId: string, bindings: ShortcutBinding[]) => void;
  unregister: (scopeId: string) => void;
}

// Same two-context split as command-registry.tsx, and for the same reason:
// callers of useRegisterShortcut only need the stable register/unregister
// pair, not the aggregated bindings array, so bundling both in one context
// would re-run every registrar's effect whenever any OTHER scope's bindings
// changed.
const ShortcutRegistryContext = createContext<ShortcutRegistryContextValue | null>(null);
const ShortcutHintEntriesContext = createContext<ShortcutBinding[]>([]);

export function ShortcutRegistryProvider({ children }: { children: ReactNode }) {
  const [scopes, setScopes] = useState<Map<string, ShortcutBinding[]>>(new Map());

  const register = useCallback((scopeId: string, bindings: ShortcutBinding[]) => {
    setScopes((prev) => {
      const next = new Map(prev);
      next.set(scopeId, bindings);
      return next;
    });
  }, []);

  const unregister = useCallback((scopeId: string) => {
    setScopes((prev) => {
      if (!prev.has(scopeId)) return prev;
      const next = new Map(prev);
      next.delete(scopeId);
      return next;
    });
  }, []);

  const bindings = useMemo(() => Array.from(scopes.values()).flat(), [scopes]);
  const registryValue = useMemo(() => ({ register, unregister }), [register, unregister]);

  return (
    <ShortcutRegistryContext.Provider value={registryValue}>
      <ShortcutHintEntriesContext.Provider value={bindings}>
        {children}
      </ShortcutHintEntriesContext.Provider>
    </ShortcutRegistryContext.Provider>
  );
}

/**
 * Suspend page-scoped shortcuts while focus sits in an editable field or any
 * modal/dialog overlay is open. Extends the app-wide editable-field guard
 * (`use-keyboard-shortcuts.ts`) to `<select>` and to open overlays — bu-5o22a's
 * gap plus the "double-fire under an open palette" failure mode this hook
 * exists to make structurally impossible.
 */
const SUSPENDED_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

export function isShortcutTargetSuspended(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (el) {
    if (SUSPENDED_TAGS.has(el.tagName)) return true;
    if (el.isContentEditable) return true;
  }
  // Any open modal/dialog overlay (EntityFinder's Command menu, the '?' help
  // sheet, or any future overlay built on useModalChoreography, bu-qvnce.10)
  // owns the keyboard while it's up — a page-scoped shortcut firing
  // underneath it is exactly the double-fire this hook exists to prevent.
  if (typeof document !== "undefined" && document.querySelector('[role="dialog"]')) return true;
  return false;
}

function matchesBinding(binding: ShortcutBinding, e: KeyboardEvent): boolean {
  return (
    e.key === binding.key &&
    !!e.ctrlKey === !!binding.ctrlKey &&
    !!e.metaKey === !!binding.metaKey &&
    !!e.shiftKey === !!binding.shiftKey &&
    !!e.altKey === !!binding.altKey
  );
}

let scopeCounter = 0;

/**
 * Install page-scoped keyboard shortcuts for the lifetime of the calling
 * component, and publish them to the '?' help sheet's "On this page" section.
 *
 * A pending g-chord (see `use-keyboard-shortcuts.ts`) owns the very next
 * keystroke app-wide, so this hook defers to it exactly like the shell's own
 * shortcuts do.
 */
export function useRegisterShortcut(bindings: ShortcutBinding[]): void {
  const ctx = useContext(ShortcutRegistryContext);
  const scopeIdRef = useRef<string | null>(null);
  if (scopeIdRef.current === null) scopeIdRef.current = `shortcut-scope-${++scopeCounter}`;

  const bindingsRef = useRef(bindings);
  bindingsRef.current = bindings;

  const register = ctx?.register;
  const unregister = ctx?.unregister;

  useEffect(() => {
    if (!register || !unregister) return;
    const scopeId = scopeIdRef.current as string;
    register(scopeId, bindings);
    return () => unregister(scopeId);
  }, [register, unregister, bindings]);

  // Installed once per mount — always dispatches through the LATEST bindings
  // via bindingsRef, so handler closures never go stale without needing to
  // tear down and re-add the DOM listener on every render.
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (window.__pendingGNav) return;
      const suspended = isShortcutTargetSuspended(e.target);
      for (const binding of bindingsRef.current) {
        if (!matchesBinding(binding, e)) continue;
        if (suspended && !binding.allowWhenSuspended) continue;
        e.preventDefault();
        binding.handler();
        return;
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);
}

/** Read the currently-registered page-scoped shortcuts (used by the '?' help sheet). */
export function useShortcutHintEntries(): ShortcutBinding[] {
  return useContext(ShortcutHintEntriesContext);
}

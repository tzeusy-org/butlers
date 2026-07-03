/**
 * Per-page command registration API (bu-86c4c.7 — "One command spine").
 *
 * The command menu's Actions group is not a hardcoded list: any mounted
 * component can contribute commands to it for as long as it stays mounted,
 * via `useRegisterCommands`. This is how page-specific verbs (e.g.
 * ApprovalsPage's "Approve next", IssuesPage's "Acknowledge issue") reach the
 * global command menu without the menu needing to know every page exists.
 * `RootLayout` also uses it to register the always-available "Trigger
 * <butler>" actions, since a command doesn't need to be page-scoped to use
 * this API.
 *
 * Usage:
 *
 *   const commands = useMemo(
 *     () => [{ id: "approve-next", label: "Approve next", perform: approveNext }],
 *     [approveNext],
 *   );
 *   useRegisterCommands(commands);
 *
 * Pass a memoized array — each call re-registers on every array identity
 * change, so an inline array literal would re-register every render.
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

export interface PaletteCommand {
  /** Stable id, unique within the caller's own command set. */
  id: string;
  /** Primary label shown (and matched against) in the command menu. */
  label: string;
  /** Extra terms matched against the query but not displayed as the label. */
  keywords?: string[];
  /** Invoked when the command is selected. The menu closes itself afterward. */
  perform: () => void;
}

interface CommandRegistryContextValue {
  register: (scopeId: string, commands: PaletteCommand[]) => void;
  unregister: (scopeId: string) => void;
}

// Split into two contexts (PR #2870 review — gemini-code-assist) so that
// callers of `useRegisterCommands` only ever subscribe to the *stable*
// register/unregister pair, not the aggregated `commands` array. Bundling
// both in one context value meant every registrar re-rendered whenever ANY
// other scope's commands changed (a new aggregated array => a new context
// value => every consumer of that context re-renders), even though
// registrars only need the stable callbacks. The aggregated array is now its
// own context, read only by the command menu via `useCommandMenuActions`.
const CommandRegistryContext = createContext<CommandRegistryContextValue | null>(null);
const CommandMenuActionsContext = createContext<PaletteCommand[]>([]);

export function CommandRegistryProvider({ children }: { children: ReactNode }) {
  const [scopes, setScopes] = useState<Map<string, PaletteCommand[]>>(new Map());

  // Stable identities: registration never needs to re-run these callbacks.
  const register = useCallback((scopeId: string, cmds: PaletteCommand[]) => {
    setScopes((prev) => {
      const next = new Map(prev);
      next.set(scopeId, cmds);
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

  const commands = useMemo(() => Array.from(scopes.values()).flat(), [scopes]);
  const registryValue = useMemo(() => ({ register, unregister }), [register, unregister]);

  return (
    <CommandRegistryContext.Provider value={registryValue}>
      <CommandMenuActionsContext.Provider value={commands}>
        {children}
      </CommandMenuActionsContext.Provider>
    </CommandRegistryContext.Provider>
  );
}

let scopeCounter = 0;

/**
 * Register a set of command-menu Actions for the lifetime of the calling
 * component. Re-registers whenever the `commands` array identity changes —
 * memoize it (useMemo/useCallback deps) when it depends on local state such
 * as the current selection.
 */
export function useRegisterCommands(commands: PaletteCommand[]): void {
  const ctx = useContext(CommandRegistryContext);
  const scopeIdRef = useRef<string | null>(null);
  if (scopeIdRef.current === null) scopeIdRef.current = `scope-${++scopeCounter}`;

  // `ctx` (register/unregister) is stable across the provider's lifetime, so
  // depending on it here never re-fires this effect when an unrelated scope's
  // commands change — only the aggregated-array context (read exclusively by
  // useCommandMenuActions) changes on that path.
  const register = ctx?.register;
  const unregister = ctx?.unregister;

  useEffect(() => {
    if (!register || !unregister) return;
    const scopeId = scopeIdRef.current as string;
    register(scopeId, commands);
    return () => unregister(scopeId);
  }, [register, unregister, commands]);
}

/** Read the currently-registered Actions (used by the command menu itself). */
export function useCommandMenuActions(): PaletteCommand[] {
  return useContext(CommandMenuActionsContext);
}

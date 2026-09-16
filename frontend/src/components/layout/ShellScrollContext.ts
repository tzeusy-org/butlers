import { createContext, type RefObject, useContext } from "react";

export interface ShellScrollContextValue {
  /** The one persistent scroll container owned by the application shell. */
  mainScrollContainerRef: RefObject<HTMLElement | null>;
}

export const ShellScrollContext = createContext<ShellScrollContextValue | null>(
  null,
);

/**
 * Read the shell's persistent main scroll container.
 *
 * Returning null outside a Shell keeps low-level consumers and isolated tests
 * harmless; the real application always renders this hook below Shell's
 * provider.
 */
export function useShellScrollContainerRef(): RefObject<HTMLElement | null> | null {
  return useContext(ShellScrollContext)?.mainScrollContainerRef ?? null;
}

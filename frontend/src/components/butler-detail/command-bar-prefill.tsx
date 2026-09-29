// ---------------------------------------------------------------------------
// Command bar prefill (bu-9ppi0z)
//
// Lets a tab body (the Skills section's "Use skill" button) hand a prompt to
// the header command bar in ButlerDetailActions without routing through the
// URL. The bar consumes a pending prefill exactly once; nothing runs until the
// operator presses Run.
// ---------------------------------------------------------------------------

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

interface CommandBarPrefillValue {
  /** Prompt text waiting for the command bar, or null when none is pending. */
  pending: string | null;
  /** Ask the command bar to show `text` as its prompt. */
  request: (text: string) => void;
  /** Clear the pending prompt once the command bar has handled it. */
  consume: () => void;
}

const noop = () => {};

// Outside a provider (isolated tests, stories) requests are harmless no-ops.
const NO_PROVIDER: CommandBarPrefillValue = { pending: null, request: noop, consume: noop };

const CommandBarPrefillContext = createContext<CommandBarPrefillValue>(NO_PROVIDER);

export function ButlerCommandBarPrefillProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<string | null>(null);
  const consume = useCallback(() => setPending(null), []);
  const value = useMemo<CommandBarPrefillValue>(
    () => ({ pending, request: setPending, consume }),
    [pending, consume],
  );
  return (
    <CommandBarPrefillContext.Provider value={value}>{children}</CommandBarPrefillContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components -- the hook is this provider's only consumer API.
export function useCommandBarPrefill(): CommandBarPrefillValue {
  return useContext(CommandBarPrefillContext);
}

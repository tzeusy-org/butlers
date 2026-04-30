// ---------------------------------------------------------------------------
// useChroniclesTimezone — hook to read owner timezone from context (bu-k18cm)
//
// Returns the owner's configured IANA timezone name injected by
// ChroniclesTimezoneProvider. Defaults to DEFAULT_TZ if called outside a
// provider.
// ---------------------------------------------------------------------------

import { useContext } from "react"
import { ChroniclesTimezoneContext } from "./timezone-context-internal"

/**
 * Returns the owner's configured timezone (IANA name).
 * Must be called inside a ChroniclesTimezoneProvider.
 * Defaults to "Asia/Singapore" if the provider is absent.
 */
export function useChroniclesTimezone(): string {
  return useContext(ChroniclesTimezoneContext)
}

// ---------------------------------------------------------------------------
// ChroniclesTimezoneProvider — injects owner timezone into context (bu-k18cm)
//
// Resolves the owner's configured timezone once per Chronicles page render.
// Default: "Asia/Singapore" (matches the briefing.py SGT constant).
//
// Source: GET /api/settings/general → data.timezone (IANA name).
//
// Usage (production):
//   const { data } = useGeneralSettings()
//   const ownerTz = data?.data?.timezone ?? DEFAULT_TZ
//   <ChroniclesTimezoneProvider timezone={ownerTz}>...</ChroniclesTimezoneProvider>
//
// Usage (tests):
//   <ChroniclesTimezoneProvider timezone="Asia/Singapore">...</ChroniclesTimezoneProvider>
//
// To read the timezone in a consumer, use useChroniclesTimezone() from
// ./use-chronicles-timezone.
// ---------------------------------------------------------------------------

import { ChroniclesTimezoneContext } from "./timezone-context-internal"

interface ChroniclesTimezoneProviderProps {
  children: React.ReactNode
  /**
   * Resolved IANA timezone name.
   * Callers (ChroniclesPage and tests) are responsible for fetching / deriving
   * this value. The provider is a thin context injector — no API calls here.
   */
  timezone: string
}

/**
 * Injects the owner's resolved timezone into context.
 *
 * This is intentionally a thin wrapper — no data fetching. The caller
 * (ChroniclesPage) fetches the value via useGeneralSettings and passes it in.
 * Tests pass an explicit string to avoid requiring a QueryClientProvider.
 */
export function ChroniclesTimezoneProvider({
  children,
  timezone,
}: ChroniclesTimezoneProviderProps) {
  return (
    <ChroniclesTimezoneContext.Provider value={timezone}>
      {children}
    </ChroniclesTimezoneContext.Provider>
  )
}

// ---------------------------------------------------------------------------
// AppTimezoneProvider / useTimezone — app-level owner timezone context (bu-ldj6y)
//
// Provides the owner's configured IANA timezone to all components in the
// React tree. Mounted once at App level so every page gets the owner timezone
// without requiring a page-specific provider.
//
// Default: "Asia/Singapore" (matches the briefing.py SGT constant).
//
// Source: GET /api/settings/general → data.timezone (IANA name).
//
// Usage (production — mounted in App.tsx):
//   const { data } = useGeneralSettings()
//   const ownerTz = data?.data?.timezone ?? DEFAULT_TZ
//   <AppTimezoneProvider timezone={ownerTz}>...</AppTimezoneProvider>
//
// Usage (tests):
//   <AppTimezoneProvider timezone="Asia/Singapore">...</AppTimezoneProvider>
//
// To read the timezone in a consumer, use useTimezone() from this module.
// ---------------------------------------------------------------------------

import { createContext, useContext } from "react"
import { DEFAULT_TZ } from "@/components/chronicles/tz-format"

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

export const AppTimezoneContext = createContext<string>(DEFAULT_TZ)

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

interface AppTimezoneProviderProps {
  children: React.ReactNode
  /**
   * Resolved IANA timezone name.
   * Callers (App and tests) are responsible for fetching / deriving this value.
   * The provider is a thin context injector — no API calls here.
   */
  timezone: string
}

/**
 * Injects the owner's resolved timezone into context for all descendant pages.
 *
 * This is intentionally a thin wrapper — no data fetching. The caller (App.tsx)
 * fetches the value via useGeneralSettings and passes it in. Tests pass an
 * explicit string to avoid requiring a QueryClientProvider.
 */
export function AppTimezoneProvider({ children, timezone }: AppTimezoneProviderProps) {
  return (
    <AppTimezoneContext.Provider value={timezone}>
      {children}
    </AppTimezoneContext.Provider>
  )
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Returns the owner's configured timezone (IANA name).
 * Works anywhere in the app — no chronicles-specific provider required.
 * Defaults to "Asia/Singapore" if the AppTimezoneProvider is absent.
 */
export function useTimezone(): string {
  return useContext(AppTimezoneContext)
}

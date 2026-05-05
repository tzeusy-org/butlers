// ---------------------------------------------------------------------------
// ChroniclesTimezoneProvider — backward-compat re-export (bu-ldj6y)
//
// The canonical provider is now AppTimezoneProvider from
// @/components/ui/timezone-context and is mounted once at App level.
//
// ChroniclesTimezoneProvider is kept here so existing tests that wrap
// chronicles components with a provider do not need to be migrated.
// It is an alias for AppTimezoneProvider and shares the same context.
//
// To read the timezone, use useTimezone() (preferred) or the legacy
// useChroniclesTimezone() alias from ./use-chronicles-timezone.
// ---------------------------------------------------------------------------

export { AppTimezoneProvider as ChroniclesTimezoneProvider } from "@/components/ui/timezone-context"

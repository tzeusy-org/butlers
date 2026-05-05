// ---------------------------------------------------------------------------
// ChroniclesTimezoneContext — re-export alias for AppTimezoneContext (bu-ldj6y)
//
// Internal module. Import this only from timezone-context.tsx (provider) and
// use-chronicles-timezone.ts (hook). Do not import directly in consumers.
//
// The canonical context now lives in @/components/ui/timezone-context.
// This module exists for backward-compatibility with chronicles-internal imports.
// ---------------------------------------------------------------------------

export { AppTimezoneContext as ChroniclesTimezoneContext } from "@/components/ui/timezone-context"

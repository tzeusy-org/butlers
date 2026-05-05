// ---------------------------------------------------------------------------
// useChroniclesTimezone — backward-compat alias (bu-ldj6y)
//
// The canonical hook is now useTimezone() from @/components/ui/timezone-context.
// useChroniclesTimezone() is kept here so existing chronicles consumers do not
// need to be migrated in this PR.
//
// Both hooks read from the same AppTimezoneContext, so they return identical
// values regardless of which one is called.
// ---------------------------------------------------------------------------

export { useTimezone as useChroniclesTimezone } from "@/components/ui/timezone-context"

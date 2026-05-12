// ---------------------------------------------------------------------------
// atoms-utils.ts — non-component utilities for butler detail atoms
// (bu-zsnuh)
//
// Exports:
//   Tone        — semantic tone token type
//   toneClass   — maps a Tone token to a Tailwind text utility class
//
// Split from atoms.tsx to satisfy react-refresh/only-export-components:
// fast-refresh requires component-only files; utilities live here.
// ---------------------------------------------------------------------------

export type Tone = "amber" | "red" | "green" | "dim" | "fg"

export function toneClass(tone: Tone): string {
  switch (tone) {
    case "amber": return "text-amber-500"
    case "red":   return "text-destructive"
    case "green": return "text-emerald-500"
    case "dim":   return "text-muted-foreground"
    case "fg":    return "text-foreground"
  }
}

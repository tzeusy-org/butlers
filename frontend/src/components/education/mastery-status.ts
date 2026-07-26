/**
 * Canonical mastery_status -> color token map (bu-ep4ks.15).
 *
 * Previously duplicated: MindMapGraph.tsx (correct, reviewed under bu-86c4c.6
 * -- see its own header comment on the mastery-progression scale) held the
 * canonical mapping as a private const while NodeDetailPanel.tsx hand-rolled
 * a second copy using raw, unguarded Tailwind classes (`bg-blue-100
 * text-blue-800` for "reviewing", `bg-slate-100`/`bg-gray-100` for
 * "diagnosed"/"unseen") that had drifted from the reviewed tokens entirely --
 * blue is not one of this dashboard's three sanctioned status colors, and
 * slate/gray were never tied to a themed custom property. Centralized here
 * so both consumers share one token and cannot drift again.
 *
 * mastered/learning map onto the tri-state (green/amber) system;
 * reviewing/diagnosed/unseen are stepped neutrals with no live red/amber/
 * green state to report, same shape as the --permanence-* progression scale.
 */
export const MASTERY_STATUS_COLORS: Record<string, string> = {
  mastered: "var(--green)",
  reviewing: "var(--mfg)",
  learning: "var(--amber)",
  diagnosed: "var(--dim)",
  unseen: "var(--border-strong)",
};

/**
 * TEXT-safe variant of MASTERY_STATUS_COLORS. Base --amber fails WCAG AA as
 * text (bu-86c4c.16) -- --amber-text is the AA-fixed alias for text usage.
 * --border-strong (18%-20% opacity) is not text-safe, so unseen and diagnosed
 * use --dim (tertiary text token) instead.
 */
export const MASTERY_STATUS_TEXT_COLORS: Record<string, string> = {
  ...MASTERY_STATUS_COLORS,
  learning: "var(--amber-text)",
  unseen: "var(--dim)", // border-opacity tokens are not text-safe
  diagnosed: "var(--dim)", // border-opacity tokens are not text-safe
};

/** Tailwind arbitrary-value badge classes derived from the maps above. */
export function masteryStatusBadgeClassName(status: string): string {
  const bg = MASTERY_STATUS_COLORS[status] ?? MASTERY_STATUS_COLORS.unseen;
  const text = MASTERY_STATUS_TEXT_COLORS[status] ?? MASTERY_STATUS_TEXT_COLORS.unseen;
  return `bg-[${bg}]/10 text-[${text}]`;
}

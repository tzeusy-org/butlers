import { SourceDegradedNote } from "@/components/ui/query-boundary"

// ---------------------------------------------------------------------------
// SpendUnavailableFootnote — shared degraded-source footnote for the spend
// fan-out surfaces (breakdown / top-sessions / by-schedule).
//
// Every spend surface that merges per-butler cost sources footnotes the butlers
// dropped from the fan-out (``meta.unavailable_butlers``) rather than letting an
// undercount read as a genuine $0 result. Extracted from the six near-identical
// inline SourceDegradedNote call sites (bu-zseqx); keeps the SourceDegradedNote
// vocabulary verbatim (name the source inline, colon-separated source and
// reason, never suppress).
// ---------------------------------------------------------------------------

export interface SpendUnavailableFootnoteProps {
  /** Surface name shown before the colon, e.g. "Spend breakdown" / "Top sessions". */
  label: string
  /** Butlers dropped from the fan-out (``meta.unavailable_butlers``). */
  butlers: string[]
  /**
   * ``"empty"`` — the whole surface is empty *because* the butlers dropped out
   * (renders "no data, ..."); ``"partial"`` — the surface is populated but
   * undercounts, some butlers are absent (renders "excluded, ...").
   */
  variant: "empty" | "partial"
  /** Test id forwarded to the underlying alert. */
  testId: string
}

export function SpendUnavailableFootnote({
  label,
  butlers,
  variant,
  testId,
}: SpendUnavailableFootnoteProps) {
  const prefix = variant === "empty" ? "no data" : "excluded"
  return (
    <SourceDegradedNote
      label={label}
      detail={`${prefix}, cost source unavailable: ${butlers.join(", ")}`}
      testId={testId}
    />
  )
}

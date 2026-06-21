import type { CSSProperties } from "react";

/**
 * Shared mono-eyebrow style (10px, --font-mono, --muted-foreground, 0.14em
 * tracking; pair with the `tnum uppercase` className). marginBottom is
 * intentionally omitted so each consumer sets its own bottom spacing
 * (KpiStrip cell: 6px; the SessionsKpiStrip "Matching filters" caption: 12px).
 *
 * Lives in its own module (not KpiStrip.tsx) so the component file only exports
 * components (react-refresh/only-export-components).
 */
export const KPI_EYEBROW_STYLE: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "10px",
  letterSpacing: "0.14em",
  lineHeight: 1,
  color: "var(--muted-foreground)",
  margin: 0,
};

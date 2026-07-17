import { Section } from "@/components/overview/Section";
import { RowLink } from "@/components/ui/RowLink";

const HEALTH_LEDGER_ENTRIES = [
  {
    label: "Measurements",
    path: "/health/measurements",
    description: "Readings, trends, and source history.",
  },
  {
    label: "Medications",
    path: "/health/medications",
    description: "Doses, adherence, and next steps.",
  },
  {
    label: "Conditions",
    path: "/health/conditions",
    description: "Active conditions and care context.",
  },
  {
    label: "Symptoms",
    path: "/health/symptoms",
    description: "Patterns, severity, and history.",
  },
  {
    label: "Meals",
    path: "/health/meals",
    description: "Meals and nutrition totals.",
  },
  {
    label: "Research",
    path: "/health/research",
    description: "Saved notes and evidence.",
  },
] as const;

/**
 * Stable index of the six Health record surfaces.
 *
 * This deliberately describes destinations rather than loading status or
 * record counts: `/health` remains useful even when individual data sources
 * are unavailable, without implying that a destination is currently healthy.
 */
export function HealthLedgerIndex() {
  return (
    <Section eyebrow="Health ledger">
      <ul
        data-testid="health-ledger-index"
        aria-label="Health ledger"
        style={{ listStyle: "none", margin: 0, padding: 0 }}
      >
        {HEALTH_LEDGER_ENTRIES.map((entry, index) => (
          <li key={entry.path}>
            <RowLink
              to={entry.path}
              aria-label={`View ${entry.label}`}
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(0, 1fr) auto",
                alignItems: "center",
                gap: "8px",
                paddingTop: "10px",
                paddingBottom: "10px",
                borderTop: index === 0 ? "1px solid var(--border)" : undefined,
                borderBottom: "1px solid var(--border)",
                color: "inherit",
                textDecoration: "none",
              }}
            >
              <div style={{ minWidth: 0 }}>
                <p
                  style={{
                    fontFamily: "var(--font-sans)",
                    fontSize: "13px",
                    fontWeight: 400,
                    color: "var(--foreground)",
                    lineHeight: 1.4,
                    margin: 0,
                  }}
                >
                  {entry.label}
                </p>
                <p
                  style={{
                    fontFamily: "var(--font-serif)",
                    fontSize: "12px",
                    color: "var(--muted-foreground)",
                    lineHeight: 1.4,
                    margin: "2px 0 0",
                  }}
                >
                  {entry.description}
                </p>
              </div>
              <span
                aria-hidden="true"
                style={{
                  color: "var(--muted-foreground)",
                  fontSize: "16px",
                  lineHeight: 1,
                }}
              >
                →
              </span>
            </RowLink>
          </li>
        ))}
      </ul>
    </Section>
  );
}

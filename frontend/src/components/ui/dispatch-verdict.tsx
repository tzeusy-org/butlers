// ---------------------------------------------------------------------------
// DispatchVerdict -- shared page-opener primitive (bu-qvnce.9, JARVIS pursuit
// 2026-07-04 move 9)
//
// Extracts SystemVerdictBanner's composition pattern (components/system/
// SystemVerdictBanner.tsx) into one primitive so every surface can open with
// the system's own synthesized verdict instead of a static tagline: a
// deterministic template of clauses, each a real door (<Link>) when it has a
// natural drill-down target, that renders as either one calm all-clear line
// or a clause list -- never both.
//
// Doctrine (fleet-wide degraded-honesty convention -- butlers/CLAUDE.md "API
// Conventions"; the same contract move 1/bu-qvnce.1 applied to the backend
// envelopes):
//   - A source that raises or is unreachable must never render as a
//     truthful empty/zero/all-clear result. Every `sources` entry with
//     isError=true contributes its own "<label> unavailable" clause, and
//     that clause alone is enough to suppress the calm `allClear` line even
//     when every OTHER computed clause would otherwise have been empty.
//   - Any `isLoading` source renders the skeleton instead of guessing --
//     a premature "all clear" is worse than a brief loading state.
//
// Variants: skeleton (any source loading) / all-clear (no clauses at all,
// including no source errors) / clauses (one or more clauses, source-error
// clauses always first).
// ---------------------------------------------------------------------------

import { Link } from "react-router";

export interface VerdictSource {
  /** Named inline when this source errors, e.g. "fleet status", "QA summary". */
  label: string;
  isLoading: boolean;
  isError: boolean;
}

export interface VerdictClause {
  key: string;
  text: string;
  /** When set, the clause renders as a real <Link> (a door) instead of plain text. */
  href?: string;
}

export interface DispatchVerdictProps {
  /** Prefix for data-testid (`${testId}-verdict-skeleton|-all-clear|-clauses`). */
  testId: string;
  /** Landmark aria-label for the wrapping region, e.g. "QA verdict". */
  landmarkLabel: string;
  /**
   * Sources feeding the verdict. Any isLoading renders the skeleton; any
   * isError contributes its own "<label> unavailable" clause (prepended,
   * in source order) and suppresses the plain `allClear` line.
   */
  sources: VerdictSource[];
  /**
   * Caller-computed clauses, already in display order. These render after
   * any source-error clauses. Pass an empty array when there is nothing to
   * report beyond source health.
   */
  clauses: VerdictClause[];
  /** Rendered as the calm line when there are no clauses at all (incl. no source errors). */
  allClear: string;
  /** aria-label for the clause list when non-empty. Defaults to `${landmarkLabel} needs attention`. */
  clausesLabel?: string;
  className?: string;
}

function slug(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
}

export function DispatchVerdict({
  testId,
  landmarkLabel,
  sources,
  clauses,
  allClear,
  clausesLabel,
  className,
}: DispatchVerdictProps) {
  const isLoading = sources.some((s) => s.isLoading);

  if (isLoading) {
    return (
      <div role="region" aria-label={landmarkLabel} className={className}>
        <div
          role="status"
          className="h-8 w-full rounded bg-muted"
          data-testid={`${testId}-verdict-skeleton`}
          aria-label={`Loading ${landmarkLabel}`}
        />
      </div>
    );
  }

  // A settled (non-loading) error must never be silently dropped -- it
  // always contributes its own clause, and its presence in `allClauses`
  // below is what keeps the calm `allClear` branch from ever rendering
  // alongside a source that is actually down.
  const errorClauses: VerdictClause[] = sources
    .filter((s) => s.isError)
    .map((s) => ({ key: `${slug(s.label)}-error`, text: `${s.label} unavailable` }));

  const allClauses = [...errorClauses, ...clauses];

  if (allClauses.length === 0) {
    return (
      <div role="region" aria-label={landmarkLabel} className={className}>
        <div
          role="status"
          data-testid={`${testId}-verdict-all-clear`}
          className="font-mono text-sm text-muted-foreground"
        >
          {allClear}
        </div>
      </div>
    );
  }

  return (
    <div role="region" aria-label={landmarkLabel} className={className}>
      <div
        role="group"
        aria-label={clausesLabel ?? `${landmarkLabel} needs attention`}
        data-testid={`${testId}-verdict-clauses`}
      >
        <ul className="flex flex-wrap items-baseline gap-x-1.5 gap-y-1 text-sm">
          {allClauses.map((c, i) => (
            <li key={c.key} className="flex items-baseline gap-1.5">
              {i > 0 && (
                <span aria-hidden="true" className="text-muted-foreground">
                  ·
                </span>
              )}
              {c.href ? (
                <Link to={c.href} className="text-inherit hover:underline">
                  {c.text}
                </Link>
              ) : (
                <span>{c.text}</span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

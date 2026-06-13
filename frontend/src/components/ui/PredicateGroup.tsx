// ---------------------------------------------------------------------------
// PredicateGroup — reusable predicate-grouped neighbour list primitive (bu-pcv1w)
//
// Renders a labelled section containing a list of NeighbourRow entries for a
// single predicate (e.g. "knows", "family-of").
//
// Call-site adapter pattern:
//   HopPage:     <PredicateGroup onSelect={onRecentre} ... />
//   ColumnsPage: <PredicateGroup columnIndex={i} onSelect={(id) => onSelect(id, i)} ... />
//
// When `columnIndex` is provided the data-testid uses the Columns-page format:
//   column-predicate-group-{columnIndex}-{predicate}
// Otherwise the Hop-page format is used:
//   predicate-group-{predicate}
// ---------------------------------------------------------------------------

import type { NeighbourEntry } from "@/api/types";
import { NeighbourRow } from "@/components/ui/NeighbourRow";

export interface PredicateGroupProps {
  /** The predicate label (e.g. "knows", "family-of"). */
  predicate: string;
  /** The neighbour entries for this predicate. */
  entries: NeighbourEntry[];
  /**
   * Called when a neighbour entry is clicked. Receives the entity ID.
   *
   * Call sites are responsible for closure-adapting any extra arguments
   * (e.g. columnIndex) before passing this prop.
   */
  onSelect: (entityId: string) => void;
  /**
   * Column index when rendered inside a Columns cascade.
   *
   * When provided:
   *  - data-testid becomes `column-predicate-group-{columnIndex}-{predicate}`
   *  - NeighbourRow receives `data-column-index` for compound selector queries
   *  - NeighbourRow testId becomes `column-neighbour-row-{columnIndex}`
   *
   * When absent (Hop view):
   *  - data-testid is `predicate-group-{predicate}`
   *  - NeighbourRow testId is `neighbour-row`
   */
  columnIndex?: number;
  /**
   * Optional per-entry accessible label factory for each NeighbourRow button.
   *
   * Call sites that need a context-specific aria-label (e.g. HopPage uses
   * "Re-centre on entity <name>" rather than the default "Select entity <name>")
   * should provide this callback. When absent, NeighbourRow falls back to its
   * own default: `Select entity <displayName>`.
   */
  getRowAriaLabel?: (entry: NeighbourEntry) => string;
  /**
   * Count of neighbours NOT returned in ``entries`` because of ranked
   * truncation (from the neighbours response ``remainders`` map). When > 0 a
   * non-interactive "+N more" affordance is rendered after the rows. Zero or
   * undefined renders nothing.
   */
  remainder?: number;
  /**
   * The entity_id of the keyboard-cursored neighbour (Hop view). The matching
   * row renders the design-language focus treatment. Undefined when no cursor
   * is active (e.g. the Columns cascade tracks its cursor separately).
   */
  cursoredEntityId?: string | null;
}

/**
 * A labelled section of neighbour rows grouped under one predicate.
 *
 * @example HopPage usage (re-centre label)
 *   <PredicateGroup
 *     predicate="knows"
 *     entries={entries}
 *     onSelect={onRecentre}
 *     getRowAriaLabel={(entry) => `Re-centre on entity ${entry.canonical_name || entry.entity_id}`}
 *   />
 *
 * @example ColumnsPage usage (closure adapter)
 *   <PredicateGroup
 *     predicate="knows"
 *     entries={entries}
 *     columnIndex={columnIndex}
 *     onSelect={(id) => onSelect(id, columnIndex)}
 *   />
 */
export function PredicateGroup({
  predicate,
  entries,
  onSelect,
  columnIndex,
  getRowAriaLabel,
  remainder,
  cursoredEntityId,
}: PredicateGroupProps) {
  const label = predicate.replace(/-/g, " ");
  const isColumns = columnIndex !== undefined;

  const sectionTestId = isColumns
    ? `column-predicate-group-${columnIndex}-${predicate}`
    : `predicate-group-${predicate}`;

  const rowTestId = isColumns ? `column-neighbour-row-${columnIndex}` : "neighbour-row";
  const moreTestId = isColumns
    ? `column-predicate-more-${columnIndex}-${predicate}`
    : `predicate-more-${predicate}`;

  const hasRemainder = remainder !== undefined && remainder > 0;
  // The full group size is what we returned plus the truncated overflow.
  const totalCount = entries.length + (hasRemainder ? remainder : 0);

  return (
    <section data-testid={sectionTestId}>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1 px-2">
        {label}
        <span className="ml-2 font-normal tabular-nums">({totalCount})</span>
      </h3>
      <ul>
        {entries.map((entry) => (
          <NeighbourRow
            key={entry.entity_id}
            entry={entry}
            onClick={onSelect}
            ariaLabel={getRowAriaLabel?.(entry)}
            testId={rowTestId}
            cursored={cursoredEntityId != null && entry.entity_id === cursoredEntityId}
            {...(isColumns ? { "data-column-index": columnIndex } : {})}
          />
        ))}
      </ul>
      {hasRemainder && (
        <p
          className="text-xs text-muted-foreground px-2 pt-0.5 tabular-nums"
          data-testid={moreTestId}
        >
          +{remainder} more
        </p>
      )}
    </section>
  );
}

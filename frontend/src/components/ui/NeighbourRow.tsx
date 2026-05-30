// ---------------------------------------------------------------------------
// NeighbourRow — reusable neighbour list-item primitive (bu-ah35h)
//
// A single clickable neighbour entry rendered as an <li> with:
//   - left side: NetworkIcon + entity name button
//   - right side: optional weight, last_seen time, and direction badge
//
// Used by HopPage (NeighbourRow) and ColumnsPage (NeighbourItem).
//
// Design: about/lay-and-land/frontend.md §Relationship sub-pages
// ---------------------------------------------------------------------------

import { NetworkIcon } from "lucide-react";

import type { NeighbourEntry } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Time } from "@/components/ui/time";

export interface NeighbourRowProps {
  /** The neighbour entry to display. */
  entry: NeighbourEntry;
  /** Called when the name button is clicked. */
  onClick: (entityId: string) => void;
  /** Accessible label for the button (defaults to "Select entity <name>"). */
  ariaLabel?: string;
  /** data-testid applied to the <li> element. */
  testId?: string;
  /** Additional data attributes forwarded to the <li> element. */
  "data-column-index"?: number;
}

/**
 * A single clickable neighbour entry inside a predicate group.
 *
 * Renders as an `<li>` with a name button on the left and metadata on the
 * right (edge weight, last-seen time, direction badge).
 *
 * @example
 *   <NeighbourRow
 *     entry={entry}
 *     onClick={(id) => handleRecentre(id)}
 *     ariaLabel={`Re-centre on entity ${entry.canonical_name}`}
 *     testId="neighbour-row"
 *   />
 */
export function NeighbourRow({
  entry,
  onClick,
  ariaLabel,
  testId = "neighbour-row",
  "data-column-index": columnIndex,
}: NeighbourRowProps) {
  const entityId = entry.entity_id;
  const displayName = entry.canonical_name || entityId;
  const label = ariaLabel ?? `Select entity ${displayName}`;

  return (
    <li
      className="flex items-center justify-between py-2 border-b last:border-0 hover:bg-muted/40 px-2 rounded-sm"
      data-testid={testId}
      data-column-index={columnIndex}
    >
      <button
        type="button"
        className="flex items-center gap-2 text-left text-sm font-medium text-primary hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
        onClick={() => onClick(entityId)}
        aria-label={label}
        data-entity-id={entityId}
        data-column-index={columnIndex}
      >
        <NetworkIcon className="h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden="true" />
        <span>{displayName}</span>
      </button>

      <div className="flex items-center gap-3 text-xs text-muted-foreground shrink-0 ml-4">
        {entry.weight != null && (
          <span className="tabular-nums" title="Edge weight">
            w={entry.weight}
          </span>
        )}
        {entry.last_seen != null && <Time value={entry.last_seen} mode="relative" />}
        <Badge variant="outline" className="text-xs">
          {entry.direction === "forward" ? "→" : "←"}
        </Badge>
      </div>
    </li>
  );
}

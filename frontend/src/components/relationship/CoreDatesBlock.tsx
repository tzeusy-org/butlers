/**
 * CoreDatesBlock (entity v3 — "Core dates block", server half wiring, bu-xzh76)
 *
 * Renders the entity's date-kind facts (has-birthday, anniversaries) sourced
 * from GET /api/butlers/relationship/entities/{id}/core-dates — the server
 * extraction that replaces the former client-side date string-matching on the
 * generic facts list (spec: "server-extracted, not client-side string-matching").
 *
 * Each row shows the predicate label, the formatted date, the next occurrence
 * with its ``days_until`` (tabular nums), and provenance per the rendering
 * requirement (``src``, ``verified``, ``staleness_band``). Items arrive ordered
 * by ``days_until`` ascending (soonest first) — no client-side sort.
 *
 * The block is a first-class section; it hides itself when there are no
 * date-kind facts.
 */

import { format } from "date-fns";

import type { CoreDateEntry } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { useEntityCoreDates } from "@/hooks/use-entities";

/** Map a date-kind predicate to a human label. */
function _coreDateLabel(predicate: string): string {
  const map: Record<string, string> = {
    "has-birthday": "Birthday",
    "has-anniversary": "Anniversary",
  };
  return map[predicate] ?? predicate.replaceAll("-", " ").replaceAll("_", " ");
}

/** Format the days-until count into canned copy. */
function _daysUntilCopy(daysUntil: number): string {
  if (daysUntil === 0) return "today";
  if (daysUntil === 1) return "tomorrow";
  return `in ${daysUntil} days`;
}

function CoreDateRow({ entry }: { entry: CoreDateEntry }) {
  const occurrence = new Date(entry.next_occurrence);
  const label = _coreDateLabel(entry.predicate);
  // Stable month/day formatting independent of the stored year.
  const monthDay = format(new Date(2000, entry.month - 1, entry.day), "MMM d");

  return (
    <div
      data-testid={`core-date-row-${entry.predicate}`}
      className="grid grid-cols-[8rem_1fr_auto] items-baseline gap-3 py-2.5"
    >
      <dt className="text-muted-foreground text-xs uppercase tracking-wide">
        {label}
      </dt>
      <dd className="text-sm">
        <span>{monthDay}</span>
        {entry.year != null && (
          <span className="text-muted-foreground"> · {entry.year}</span>
        )}
        <span className="text-muted-foreground tabular-nums">
          {" "}
          · next {format(occurrence, "MMM d, yyyy")} ({_daysUntilCopy(entry.days_until)})
        </span>
      </dd>
      <dd className="flex items-center gap-1.5 justify-self-end">
        {entry.verified && (
          <Badge variant="secondary" className="text-[10px]">
            verified
          </Badge>
        )}
        <span className="text-muted-foreground text-[10px] capitalize">
          {entry.staleness_band}
        </span>
        <span className="text-muted-foreground text-[10px]">{entry.src}</span>
      </dd>
    </div>
  );
}

export function CoreDatesBlock({ entityId }: { entityId: string }) {
  const { data, isLoading } = useEntityCoreDates(entityId);

  if (isLoading) return null;
  const items = data?.items ?? [];
  if (items.length === 0) return null;

  return (
    <section data-testid="core-dates-block" className="space-y-3">
      <h2 className="text-lg font-semibold">Core dates</h2>
      <dl className="divide-y divide-border border-y">
        {items.map((entry) => (
          <CoreDateRow key={entry.id} entry={entry} />
        ))}
      </dl>
    </section>
  );
}

import { ApiError } from "@/api/client.ts";
import type { PresenceTransition } from "@/api/home-presence.ts";
import { usePresenceTransitions } from "@/hooks/use-home-presence.ts";
import { useOwnerPlaces } from "@/hooks/use-home-places.ts";

import { Panel } from "@/components/ui/atoms";
import { Time } from "@/components/ui/time.tsx";

function describe(transition: PresenceTransition, labels: Map<string, string>): string {
  const place = (transition.place_ref && labels.get(transition.place_ref)) || "a saved place";
  return transition.direction === "leave" ? `Left ${place}` : `Arrived at ${place}`;
}

function Row({ transition, labels }: { transition: PresenceTransition; labels: Map<string, string> }) {
  return (
    <li
      className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-1.5"
      data-testid="presence-transition"
      data-direction={transition.direction}
    >
      <Time
        value={transition.at}
        mode="absolute"
        compact
        className="w-32 shrink-0 font-mono text-xs text-muted-foreground"
      />
      <span
        className={
          transition.retracted
            ? "text-sm text-muted-foreground line-through"
            : transition.late
              ? "text-sm text-muted-foreground"
              : "text-sm"
        }
      >
        {describe(transition, labels)}
      </span>
      {transition.retracted ? (
        <span className="text-xs text-muted-foreground">undone: you came straight back</span>
      ) : null}
      {transition.late ? (
        <span className="text-xs text-muted-foreground">confirmed late, so not acted on</span>
      ) : null}
    </li>
  );
}

/**
 * Owner-only history of confirmed leave and arrive events. Undone events are struck through and
 * late ones muted; both stay listed
 * (history is never hidden); nothing here acts on them. While the presence
 * reader is not yet switched on the panel says so quietly instead of showing an error.
 */
export function PresenceTimeline() {
  const timeline = usePresenceTransitions();
  const places = useOwnerPlaces();
  const labels = new Map(
    places.data === undefined ? [] : places.data.map((place) => [place.id, place.label] as const),
  );
  const inactive =
    timeline.error instanceof ApiError && timeline.error.code === "PRESENCE_TIMELINE_UNAVAILABLE";

  return (
    <Panel title="Arrivals and departures" span={4} testId="presence-timeline-panel">
      <div className="space-y-3">
        {timeline.isLoading ? (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            Loading arrivals and departures
          </p>
        ) : null}
        {inactive ? (
          <p className="text-sm text-muted-foreground" data-testid="presence-timeline-inactive">
            Arrival and departure history isn&apos;t switched on yet.
          </p>
        ) : null}
        {timeline.isError && !inactive ? (
          <p className="text-sm text-destructive" data-testid="presence-timeline-error">
            Couldn&apos;t load arrivals and departures.
          </p>
        ) : null}
        {timeline.data && timeline.data.transitions.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="presence-timeline-empty">
            No confirmed arrivals or departures in the last week.
          </p>
        ) : null}
        {timeline.data && timeline.data.transitions.length > 0 ? (
          <ul className="divide-y divide-border" data-testid="presence-timeline-list">
            {timeline.data.transitions.map((transition) => (
              <Row key={transition.id} transition={transition} labels={labels} />
            ))}
          </ul>
        ) : null}
      </div>
    </Panel>
  );
}

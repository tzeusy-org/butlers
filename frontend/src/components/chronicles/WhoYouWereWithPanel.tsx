// ---------------------------------------------------------------------------
// WhoYouWereWithPanel — resolved companions for the day (IEA §10)
//
// Lists the people the owner spent time with, with co-present duration and
// channel (in-person vs a comms channel). Identity is resolved once at write
// time; this surface only displays.
//
// Degraded-mode contract (butlers/CLAUDE.md API Conventions):
//   - who_you_were_with_source_error → SourceDegradedNote; never a truthful-
//     empty companion list.
//   - companion_names_unavailable → the duration/channel data is still trusted
//     (chronicler's own), only the relationship-butler display names are
//     degraded — render an inline SourceDegradedNote for names AND still show
//     the entries. Distinct from a per-entry `unattributed` (identity genuinely
//     unknown, not a lookup failure).
//
// Presentational: takes the query result pieces as props (renderToStaticMarkup
// testable).
// ---------------------------------------------------------------------------

import { User, Users } from "lucide-react";

import type { ChroniclerCompanionEntry, ChroniclerWhoYouWereWithResponse } from "@/api/types";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { formatSeconds } from "./iea-format";

export interface WhoYouWereWithPanelProps {
  data: ChroniclerWhoYouWereWithResponse | undefined;
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
}

function companionLabel(c: ChroniclerCompanionEntry, namesUnavailable: boolean): string {
  if (c.unattributed) return "Someone (unattributed)";
  if (c.display_name) return c.display_name;
  // entity_id known but name absent: distinguish a lookup failure (degraded)
  // from a genuinely blank name.
  return namesUnavailable ? "Name unavailable" : "Unnamed contact";
}

export function WhoYouWereWithPanel({
  data,
  isLoading,
  isError,
  onRetry,
}: WhoYouWereWithPanelProps) {
  if (isLoading) {
    return (
      <div className="space-y-2" role="status" aria-label="Loading companions" data-testid="who-skeleton">
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-9 w-full rounded-md" />
        ))}
      </div>
    );
  }

  if (isError || data?.who_you_were_with_source_error) {
    return (
      <SourceDegradedNote
        label="Who you were with"
        detail="data source unreachable"
        onRetry={onRetry}
      />
    );
  }

  if (!data) return null;

  const { companions, companion_names_unavailable } = data;

  return (
    <div className="space-y-2" data-testid="who-you-were-with">
      {companion_names_unavailable && (
        <SourceDegradedNote
          label="Companion names"
          detail="name lookup unavailable, though durations and channels are still accurate"
        />
      )}
      {companions.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="who-empty">
          No one else recorded for this day.
        </p>
      ) : (
        <ul className="space-y-1.5" data-testid="who-list">
          {companions.map((c, i) => (
            <li
              key={c.entity_id ?? `unattributed-${c.channel}-${i}`}
              className="flex items-center gap-2 rounded-md border p-2 text-sm"
              style={{ borderColor: "var(--border)" }}
              data-testid="who-item"
            >
              {c.unattributed ? (
                <User className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              ) : (
                <Users className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              )}
              <span className="min-w-0 flex-1 truncate">
                {companionLabel(c, companion_names_unavailable)}
              </span>
              <Badge variant="outline" className="text-[10px]">
                {c.channel}
              </Badge>
              <span className="tnum text-xs text-muted-foreground">
                {formatSeconds(c.co_present_seconds)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

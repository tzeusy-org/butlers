/**
 * The owner's agenda line from `/now` (bu-5ii4gj.2 P3): next, or free until. Rendered in the Shell
 * header and at the top of the Overview. It never invents a line: while the surface is off (404)
 * or the card carries no agenda it renders nothing, and an unreadable calendar says so.
 * A polite live region announces the line only when its text changes.
 */

import { useState } from "react";
import { Link } from "react-router";

import { isNowSurfaceOff, useNow } from "@/hooks/use-now.ts";
import { cn } from "@/lib/utils.ts";

export const AGENDA_KIND = "owner.agenda";

export function NowStrip({ className }: { className?: string }) {
  const { data, isError, error } = useNow();
  const line = data?.data.lines.find((l) => l.kind === AGENDA_KIND) ?? null;

  // Announce only a change of the line; the first read is the page's own content. Derived during
  // render (not in an effect) so the region updates in the same commit as the text.
  const text = line?.text ?? null;
  const [seen, setSeen] = useState<string | null>(null);
  const [announced, setAnnounced] = useState("");
  if (text !== seen) {
    setSeen(text);
    if (seen !== null && text !== null) setAnnounced(text);
  }

  if (isError && !isNowSurfaceOff(error)) {
    return (
      <span data-testid="now-strip-unavailable" className={cn("text-xs text-[var(--dim)]", className)}>
        Now unavailable
      </span>
    );
  }
  if (!line) return null;

  const body = (
    <span className={line.state === "known" ? undefined : "text-[var(--amber-text)]"}>
      <span className="text-[var(--dim)]">{line.label}: </span>
      {line.text}
    </span>
  );
  return (
    <div data-testid="now-strip" className={cn("min-w-0 truncate text-xs", className)}>
      {line.door ? (
        <Link to={line.door} className="underline decoration-border underline-offset-4">
          {body}
        </Link>
      ) : (
        body
      )}
      <span role="status" aria-live="polite" className="sr-only" data-testid="now-strip-live">
        {announced}
      </span>
    </div>
  );
}

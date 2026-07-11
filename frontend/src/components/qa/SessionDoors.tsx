// ---------------------------------------------------------------------------
// SessionDoors — trace-spine doors on the QA case dossier (bu-533qx.3)
//
// The dossier narrates what the QA staffer caught and fixed, but the trace
// spine dead-ended one hop short of the sessions themselves. This section
// renders real navigation doors to `/sessions/:id`:
//
//   - the investigation session the QA staffer ran (`healing_session_id`)
//   - the failing sessions that seeded the finding (`session_ids[]`)
//
// Each door is a react-router <Link>, not a dead onClick — middle-click and
// open-in-new-tab work, and screen readers announce a "link". When a case
// never spawned an investigation session (`healing_session_id === null`) and
// captured no failing sessions (`session_ids` empty), the section renders
// nothing at all rather than an empty header or a broken link.
// ---------------------------------------------------------------------------

import { Link } from "react-router";

import { cn } from "@/lib/utils";

interface SessionDoorsProps {
  /** The QA staffer's investigation session, or null when none was spawned. */
  healingSessionId: string | null;
  /** Failing sessions that seeded the finding. */
  sessionIds: string[];
  className?: string;
}

/** Compact, stable label for a session UUID (first segment, mono). */
function shortSessionLabel(id: string): string {
  const head = id.split("-")[0] ?? id;
  return head.slice(0, 8) || id;
}

function SessionDoor({ id, label }: { id: string; label: string }) {
  return (
    <Link
      to={`/sessions/${id}`}
      aria-label={`Open ${label} session ${id}`}
      data-testid="qa-session-door"
      className="inline-flex items-baseline gap-1.5 border border-border/60 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.12em] text-foreground underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-1 focus-visible:outline-ring tnum"
    >
      <span className="text-muted-foreground">{label}</span>
      <span>{shortSessionLabel(id)}</span>
    </Link>
  );
}

export function SessionDoors({
  healingSessionId,
  sessionIds,
  className,
}: SessionDoorsProps) {
  const failing = sessionIds ?? [];

  // No investigation session and no failing sessions -> no door, no header.
  if (!healingSessionId && failing.length === 0) {
    return null;
  }

  return (
    <section
      className={cn("space-y-2", className)}
      aria-label="Session trace"
      data-testid="qa-session-doors"
    >
      <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        Session trace · investigation &amp; failing sessions
      </p>
      <div className="flex flex-wrap gap-2">
        {healingSessionId ? (
          <SessionDoor id={healingSessionId} label="investigation" />
        ) : null}
        {failing.map((id) => (
          <SessionDoor key={id} id={id} label="failing" />
        ))}
      </div>
    </section>
  );
}

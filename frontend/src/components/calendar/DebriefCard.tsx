// ---------------------------------------------------------------------------
// DebriefCard -- prompted, unanswered meeting debriefs [bu-q7vx1q.63]
//
// Hidden when nothing is pending; an unreadable list says so instead of reading
// as "all answered". Replying posts to the Relationship butler's pinned
// conversation (see lib/debrief-reply.ts); the text is kept on failure and the
// list is refetched on success.
// ---------------------------------------------------------------------------

import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router";

import type { PendingDebrief } from "@/api/types.ts";
import { Button } from "@/components/ui/button";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Time } from "@/components/ui/time";
import { PENDING_DEBRIEFS_KEY, usePendingMeetingDebriefs } from "@/hooks/use-meeting-debriefs.ts";
import { sendDebriefReply } from "@/lib/debrief-reply.ts";

function attendeeNames(debrief: PendingDebrief): string {
  return debrief.attendees
    .map((a) => a.name || a.email)
    .filter(Boolean)
    .join(", ");
}

function DebriefRow({ debrief }: { debrief: PendingDebrief }) {
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  // Stable across retries of the same text so the server can de-duplicate the submission;
  // a new id is minted once the text changes or the send succeeds.
  const attempt = useRef<{ text: string; messageId: string } | null>(null);

  const send = async () => {
    if (!text.trim() || sending) return;
    setSending(true);
    setError(null);
    try {
      if (attempt.current?.text !== text) {
        attempt.current = { text, messageId: crypto.randomUUID() };
      }
      const id = await sendDebriefReply(debrief, text, attempt.current.messageId);
      attempt.current = null;
      setConversationId(id || null);
      setText("");
      await queryClient.invalidateQueries({ queryKey: PENDING_DEBRIEFS_KEY });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the reply");
    } finally {
      setSending(false);
    }
  };

  const inputId = `debrief-reply-${debrief.debrief_id}`;
  return (
    <li className="flex flex-col gap-1.5 py-2" data-testid="debrief-row">
      <div className="flex flex-wrap items-baseline gap-x-2">
        {debrief.number != null ? (
          <span className="font-mono text-xs text-[var(--mfg)]">{debrief.number}</span>
        ) : null}
        <span className="text-sm font-medium">{debrief.event_title || "Untitled meeting"}</span>
        <Time value={debrief.occurrence_start} className="text-xs text-muted-foreground" />
      </div>
      {attendeeNames(debrief) ? (
        <span className="text-xs text-muted-foreground">With {attendeeNames(debrief)}</span>
      ) : null}
      <div className="flex gap-2">
        <label htmlFor={inputId} className="sr-only">
          Reply to the debrief for {debrief.event_title || "this meeting"}
        </label>
        <input
          id={inputId}
          data-testid="debrief-reply-input"
          className="min-w-0 flex-1 rounded border border-border bg-transparent px-2 py-1 text-sm"
          placeholder='e.g. "send Sam the deck by Friday", or "nothing agreed"'
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void send();
          }}
          disabled={sending}
        />
        <Button
          type="button"
          size="sm"
          data-testid="debrief-reply-send"
          disabled={sending || !text.trim()}
          onClick={() => void send()}
        >
          {sending ? "Sending…" : "Send"}
        </Button>
      </div>
      {error ? (
        <p role="alert" data-testid="debrief-reply-error" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
      {conversationId ? (
        <Link
          to={`/chat/${conversationId}`}
          data-testid="debrief-reply-conversation"
          className="text-xs underline"
        >
          Sent. View conversation
        </Link>
      ) : null}
    </li>
  );
}

export function DebriefCard() {
  const query = usePendingMeetingDebriefs();
  const data = query.data;

  if (query.isError || (data && !data.debriefs_available)) {
    return (
      <div data-testid="debrief-card-degraded" className="pb-2">
        <SourceDegradedNote label="Meeting debriefs" detail="unavailable: pending debriefs unknown" />
      </div>
    );
  }
  if (!data || data.debriefs.length === 0) return null;

  return (
    <section
      aria-label="Meeting debriefs"
      data-testid="debrief-card"
      className="mb-3 rounded border border-border px-4 py-2"
    >
      <h2 className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--mfg)]">
        Debrief · {data.debriefs.length} waiting
      </h2>
      <ul className="divide-y divide-border">
        {data.debriefs.map((debrief) => (
          <DebriefRow key={debrief.debrief_id} debrief={debrief} />
        ))}
      </ul>
    </section>
  );
}

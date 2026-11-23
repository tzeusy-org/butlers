import { useRef, useState } from "react";

import { decideCalendarOrganizer } from "@/api/index";
import type { CalendarOrganizerVerb, CalendarQuarantinedInvitation } from "@/api/types";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const buttonClass =
  "min-h-9 rounded border border-border px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50";

const VERDICT_LABEL: Record<CalendarQuarantinedInvitation["verdict"], string> = {
  first_contact: "First contact",
  blocked: "Blocked sender",
  unknown: "Sender not checked",
};

type Decision = {
  verb: CalendarOrganizerVerb;
  phase: "pending" | "done";
  restampDegraded: boolean;
};

function humanize(token: string): string {
  return token.replace(/_/g, " ");
}

/** Time only: the invitation's title, organizer and guests are never rendered here. */
export function formatQuarantineTime(item: CalendarQuarantinedInvitation): string {
  try {
    const opts: Intl.DateTimeFormatOptions = { timeZone: item.timezone };
    const day = new Intl.DateTimeFormat(undefined, {
      ...opts,
      weekday: "short",
      day: "numeric",
      month: "short",
    }).format(new Date(item.start_at));
    const clock = new Intl.DateTimeFormat(undefined, {
      ...opts,
      hour: "2-digit",
      minute: "2-digit",
    });
    return `${day}, ${clock.format(new Date(item.start_at))} to ${clock.format(new Date(item.end_at))} (${item.timezone})`;
  } catch {
    return `${item.start_at} to ${item.end_at}`;
  }
}

/**
 * Unanswered invitations from senders the owner has not trusted. The owner decides about the
 * sender, never the content: each row shows the time, the verdict and lure badges only.
 * Vouch applies at once (Block undoes it); Block asks for confirmation first. State is
 * optimistic and rolls back on error.
 */
export function CalendarQuarantinePanel({
  items,
  unavailable,
  onDecided,
}: {
  items: CalendarQuarantinedInvitation[];
  unavailable: boolean;
  onDecided?: () => void;
}) {
  const [decisions, setDecisions] = useState(new Map<string, Decision>());
  const [errors, setErrors] = useState(new Map<string, string>());
  const [confirming, setConfirming] = useState<CalendarQuarantinedInvitation | null>(null);
  const busy = useRef(new Set<string>());
  const regionRef = useRef<HTMLElement>(null);
  const blockButtons = useRef(new Map<string, HTMLButtonElement>());

  if (items.length === 0 && decisions.size === 0 && errors.size === 0) return null;

  async function decide(item: CalendarQuarantinedInvitation, verb: CalendarOrganizerVerb) {
    if (unavailable || busy.current.has(item.entry_id)) return;
    busy.current.add(item.entry_id);
    setErrors((prev) => {
      const next = new Map(prev);
      next.delete(item.entry_id);
      return next;
    });
    setDecisions((prev) =>
      new Map(prev).set(item.entry_id, { verb, phase: "pending", restampDegraded: false }),
    );
    try {
      const response = await decideCalendarOrganizer({ entry_id: item.entry_id, verb });
      setDecisions((prev) =>
        new Map(prev).set(item.entry_id, {
          verb,
          phase: "done",
          restampDegraded: response.data.restamp_degraded,
        }),
      );
      onDecided?.();
    } catch {
      // Roll back: the row returns to its server verdict.
      setDecisions((prev) => {
        const next = new Map(prev);
        next.delete(item.entry_id);
        return next;
      });
      setErrors((prev) =>
        new Map(prev).set(
          item.entry_id,
          `${verb === "vouch" ? "Vouch" : "Block"} did not go through. Nothing changed; try again.`,
        ),
      );
    } finally {
      busy.current.delete(item.entry_id);
    }
  }

  return (
    <section
      ref={regionRef}
      tabIndex={-1}
      aria-label="Quarantined invitations"
      className="space-y-2 border-b border-border py-3"
    >
      <p className="text-sm text-muted-foreground">
        Invitations from senders you have not vouched for. Only the time and sender check are shown.
      </p>
      <ul className="space-y-2">
        {items.map((item) => {
          const decision = decisions.get(item.entry_id);
          const when = formatQuarantineTime(item);
          const error = errors.get(item.entry_id);
          const locked = unavailable || decision?.phase === "pending";
          return (
            <li
              key={item.entry_id}
              role="group"
              aria-label={`Quarantined invitation, ${when}`}
              className="flex flex-wrap items-center gap-x-3 gap-y-1"
            >
              <span className="text-sm">{when}</span>
              <span className="text-sm text-muted-foreground">
                {decision
                  ? decision.verb === "vouch"
                    ? "Vouched"
                    : "Blocked"
                  : VERDICT_LABEL[item.verdict]}
              </span>
              {item.lure_badges.map((badge) => (
                <span key={badge} className="rounded border border-border px-1.5 text-xs">
                  {humanize(badge)}
                </span>
              ))}
              <button
                type="button"
                className={buttonClass}
                disabled={locked || decision?.verb === "vouch"}
                aria-label={`Vouch for the sender of the invitation, ${when}`}
                onClick={() => {
                  void decide(item, "vouch");
                }}
              >
                Vouch
              </button>
              {item.verdict !== "blocked" ? (
                <button
                  type="button"
                  className={buttonClass}
                  disabled={locked || decision?.verb === "block"}
                  aria-label={`Block the sender of the invitation, ${when}`}
                  ref={(element) => {
                    if (element) blockButtons.current.set(item.entry_id, element);
                    else blockButtons.current.delete(item.entry_id);
                  }}
                  onClick={() => setConfirming(item)}
                >
                  Block
                </button>
              ) : null}
              {decision?.phase === "pending" ? (
                <span role="status" className="text-sm">
                  {decision.verb === "vouch" ? "Vouching…" : "Blocking…"}
                </span>
              ) : null}
              {decision?.phase === "done" ? (
                <span role="status" className="text-sm">
                  {decision.restampDegraded
                    ? "Saved. Existing invitations from this sender will update shortly."
                    : decision.verb === "vouch"
                      ? "Vouched. Block undoes this."
                      : "Blocked."}
                </span>
              ) : null}
              {error ? <p role="alert" className="w-full text-sm">{error}</p> : null}
            </li>
          );
        })}
      </ul>
      <AlertDialog
        open={confirming !== null}
        onOpenChange={(open) => {
          if (!open) setConfirming(null);
        }}
      >
        <AlertDialogContent
          onCloseAutoFocus={(event) => {
            const target = confirming ? blockButtons.current.get(confirming.entry_id) : null;
            if (target) {
              event.preventDefault();
              target.focus();
            }
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Block this sender?</AlertDialogTitle>
            <AlertDialogDescription>
              Future calendar invitations from this sender are blocked. Their email and messages
              are not affected. Vouch undoes this.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                const item = confirming;
                setConfirming(null);
                if (item) void decide(item, "block");
              }}
            >
              Block sender
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

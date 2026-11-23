import { useEffect, useRef, useState } from "react";

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
import { formatOwnerDateTime } from "@/components/ui/time";

const buttonClass =
  "min-h-9 rounded border border-border px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50";

const VERDICT_LABEL: Record<CalendarQuarantinedInvitation["verdict"], string> = {
  first_contact: "First contact",
  blocked: "Blocked sender",
  unknown: "Sender not checked",
};

type Decision = {
  verb: CalendarOrganizerVerb;
  // pending: request in flight; saved: accepted, list refetch in flight; settled: refetched.
  phase: "pending" | "saved" | "settled";
  restampDegraded: boolean;
};

function humanize(token: string): string {
  return token.replace(/_/g, " ");
}

/** Time only: the invitation's title, organizer and guests are never rendered here. */
function quarantineTime(item: CalendarQuarantinedInvitation): string {
  const start = formatOwnerDateTime(item.start_at, item.timezone);
  const end = formatOwnerDateTime(item.end_at, item.timezone);
  return `${start} to ${end} (${item.timezone})`;
}

function outcomeText(decision: Decision, row: CalendarQuarantinedInvitation | undefined): string {
  if (decision.verb === "block") return "Sender blocked.";
  // A Vouch never overrides a global block: report the verdict the server now holds.
  if (row?.verdict === "blocked") return "Vouch saved. Still blocked by a global rule.";
  if (decision.restampDegraded) return "Vouch saved. Existing invitations will update shortly.";
  return "Sender vouched. Block undoes this.";
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
  /** Refetch the list; the panel waits for it so it can report the verdict that resulted. */
  onDecided?: () => void | Promise<unknown>;
}) {
  const [decisions, setDecisions] = useState(new Map<string, Decision>());
  const [errors, setErrors] = useState(new Map<string, string>());
  const [confirming, setConfirming] = useState<CalendarQuarantinedInvitation | null>(null);
  const busy = useRef(new Set<string>());
  const regionRef = useRef<HTMLElement>(null);
  const blockButtons = useRef(new Map<string, HTMLButtonElement>());
  const closedBy = useRef<"cancel" | "confirm">("cancel");
  const returnTo = useRef<string | null>(null);
  const announced = useRef(new Set<string>());
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    // Once a decision has settled (accepted and the list refetched), announce the real outcome
    // in a panel-level live region that outlives the row, and keep focus from falling to <body>
    // (a vouched row leaves the list, and its disabled button drops focus).
    for (const [entryId, decision] of decisions) {
      if (decision.phase !== "settled" || announced.current.has(entryId)) continue;
      announced.current.add(entryId);
      const row = items.find((item) => item.entry_id === entryId);
      setAnnouncement(outcomeText(decision, row));
      const active = document.activeElement;
      if (!active || active === document.body || regionRef.current?.contains(active)) {
        regionRef.current?.focus();
      }
    }
  }, [decisions, items]);

  if (items.length === 0 && decisions.size === 0 && errors.size === 0 && !announcement) return null;

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
          phase: "saved",
          restampDegraded: response.data.restamp_degraded,
        }),
      );
      try {
        await onDecided?.();
      } catch {
        // The refetch failing leaves the optimistic text; the poll will correct it.
      }
      setDecisions((prev) => {
        const current = prev.get(item.entry_id);
        return current ? new Map(prev).set(item.entry_id, { ...current, phase: "settled" }) : prev;
      });
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
          const when = quarantineTime(item);
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
                {decision && decision.phase !== "settled"
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
                  onClick={() => {
                    returnTo.current = item.entry_id;
                    closedBy.current = "cancel";
                    setConfirming(item);
                  }}
                >
                  Block
                </button>
              ) : null}
              {decision?.phase === "pending" ? (
                <span role="status" className="text-sm">
                  {decision.verb === "vouch" ? "Vouching…" : "Blocking…"}
                </span>
              ) : null}
              {decision?.phase === "saved" ? (
                <span className="text-sm">Saved, checking the result…</span>
              ) : null}
              {decision?.phase === "settled" ? (
                <span className="text-sm">{outcomeText(decision, item)}</span>
              ) : null}
              {error ? <p role="alert" className="w-full text-sm">{error}</p> : null}
            </li>
          );
        })}
      </ul>
      <p role="status" aria-live="polite" className="text-sm">
        {announcement}
      </p>
      <AlertDialog
        open={confirming !== null}
        onOpenChange={(open) => {
          if (!open) setConfirming(null);
        }}
      >
        <AlertDialogContent
          onCloseAutoFocus={(event) => {
            // There is no trigger element for Radix to return to, so place focus ourselves:
            // Cancel goes back to that row's Block button; a confirmed Block unmounts it (the
            // status moves to the live region), so focus the panel.
            event.preventDefault();
            const target = returnTo.current ? blockButtons.current.get(returnTo.current) : null;
            if (closedBy.current === "cancel" && target) target.focus();
            else regionRef.current?.focus();
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
                closedBy.current = "confirm";
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

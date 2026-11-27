import { useMemo, useState } from "react";

import type { CalendarInvitationEntry, ConflictIssue, ResolutionOption } from "@/api/types";
import { optionLabel } from "@/components/calendar/conflict-option-label";
import { supportedOptions } from "@/pages/calendar/use-conflict-resolution";
import type { InvitationResponses } from "@/components/calendar/use-invitation-responses";
import { ListTriageFooterHint } from "@/components/ui/list-triage-footer";
import { useListTriage, type ListTriageVerb } from "@/hooks/use-list-triage";
import { describeChange } from "@/pages/calendar/change-text";
import {
  KIND_LABEL,
  needsYouCountText,
  overlapKey,
  type NeedsYouItem,
  type NeedsYouQueue as Queue,
} from "@/pages/calendar/needs-you-queue";

const KIND_NAME: Record<NeedsYouItem["kind"], string> = {
  invitation: "Invitation",
  conflict: "Overlap",
  proposal: "Proposal",
  debrief: "Debrief",
  change: "Changed",
};

function receiptText(status: string | undefined): string | null {
  switch (status) {
    case undefined:
      return null;
    case "applied":
      return "Response applied";
    case "noop":
      return "Response already matched";
    case "uncertain":
      return "Response outcome uncertain";
    case "failed":
    case "rejected":
      return "Response not applied";
    default:
      return "Response awaiting approval";
  }
}

export interface NeedsYouQueueProps {
  queue: Queue;
  invitations: readonly CalendarInvitationEntry[];
  responses: InvitationResponses;
  timezone: string;
  /** The owner's account addresses were unreadable: changes may include their own edits. */
  changesMayIncludeOwn?: boolean;
  /** The overlap issues the queue's conflict items came from: their options and digests. */
  conflicts?: readonly ConflictIssue[];
  /** Carry out one offered option of an overlap, bound to the digest the owner saw. */
  onResolveConflict?: (issue: ConflictIssue, option: ResolutionOption) => void;
  isResolvingConflict?: boolean;
  /** What the server answered for an overlap answered this session, by `pair_id`. */
  conflictReceipts?: ReadonlyMap<string, string>;
  onMarkChangesSeen: () => void;
  onOpenEntry: (entryId: string, kind: NeedsYouItem["kind"]) => void;
  onAcceptProposal: (proposalId: string) => void;
  onDismissProposal: (proposalId: string) => void;
}

/** The one keyboard queue: soonest first, j/k to move, the selected kind's verbs on plain keys. */
export function NeedsYouQueue({
  queue,
  invitations,
  responses,
  timezone,
  changesMayIncludeOwn = false,
  conflicts = [],
  onResolveConflict,
  isResolvingConflict = false,
  conflictReceipts,
  onMarkChangesSeen,
  onOpenEntry,
  onAcceptProposal,
  onDismissProposal,
}: NeedsYouQueueProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const ids = useMemo(() => queue.items.map((item) => item.id), [queue.items]);
  // A selection whose item left the queue (answered, refetched away) is no selection.
  const selected = queue.items.find((item) => item.id === selectedId) ?? null;
  const invitationById = useMemo(
    () => new Map(invitations.map((entry) => [entry.entry_id, entry] as const)),
    [invitations],
  );

  const conflictByItemId = useMemo(
    () =>
      new Map<string, ConflictIssue>(
        conflicts
          .filter((issue) => issue.kind === "overlap" && issue.events.length > 0)
          .map((issue) => [`conflict:${overlapKey(issue)}`, issue] as const),
      ),
    [conflicts],
  );

  const { respond, receipts } = responses;
  const verbs = useMemo<ListTriageVerb[]>(() => {
    if (!selected) return [];
    const list: ListTriageVerb[] = [];
    const entryId = selected.entryId;
    if (entryId !== null) {
      list.push({
        key: "Enter",
        description: "Open evidence",
        handler: () => onOpenEntry(entryId, selected.kind),
        command: { id: "needs-you-open", label: "Needs you: open evidence" },
      });
    }
    if (selected.kind === "invitation" && entryId !== null) {
      const invitation = invitationById.get(entryId);
      // Only an invitation the response path is configured for can be answered; and once it has
      // a receipt the existing path refuses a second choice, so the keys leave with it.
      if (invitation && invitation.response_configured === true && !receipts.has(entryId)) {
        const choices = [
          { key: "a", value: "accepted", label: "Accept" },
          { key: "d", value: "declined", label: "Decline" },
          // Not "t": the page binds t to "jump to today" and a mis-fired key here would send a real reply.
          { key: "u", value: "tentative", label: "Tentative (unsure)" },
        ] as const;
        for (const choice of choices) {
          list.push({
            key: choice.key,
            description: `${choice.label} invitation`,
            handler: () => void respond(invitation, choice.value),
            command: {
              id: `needs-you-invitation-${choice.value}`,
              label: `Needs you: ${choice.label.toLowerCase()} invitation`,
            },
          });
        }
      }
    }
    if (selected.kind === "proposal") {
      const proposalId = selected.id.slice("proposal:".length);
      list.push(
        {
          key: "a",
          description: "Accept proposal",
          handler: () => onAcceptProposal(proposalId),
          command: { id: "needs-you-proposal-accept", label: "Needs you: accept proposal" },
        },
        {
          key: "d",
          description: "Dismiss proposal",
          handler: () => onDismissProposal(proposalId),
          command: { id: "needs-you-proposal-dismiss", label: "Needs you: dismiss proposal" },
        },
      );
    }
    if (selected.kind === "conflict" && onResolveConflict) {
      const issue = conflictByItemId.get(selected.id);
      const options = issue ? supportedOptions(issue) : [];
      // An overlap the server did not bind a digest and id to, one already answered, or one being
      // answered has no resolve keys: a key without an action is unbound, never a silent no-op.
      const open =
        issue !== undefined &&
        Boolean(issue.issue_digest && issue.pair_id) &&
        !isResolvingConflict &&
        !(issue.pair_id && conflictReceipts?.has(issue.pair_id));
      if (issue && open) {
        // Only the verb is lowercased in the palette form; the event's own title keeps its case.
        const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);
        const bind = (key: string, option: ResolutionOption, id: string, text: string) =>
          list.push({
            key,
            description: text,
            handler: () => onResolveConflict(issue, option),
            command: { id, label: `Needs you: ${lowerFirst(text)}` },
          });
        options.slice(0, 9).forEach((option, index) =>
          bind(String(index + 1), option, `needs-you-conflict-option-${index + 1}`, optionLabel(issue, option)),
        );
        const move = options.find((o) => o.kind === "move_block" || o.kind === "move_own");
        if (move) bind("m", move, "needs-you-conflict-move", optionLabel(issue, move));
        const keep = options.find((o) => o.kind === "keep_both");
        if (keep) bind("x", keep, "needs-you-conflict-dismiss", "Dismiss (keep both)");
      }
    }
    return list;
  }, [
    selected,
    invitationById,
    receipts,
    respond,
    onOpenEntry,
    onAcceptProposal,
    onDismissProposal,
    conflictByItemId,
    onResolveConflict,
    isResolvingConflict,
    conflictReceipts,
  ]);

  const { hints } = useListTriage({
    ids,
    selectedId: selected?.id ?? null,
    onSelect: setSelectedId,
    verbs,
  });

  const unknown = queue.unknownKinds.map((kind) => KIND_LABEL[kind]);
  const count = needsYouCountText(queue);
  const nothingKnown = queue.items.length === 0 && queue.unknownKinds.length === 0;

  return (
    <section aria-label="Needs you" className="border-b border-border py-3">
      <h2 className="text-sm font-medium">
        {(count ?? (nothingKnown ? "Nothing needs you" : "Needs you")) +
          (unknown.length > 0 ? ` · ${unknown.join(", ")} unknown` : "")}
      </h2>
      {unknown.length > 0 ? (
        <p role="status" className="text-xs text-muted-foreground">
          This list may be longer than shown.
        </p>
      ) : null}
      {changesMayIncludeOwn ? (
        <p className="text-xs text-muted-foreground">Changes may include your own edits.</p>
      ) : null}
      {queue.overlapsNotRead ? (
        <p className="text-xs text-muted-foreground">Overlaps are checked in day and week views.</p>
      ) : null}
      <ul className="mt-2 space-y-1">
        {queue.items.map((item) => {
          const conflictPair =
            item.kind === "conflict" ? conflictByItemId.get(item.id)?.pair_id : undefined;
          const receipt =
            (conflictPair ? conflictReceipts?.get(conflictPair) : undefined) ??
            receiptText(
              item.entryId !== null && item.kind === "invitation"
                ? receipts.get(item.entryId)?.status
                : undefined,
            );
          return (
            <li key={item.id}>
              <button
                type="button"
                aria-current={item.id === selected?.id ? "true" : undefined}
                onClick={() => setSelectedId(item.id)}
                className="flex w-full items-baseline gap-2 rounded px-2 py-1 text-left text-sm aria-[current=true]:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
              >
                <span className="w-20 shrink-0 text-xs text-muted-foreground">
                  {KIND_NAME[item.kind]}
                </span>
                <span className="min-w-0 flex-1 truncate">
                  {item.title}
                  {item.change ? (
                    <span className="block truncate text-xs text-muted-foreground">
                      {describeChange(item.change, timezone)}
                    </span>
                  ) : null}
                </span>
                {receipt ? <span className="text-xs text-muted-foreground">{receipt}</span> : null}
              </button>
            </li>
          );
        })}
      </ul>
      {queue.items.some((item) => item.kind === "change") ? (
        <button
          type="button"
          className="mt-2 text-xs underline decoration-border underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
          onClick={onMarkChangesSeen}
        >
          Mark changes seen
        </button>
      ) : null}
      <ListTriageFooterHint bindings={hints} />
    </section>
  );
}

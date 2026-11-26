import { useMemo, useState } from "react";

import type { CalendarInvitationEntry } from "@/api/types";
import type { InvitationResponses } from "@/components/calendar/use-invitation-responses";
import { ListTriageFooterHint } from "@/components/ui/list-triage-footer";
import { useListTriage, type ListTriageVerb } from "@/hooks/use-list-triage";
import {
  KIND_LABEL,
  needsYouCountText,
  type NeedsYouItem,
  type NeedsYouQueue as Queue,
} from "@/pages/calendar/needs-you-queue";

const KIND_NAME: Record<NeedsYouItem["kind"], string> = {
  invitation: "Invitation",
  conflict: "Overlap",
  proposal: "Proposal",
  debrief: "Debrief",
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
  onOpenEntry: (entryId: string) => void;
  onAcceptProposal: (proposalId: string) => void;
  onDismissProposal: (proposalId: string) => void;
}

/** The one keyboard queue: soonest first, j/k to move, the selected kind's verbs on plain keys. */
export function NeedsYouQueue({
  queue,
  invitations,
  responses,
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

  const { respond, receipts } = responses;
  const verbs = useMemo<ListTriageVerb[]>(() => {
    if (!selected) return [];
    const list: ListTriageVerb[] = [];
    const entryId = selected.entryId;
    if (entryId !== null) {
      list.push({
        key: "Enter",
        description: "Open evidence",
        handler: () => onOpenEntry(entryId),
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
          { key: "t", value: "tentative", label: "Tentative" },
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
    return list;
  }, [selected, invitationById, receipts, respond, onOpenEntry, onAcceptProposal, onDismissProposal]);

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
      <p className="text-xs text-muted-foreground">Moved-under-you changes are not tracked yet.</p>
      <ul className="mt-2 space-y-1">
        {queue.items.map((item) => {
          const receipt = receiptText(
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
                <span className="min-w-0 flex-1 truncate">{item.title}</span>
                {receipt ? <span className="text-xs text-muted-foreground">{receipt}</span> : null}
              </button>
            </li>
          );
        })}
      </ul>
      <ListTriageFooterHint bindings={hints} />
    </section>
  );
}

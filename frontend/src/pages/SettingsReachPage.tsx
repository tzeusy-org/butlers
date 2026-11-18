/**
 * SettingsReachPage — /settings/reach
 *
 * The owner's list of break-through clauses: the people, domains, channels and account-security
 * events that are allowed to reach them past quiet hours. One row per clause. The only verb here
 * is revoke, which stops new interruptions at once; past ones stay in the ledger.
 *
 * A clause the owner proposed from Telegram (`/reach add`) waits above the list as a pending
 * proposal. Only Confirm here, in the cookie session, turns it into a clause; Dismiss drops it.
 * There is no free-form "add" form: granting is only ever the confirmation of a named proposal.
 *
 * Design language: Dispatch. Hairline rows, no card chrome.
 */

import { useState } from "react";
import { toast } from "sonner";

import type { ReachClause, ReachProposal } from "@/api/reach";
import { describeClause } from "@/lib/reach-clause";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { InlineActionLink } from "@/components/ui/inline-action-link";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import {
  useConfirmReachProposal,
  useDismissReachProposal,
  useReachClauses,
  useReachProposals,
  useRevokeReachClause,
} from "@/hooks/use-reach-clauses";

export default function SettingsReachPage() {
  const { data, isLoading, isError, refetch } = useReachClauses();
  const revoke = useRevokeReachClause();
  const [revoking, setRevoking] = useState<ReachClause | null>(null);
  const proposalsQuery = useReachProposals();
  const confirm = useConfirmReachProposal();
  const dismiss = useDismissReachProposal();
  const [confirming, setConfirming] = useState<ReachProposal | null>(null);

  const proposals =
    !proposalsQuery.isError && proposalsQuery.data ? proposalsQuery.data.data.proposals : [];

  function onConfirm() {
    if (!confirming) return;
    confirm.mutate(confirming.id, {
      onSuccess: () => {
        setConfirming(null);
        toast.success("Confirmed. Messages from this sender can now break through.");
      },
      onError: () => {
        // It may have expired or been confirmed elsewhere; the settle refetch drops it.
        setConfirming(null);
        toast.error("Could not confirm this proposal. It may have expired.");
      },
    });
  }

  function onDismiss(proposal: ReachProposal) {
    dismiss.mutate(proposal.id, {
      onError: () => toast.error("Could not dismiss this proposal"),
    });
  }

  const clauses = !isError && data ? data.data.clauses : [];

  function onRevoke() {
    if (!revoking) return;
    const target = revoking;
    revoke.mutate(target.id, {
      onSuccess: () => {
        setRevoking(null);
        toast.success("Revoked. New messages from this clause no longer break through.");
      },
      onError: () => {
        // The refetch may already have dropped this row; do not leave its dialog open.
        setRevoking(null);
        toast.error("Could not revoke this clause");
      },
    });
  }

  return (
    <div className="space-y-6">
      <div>
        <Eyebrow as="p" className="mb-2">
          settings · reach
        </Eyebrow>
        <h1 className="text-3xl font-medium tracking-tight leading-tight">Who reaches you</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Senders you declared that may interrupt you, including during quiet hours. Each line you
          receive names the sender and channel only, never the message.
        </p>
      </div>

      {proposalsQuery.isError && (
        <div
          className="attention-row px-4 py-3 flex items-center justify-between"
          data-tone="amber"
          role="alert"
        >
          <p className="text-sm">Could not load proposals from Telegram.</p>
          <InlineActionLink onClick={() => void proposalsQuery.refetch()}>Retry →</InlineActionLink>
        </div>
      )}

      {proposals.length > 0 && (
        <section aria-labelledby="reach-proposals-heading" className="space-y-2">
          <Eyebrow as="h2" id="reach-proposals-heading">
            Proposed from Telegram
          </Eyebrow>
          <p className="text-muted-foreground text-sm">
            Nothing here reaches you until you confirm it. Proposals expire after 24 hours.
          </p>
          <table className="w-full text-sm">
            <thead className="sr-only">
              <tr>
                <th scope="col">Who</th>
                <th scope="col">Scope</th>
                <th scope="col">Expires</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {proposals.map((proposal) => (
                <tr key={proposal.id} className="border-b border-border/60">
                  <td className="py-2 pr-4 font-medium">{describeClause(proposal)}</td>
                  <td className="py-2 pr-4">
                    {proposal.scope === "always" ? "Always" : "Only during quiet hours"}
                  </td>
                  <td className="py-2 pr-4">
                    <Time value={proposal.expires_at} mode="relative-compact" />
                  </td>
                  <td className="py-2 text-right space-x-2">
                    <Button
                      size="xs"
                      aria-label={`Confirm ${describeClause(proposal)}`}
                      onClick={() => setConfirming(proposal)}
                    >
                      Confirm
                    </Button>
                    <Button
                      size="xs"
                      variant="outline"
                      aria-label={`Dismiss ${describeClause(proposal)}`}
                      disabled={dismiss.isPending}
                      onClick={() => onDismiss(proposal)}
                    >
                      Dismiss
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {isLoading ? (
        <div className="space-y-1">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : isError ? (
        <div
          className="attention-row px-4 py-3 flex items-center justify-between"
          data-tone="amber"
          role="alert"
        >
          <p className="text-sm">Could not load your reach list.</p>
          <InlineActionLink onClick={() => void refetch()}>Retry →</InlineActionLink>
        </div>
      ) : clauses.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No one is declared yet, so quiet hours hold everything routine.
        </p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border/60 text-left">
              {["Who", "When", "Until"].map((heading) => (
                <th key={heading} scope="col" className="py-2 pr-4 font-normal">
                  <Eyebrow>{heading}</Eyebrow>
                </th>
              ))}
              <th scope="col" className="py-2 font-normal">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {clauses.map((clause) => (
              <tr key={clause.id} className="border-b border-border/60">
                <td className="py-2 pr-4 font-medium">{describeClause(clause)}</td>
                <td className="py-2 pr-4">
                  {clause.scope === "always" ? "Always" : "Only during quiet hours"}
                </td>
                <td className="py-2 pr-4">
                  {clause.expires_at ? (
                    <Time value={clause.expires_at} mode="relative-compact" />
                  ) : (
                    <span className="text-muted-foreground">no expiry</span>
                  )}
                </td>
                <td className="py-2 text-right">
                  <Button
                    size="xs"
                    variant="outline"
                    aria-label={`Revoke ${describeClause(clause)}`}
                    onClick={() => setRevoking(clause)}
                  >
                    Revoke
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(open) => !open && setRevoking(null)}
        title={`Stop ${revoking ? describeClause(revoking) : "this clause"} reaching you?`}
        description="New messages from them are held like any other routine message from now on. Lines already sent stay in your history."
        confirmLabel="Revoke"
        pendingLabel="Revoking…"
        variant="destructive"
        pending={revoke.isPending}
        onConfirm={onRevoke}
        testId="revoke-reach-dialog"
      />

      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(open) => !open && setConfirming(null)}
        title={`Let ${confirming ? describeClause(confirming) : "this sender"} reach you?`}
        description={
          confirming?.scope === "always"
            ? "Their messages will interrupt you at any time, including during quiet hours."
            : "Their messages will interrupt you during quiet hours."
        }
        confirmLabel="Confirm"
        pendingLabel="Confirming…"
        pending={confirm.isPending}
        onConfirm={onConfirm}
        testId="confirm-reach-dialog"
      />
    </div>
  );
}

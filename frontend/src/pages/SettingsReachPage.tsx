/**
 * SettingsReachPage — /settings/reach
 *
 * The owner's list of break-through clauses: the people, domains, channels and account-security
 * events that are allowed to reach them past quiet hours. One row per clause. The only verb here
 * is revoke, which stops new interruptions at once; past ones stay in the ledger.
 *
 * Granting a clause stays in the cookie-authenticated API and is deliberately not offered here.
 *
 * Design language: Dispatch. Hairline rows, no card chrome.
 */

import { useState } from "react";
import { toast } from "sonner";

import type { ReachClause } from "@/api/reach";
import { describeClause } from "@/lib/reach-clause";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { InlineActionLink } from "@/components/ui/inline-action-link";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { useReachClauses, useRevokeReachClause } from "@/hooks/use-reach-clauses";

export default function SettingsReachPage() {
  const { data, isLoading, isError, refetch } = useReachClauses();
  const revoke = useRevokeReachClause();
  const [revoking, setRevoking] = useState<ReachClause | null>(null);

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
    </div>
  );
}

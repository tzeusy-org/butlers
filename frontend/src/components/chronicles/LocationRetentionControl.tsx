import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Time } from "@/components/ui/time";
import { useLocationRetention, useUpdateLocationRetention } from "@/hooks/use-chronicles";

/** Shared current/archive notice: deadlines never masquerade as purge receipts. */
export function LocationRetentionControl() {
  const query = useLocationRetention();
  const update = useUpdateLocationRetention();
  const [draft, setDraft] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const policy = query.data?.data;
  const validPolicy = policy && Number.isInteger(policy.days) && policy.days >= 1 &&
    policy.days <= 30 && Number.isSafeInteger(policy.version) && policy.version > 0 &&
    policy.precision_after_forgetting_m === 150 && policy.widening_restores_forgotten_points === false;
  if (query.isLoading) return <p role="status" className="text-sm text-muted-foreground">Checking location retention…</p>;
  if (query.isError || !validPolicy) return (
    <div className="text-sm text-muted-foreground" role="status">
      <p>Location retention could not be confirmed. Exact points may remain.</p>
      <Button size="sm" variant="ghost" onClick={() => void query.refetch()}>Retry retention status</Button>
    </div>
  );
  const value = draft ?? String(policy.days);
  const days = /^\d+$/.test(value) ? Number(value) : NaN;
  const valid = Number.isInteger(days) && days >= 1 && days <= 30;
  const submit = () => {
    if (!valid || update.isPending) return;
    if (days < policy.days && confirming !== policy.version) { setConfirming(policy.version); return; }
    update.mutate({days, version: policy.version}, {
      onSuccess: () => { setDraft(null); setConfirming(null); },
      onError: () => { setDraft(null); setConfirming(null); void query.refetch(); },
    });
  };
  const confirmed = (policy.status === "complete" || policy.status === "no_work") &&
    typeof policy.receipt === "string" && policy.receipt.length > 0 &&
    typeof policy.completion_at === "string" && Number.isFinite(Date.parse(policy.completion_at)) &&
    Number.isInteger(policy.deleted_count) && (policy.deleted_count ?? -1) >= 0;
  return (
    <section aria-label="Location retention" className="space-y-2 border-t py-3 text-sm">
      <p>Exact trail retention is {policy.days} days. Forgetting also requires completed projection and verified dependent records.</p>
      <p className="text-muted-foreground">
        {confirmed ? `${policy.deleted_count ?? 0} exact points were deleted in the latest measured attempt.` :
          "Deletion is not confirmed. Exact points may remain while projection or dependent records are pending."}
        {policy.blocked_count != null && policy.blocked_count > 0 ? ` ${policy.blocked_count} overdue points await projection.` : ""}
        {policy.holder_pending_count != null && policy.holder_pending_count > 0 ? ` ${policy.holder_pending_count} overdue points await removal from dependent records.` : ""}
      </p>
      {policy.attempt_started_at ? <p className="text-muted-foreground">Last attempt <Time value={policy.attempt_started_at} /></p> : null}
      <p className="text-muted-foreground">After forgetting, summaries retain approximately 150 m precision. Increasing the period cannot restore forgotten points; already prepared deletions may finish.</p>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="location-retention-days">Keep exact trail for</label>
        <Input id="location-retention-days" type="number" min={1} max={30} step={1}
          className="h-8 w-20" value={value} disabled={update.isPending}
          aria-invalid={!valid} onChange={event => { setDraft(event.target.value); setConfirming(null); }} />
        <span>days</span>
        <Button size="sm" variant="outline" disabled={!valid || days === policy.days || update.isPending}
          onClick={submit}>{update.isPending ? "Saving…" : confirming === policy.version ? "Confirm shorter retention" : "Save retention"}</Button>
        {confirming === policy.version ? <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>Cancel</Button> : null}
      </div>
      {confirming === policy.version ? <p role="alert">Eligible exact points may be permanently forgotten sooner. This cannot be undone.</p> : null}
      {!valid ? <p role="alert">Choose a whole number from 1 to 30.</p> : null}
      {update.isError ? <p role="alert">Retention was not saved. Reloaded the current policy; review it before retrying.</p> : null}
    </section>
  );
}

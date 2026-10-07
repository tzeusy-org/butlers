import { getIdentityDecision } from "@/api/client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useIdentityCandidates, useDecideIdentityCandidate } from "@/hooks/use-entities";
import { FactReporterLine } from "./FactReporterLine";

/** Owner review changes eligibility only after the exact server receipt. */
export function IdentityCandidateReview({ entityId }: { entityId: string }) {
  const query = useIdentityCandidates(entityId);
  const decision = useDecideIdentityCandidate();
  const [pendingFacts, setPendingFacts] = useState<Set<string>>(() => new Set());
  const [uncertainFact, setUncertainFact] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const choose = async (factId: string, value: "adopt" | "reject") => {
    setPendingFacts(current => new Set(current).add(factId));
    setMessage(null);
    setUncertainFact(null);
    try {
      await decision.mutateAsync({ entityId, factId, decision: value });
      setMessage(value === "adopt" ? "Channel adopted." : "Report rejected.");
    } catch (error) {
      setUncertainFact(factId);
      const status = typeof error === "object" && error && "status" in error ? error.status : null;
      setMessage(status === 401 ? "Sign in again to review this report."
        : status === 409 ? "This report changed. Refresh to review the current channels."
        : "Outcome not confirmed. Refresh to check whether the decision was recorded.");
    } finally {
      setPendingFacts(current => {
        const next = new Set(current);
        next.delete(factId);
        return next;
      });
    }
  };
  const refresh = async () => {
    if (uncertainFact) {
      try {
        const receipt = await getIdentityDecision(entityId, uncertainFact);
        setMessage(receipt.decision === "adopt" ? "Channel adoption recorded." : "Rejection recorded.");
        setUncertainFact(null);
      } catch {
        setMessage("No committed decision confirmed. Review the current report before trying again.");
      }
    }
    await query.refetch();
  };
  if (query.isPending) return <p className="text-xs text-muted-foreground" role="status">Checking reported channels…</p>;
  if (query.isError) return (
    <div className="text-sm" role="alert">
      Could not load reported channels.
      <Button size="sm" variant="ghost" onClick={() => void query.refetch()}>Try again</Button>
    </div>
  );
  if (!query.data) return <p className="text-xs text-muted-foreground" role="status">Checking reported channels…</p>;
  const facts = query.data.facts;
  if (!facts.length && !message) return null;
  return (
    <div className="rounded-md border p-3 space-y-3" aria-label="Reported channels awaiting adoption">
      {!!facts.length && <p className="text-sm font-medium">Reported channels</p>}
      {facts.map(fact => (
        <div key={fact.id} aria-busy={pendingFacts.has(fact.id)} className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <p className="text-sm break-all">{fact.object}</p>
            <FactReporterLine fact={fact} />
            <p className="text-xs text-muted-foreground">Unavailable for messages until adopted.</p>
          </div>
          <div className="flex gap-2">
            <Button size="sm" disabled={pendingFacts.has(fact.id)} onClick={() => void choose(fact.id, "adopt")}
              aria-label={`Adopt ${fact.object}`}>Adopt</Button>
            <Button size="sm" variant="outline" disabled={pendingFacts.has(fact.id)} onClick={() => void choose(fact.id, "reject")}
              aria-label={`Reject report for ${fact.object}`}>Reject</Button>
          </div>
        </div>
      ))}
      {message && <p role="status" className="text-sm">{message}
        <Button size="sm" variant="ghost" onClick={() => void refresh()}>Refresh</Button>
      </p>}
    </div>
  );
}

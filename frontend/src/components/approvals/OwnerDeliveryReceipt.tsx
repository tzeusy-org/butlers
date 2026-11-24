import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { acknowledgeOwnerDelivery, getOwnerDeliveryWhy, getPersistedOwnerDeliveryWhy } from "@/api/client";
import type { OwnerDeliveryCard } from "@/api/types";

/** Native disclosure keeps Why keyboard accessible and reads no evidence until opened. */
export function OwnerDeliveryReceipt({ card, presentationId }: { card?: Pick<OwnerDeliveryCard, "approve_token" | "ack_token" | "generation"> | null; presentationId: string }) {
  const [open, setOpen] = useState(false);
  const client = useQueryClient();
  const receipt = useQuery({
    queryKey: ["owner-delivery-why", presentationId, card?.generation],
    queryFn: () => card ? getOwnerDeliveryWhy(card.approve_token) : getPersistedOwnerDeliveryWhy(presentationId), enabled: open,
    retry: false, staleTime: 0,
  });
  const ack = useMutation({
    mutationFn: () => acknowledgeOwnerDelivery(card!.ack_token!),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["approvals"] });
      void client.invalidateQueries({ queryKey: ["decision-sets"] });
    },
  });
  const shown = receipt.isError ? undefined : receipt.data?.data;
  return <section aria-label="Delivery receipt" className="mb-6 text-sm">
    {card?.ack_token && <button type="button" className="underline underline-offset-4"
      disabled={ack.isPending || ack.isSuccess} onClick={() => ack.mutate()}>
      {ack.isSuccess ? "Acknowledged" : "Acknowledge receipt"}
    </button>}
    {ack.isError && <p role="alert">Acknowledgement failed. Refresh this card before trying again.</p>}
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer">Why this delivery?</summary>
      {receipt.isPending && <p role="status">Loading delivery evidence…</p>}
      {receipt.isError && <p role="alert">Evidence unavailable or this card is stale. Refresh the dossier.</p>}
      {shown && !shown.current && <p>This presentation is stale. Receipt only.</p>}
      {shown && <dl className="mt-2 space-y-1 font-mono text-xs">
        <div><dt>Selection cause</dt><dd>{shown.cause.replaceAll("_", " ")}</dd></div>
        <div><dt>Origin</dt><dd>{shown.origin}</dd></div>
        <div><dt>Decision</dt><dd>{shown.decision ?? shown.state}</dd></div>
        <div><dt>Delivery</dt><dd>{shown.delivery_state}</dd></div>
        <div><dt>Presentations</dt><dd>{shown.presentations.map((p) =>
          <p key={`${p.surface}:${p.generation}`}>{p.surface}, generation {p.generation}: {p.state}</p>)}</dd></div>
        <div><dt>Urgency</dt><dd>{shown.urgency_class}{shown.urgency_reason ? ` (${shown.urgency_reason.replaceAll("_", " ")})` : ""}</dd></div>
        <div><dt>Effect</dt><dd>{shown.effect_status}</dd></div>
        <div><dt>Budget</dt><dd>{shown.budget_status}</dd></div>
        <div><dt>Holding reason</dt><dd>{shown.holding_reason?.stage.replaceAll("_", " ") ?? "unknown"}</dd></div>
        <div><dt>Evidence</dt><dd>{shown.evidence.length === 0 ? "unknown" : shown.evidence.map((e) =>
          <p key={e.source}>{e.source.replaceAll("_", " ")}: {e.state}, {Math.round(e.age_seconds)} seconds old</p>)}</dd></div>
      </dl>}
    </details>
  </section>;
}

import type { CalendarInvitationEntry, CalendarResponseReceipt } from "@/api/types";
import {
  buttonClass, choices, useInvitationResponses, type InvitationResponses,
} from "@/components/calendar/use-invitation-responses";

/** The panel over one shared controller, so another surface (the Needs-you queue) acts on the
 *  same request keys and receipts instead of a second copy. */
export function CalendarResponsePanelView({ controller }: { controller: InvitationResponses }) {
  const { unavailable, controls, respond, pending, receipts, errors, review, setReview, requests, requestOwners,
    receiptButtons, closeReceipt } = controller;
  if (controls.length === 0 && receipts.size === 0 && errors.size === 0) return null;
  return <section aria-label="Invitation responses" className="space-y-3 border-b border-border py-3">
    {controls.map((entry) => <div key={entry.entry_id} role="group" aria-label={`Respond to ${entry.title}`}
      className="flex flex-wrap items-center gap-2">
      <span className="text-sm">{entry.title}</span>
      {choices.map((choice) => <button key={choice.value} type="button" className={buttonClass}
        disabled={unavailable || pending.has(entry.entry_id) || receipts.has(entry.entry_id)}
        aria-label={`${choice.label} invitation ${entry.title}`} aria-keyshortcuts={choice.key}
        onKeyDown={(event) => {
          if (event.ctrlKey || event.metaKey || event.altKey) return;
          const shortcut = choices.find((item) => item.key === event.key.toLowerCase());
          if (shortcut) {
            event.preventDefault();
            void respond(entry, shortcut.value);
          }
        }}
        onClick={() => { void respond(entry, choice.value); }}>{choice.label}</button>)}
      {pending.has(entry.entry_id) ? <span role="status">Submitting response…</span> : null}
    </div>)}
    {Array.from(errors, ([entryId, message]) => <div key={entryId}>
      <p role="alert">{message}</p>
      {controls.find((entry) => entry.entry_id === entryId) && !receipts.has(entryId)
        ? <button type="button" className={buttonClass} disabled={pending.has(entryId)}
          onClick={() => {
            const entry = controls.find((item) => item.entry_id === entryId);
            const request = requests.current.get(entryId);
            if (entry && request) void respond(entry, request.response_status);
          }}>Check response outcome</button> : null}
    </div>)}
    {Array.from(receipts, ([entryId, receipt]) => <div key={entryId} className="space-y-1 text-sm">
      <p role="status">{receipt.status === "applied" ? "Response applied." : receipt.status === "noop" ? "Response already matches; no change made."
        : receipt.status === "uncertain" ? "Response outcome uncertain; no second write will be attempted."
        : receipt.status === "failed" ? "Response rejected or unavailable; no applied receipt."
        : receipt.status === "rejected" ? (receipt.reason === "response_approval_expired" ? "Response approval expired." : "Response approval rejected.") : "Response awaiting approved execution."}
        {receipt.status === "applied" && !receipt.projection_available ? " Calendar projection unavailable; the provider receipt is retained." : ""}</p>
      <button type="button" className={buttonClass}
        ref={(element) => { if (element) receiptButtons.current.set(entryId, element); else receiptButtons.current.delete(entryId); }}
        onClick={() => setReview(entryId)}>Review response receipt</button>
      {["pending_approval", "approved"].includes(receipt.status) && controls.find((entry) => entry.entry_id === entryId)
          ? <button type="button" className={buttonClass} disabled={pending.has(entryId)} onClick={() => {
            const entry = controls.find((item) => item.entry_id === entryId);
            const request = requests.current.get(entryId);
            if (entry && request) void respond(entry, request.response_status);
          }}>Check response outcome</button> : null}
      {review === entryId ? <div role="region" aria-label="Response receipt" className="rounded border border-border p-3">
        <p>Owning source: {receipt.source_butler}. Status: {receipt.status}.</p>
        <p>Command: {receipt.command_id}. Approval: {receipt.approval_id}.</p>
        {receipt.approval_id === receipt.command_id && typeof receipt.approval_id === "string"
          && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(receipt.approval_id)
          && requestOwners.current.get(entryId) === receipt.source_butler
          && ["messenger", "relationship"].includes(receipt.source_butler)
          ? <a className={buttonClass}
            href={`/approvals/${encodeURIComponent(receipt.approval_id)}?review_source=${encodeURIComponent(`calendar:${receipt.source_butler}`)}`}>
            Open approval dossier</a> : <p>Approval review unavailable; source or action identity is unverified.</p>}
        <button type="button" className={buttonClass} ref={closeReceipt} onClick={() => {
          setReview(null); receiptButtons.current.get(entryId)?.focus();
        }}>Close receipt</button>
      </div> : null}
    </div>)}
  </section>;
}

export function CalendarResponsePanel(props: {
  entries: CalendarInvitationEntry[];
  unavailable: boolean;
  onObserved?: (receipt: CalendarResponseReceipt) => void;
}) {
  return <CalendarResponsePanelView controller={useInvitationResponses(props)} />;
}

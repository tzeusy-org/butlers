import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { respondToCalendarInvitation, undoCalendarWorkspaceMutation } from "@/api/index";
import type { CalendarInvitationEntry, CalendarResponseReceipt, CalendarResponseRequest } from "@/api/types";
import { useRegisterCommands } from "@/lib/command-registry";

type Receipt = CalendarResponseReceipt & { inverse?: boolean };

type Choice = CalendarResponseRequest["response_status"];
const choices: { value: Choice; label: string; key: string }[] = [
  { value: "accepted", label: "Accept", key: "a" },
  { value: "declined", label: "Decline", key: "d" },
  { value: "tentative", label: "Tentative", key: "t" },
];
const buttonClass = "min-h-9 rounded border border-border px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50";

/** Receipt state belongs to the mounted workspace, not a disappearing inbox row. */
export function CalendarResponsePanel({ entries, unavailable, onObserved }: {
  entries: CalendarInvitationEntry[];
  unavailable: boolean;
  onObserved?: (receipt: CalendarResponseReceipt) => void;
}) {
  const requests = useRef(new Map<string, CalendarResponseRequest>());
  const busy = useRef(new Set<string>());
  const [pending, setPending] = useState(new Set<string>());
  const [receipts, setReceipts] = useState(new Map<string, Receipt>());
  const [errors, setErrors] = useState(new Map<string, string>());
  const receiptButtons = useRef(new Map<string, HTMLButtonElement>());
  const closeReceipt = useRef<HTMLButtonElement>(null);
  const submitted = useRef<string | null>(null);
  const [review, setReview] = useState<string | null>(null);
  const controls = useMemo(() => entries.filter((entry) => entry.response_configured === true), [entries]);

  useEffect(() => {
    if (review) closeReceipt.current?.focus();
  }, [review]);
  useEffect(() => {
    if (submitted.current && receipts.has(submitted.current)) {
      receiptButtons.current.get(submitted.current)?.focus();
      submitted.current = null;
    }
  }, [receipts]);

  const respond = useCallback(async (entry: CalendarInvitationEntry, choice: Choice) => {
    if (unavailable || entry.response_configured !== true || busy.current.has(entry.entry_id)) return;
    const prior = receipts.get(entry.entry_id);
    if (prior && ["applied", "noop", "uncertain", "rejected", "failed"].includes(prior.status)) return;
    let request = requests.current.get(entry.entry_id);
    if (!request) {
      request = { entry_id: entry.entry_id, response_status: choice,
        request_id: crypto.randomUUID(), send_updates: "none" };
      requests.current.set(entry.entry_id, request);
    }
    if (request.response_status !== choice) {
      setErrors((previous) => new Map(previous).set(entry.entry_id,
        "This request already has a response choice. Review its outcome before making another choice."));
      return;
    }
    // One key and choice survive double-click, timeout and ordinary refresh.
    // An unknown network outcome never creates a replacement request.
    submitted.current = entry.entry_id;
    busy.current.add(entry.entry_id);
    setPending(new Set(busy.current));
    try {
      const response = await respondToCalendarInvitation(request);
      setReceipts((previous) => new Map(previous).set(entry.entry_id, response.data));
      onObserved?.(response.data);
      setErrors((previous) => { const next = new Map(previous); next.delete(entry.entry_id); return next; });
    } catch {
      setErrors((previous) => new Map(previous).set(entry.entry_id,
        "Response outcome unavailable. Review this request before trying another response."));
    } finally {
      busy.current.delete(entry.entry_id);
      setPending(new Set(busy.current));
    }
  }, [receipts, unavailable, onObserved]);

  const commands = useMemo(() => controls.flatMap((entry) => choices.map((choice) => ({
    id: `calendar-response:${entry.entry_id}:${choice.value}`,
    label: `${choice.label} invitation: ${entry.title}`,
    perform: () => { void respond(entry, choice.value); },
  }))), [controls, respond]);
  useRegisterCommands(commands);

  async function undo(entryId: string, receipt: CalendarResponseReceipt) {
    if (!receipt.undo_available || !receipt.command_id || busy.current.has(entryId)) return;
    // A network failure can follow a committed inverse reservation. Never
    // offer a second inverse merely because this HTTP response was lost.
    setReceipts((previous) => new Map(previous).set(entryId, { ...receipt, undo_available: false }));
    submitted.current = entryId;
    busy.current.add(entryId);
    setPending(new Set(busy.current));
    try {
      const response = await undoCalendarWorkspaceMutation(receipt.command_id);
      const next = response.data.result as unknown as CalendarResponseReceipt;
      onObserved?.(next);
      // An inverse has its own actual receipt. No optimistic "undone" from a
      // successful HTTP response or pending approval alone.
      setReceipts((previous) => new Map(previous).set(entryId, {
        ...next, undo_available: false, inverse: true,
      }));
    } catch {
      setErrors((previous) => new Map(previous).set(entryId, "Undo unavailable. The original response receipt is retained."));
    } finally {
      busy.current.delete(entryId);
      setPending(new Set(busy.current));
    }
  }

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
      <p role="status">{receipt.status === "applied" ? (receipt.inverse ? "Previous response restored." : "Response applied.") : receipt.status === "noop" ? "Response already matches; no change made."
        : receipt.status === "uncertain" ? "Response outcome uncertain; no second write will be attempted."
        : receipt.status === "failed" ? "Response rejected or unavailable; no applied receipt."
        : receipt.status === "rejected" ? "Response approval rejected." : "Response awaiting approved execution."}
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
      {receipt.undo_available ? <button type="button" className={buttonClass} disabled={pending.has(entryId)}
        onClick={() => { void undo(entryId, receipt); }}>Undo response</button> : null}
      {review === entryId ? <div role="region" aria-label="Response receipt" className="rounded border border-border p-3">
        <p>Owning source: {receipt.source_butler}. Status: {receipt.status}.</p>
        <p>Command: {receipt.command_id}. Approval: {receipt.approval_id}.</p>
        <button type="button" className={buttonClass} ref={closeReceipt} onClick={() => {
          setReview(null); receiptButtons.current.get(entryId)?.focus();
        }}>Close receipt</button>
      </div> : null}
    </div>)}
  </section>;
}

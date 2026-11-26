import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { respondToCalendarInvitation } from "@/api/index";
import type { CalendarInvitationEntry, CalendarResponseReceipt, CalendarResponseRequest } from "@/api/types";
import { useRegisterCommands } from "@/lib/command-registry";

type Receipt = CalendarResponseReceipt;

export type Choice = CalendarResponseRequest["response_status"];
export const choices: { value: Choice; label: string; key: string }[] = [
  { value: "accepted", label: "Accept", key: "a" },
  { value: "declined", label: "Decline", key: "d" },
  { value: "tentative", label: "Tentative", key: "t" },
];
export const buttonClass = "min-h-9 rounded border border-border px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50";

/** Receipt state belongs to the mounted workspace, not a disappearing inbox row. */
export function useInvitationResponses({ entries, unavailable, onObserved }: {
  entries: CalendarInvitationEntry[];
  unavailable: boolean;
  onObserved?: (receipt: CalendarResponseReceipt) => void;
}) {
  const requests = useRef(new Map<string, CalendarResponseRequest>());
  const busy = useRef(new Set<string>());
  const requestOwners = useRef(new Map<string, string>());
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
      requestOwners.current.set(entry.entry_id, entry.butler_name ?? "");
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

  return { unavailable, controls, respond, pending, receipts, errors, review, setReview, requests, requestOwners,
    receiptButtons, closeReceipt };
}

export type InvitationResponses = ReturnType<typeof useInvitationResponses>;


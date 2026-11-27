/**
 * Invitation docket panel (bu-5ii4gj.9 S7, REQ-calendar-invitation-docket-005).
 *
 * Every answer the standing policies prepared is listed with its exact call (event, verdict,
 * whether guests are notified), the policy and version that prepared it, and the facts it matched.
 * Nothing is ticked until the owner ticks it; Submit approves each ticked card individually through
 * the server, and an unticked card stays pending. A card whose event or policy changed since it
 * was shown cannot be ticked and says why; a refused card in a submit is named, never swallowed.
 *
 * Counts that are not clean are never hidden: the unprepared row stays visible even when no card
 * is listed, and invitations whose policies disagree name the policies. Prop-driven for tests;
 * the page wires the query and the submit mutation.
 */

import { useId, useMemo, useState } from "react";

import { DOCKET_MAX_SUBMIT } from "@/api/rsvp-docket.ts";
import type {
  Docket,
  DocketCard,
  DocketDisagreement,
  DocketEvent,
  DocketSubmitItem,
  DocketSubmitResult,
} from "@/api/rsvp-docket.ts";
import { formatEventTime } from "@/lib/calendar-grid.ts";
import { cn } from "@/lib/utils.ts";

const VERDICT_WORDS: Record<string, string> = {
  accepted: "Accept",
  declined: "Decline",
  tentative: "Tentative",
};

const NOTIFY_WORDS: Record<string, string> = {
  all: "notifies everyone",
  externalOnly: "notifies outside guests",
  none: "tells no one",
};

/** Closed refusal / change reasons the server names. Unknown ones show as sent. */
const REASON_WORDS: Record<string, string> = {
  stale_event: "The invitation changed or was answered",
  policy_changed: "The policy was edited since this was prepared",
  policy_revoked: "The policy was revoked",
  policy_no_longer_matches: "The policy no longer matches this invitation",
  facts: "Its facts could not be read right now",
  calendar: "Your calendar could not be read right now",
  no_longer_pending: "Already decided elsewhere",
  call_changed: "The call changed after it was shown",
  unverifiable: "It could not be re-checked right now",
  outcome_unknown: "The outcome is unknown; check the approvals queue before trying again",
  not_attempted: "Not attempted: the batch stopped before it",
};

const UNKNOWN_OUTCOME =
  "The outcome is unknown; the docket has been refreshed. Check it before resubmitting.";

const UNPREPARED_WORDS: Record<string, string> = {
  invitations_unavailable: "invitations unreadable",
  facts_unreadable: "facts unreadable",
  organizer_unreadable: "organizer unreadable",
  card_kept_unreadable: "card kept, calendar unreadable",
  declared_hours: "declared hours unreadable",
};

function docketReasonWords(reason: string | null | undefined): string {
  if (!reason) return "";
  if (REASON_WORDS[reason]) return REASON_WORDS[reason];
  if (reason.startsWith("approval_refused:")) {
    return `The approval path refused it (${reason.slice("approval_refused:".length)})`;
  }
  return reason.replaceAll("_", " ");
}

function whenLabel(event: DocketEvent | null | undefined, timezone: string): string {
  if (!event?.starts_at) return "Time unknown";
  const start = formatEventTime(event.starts_at, timezone, "EEE, MMM d · HH:mm");
  const end = event.ends_at ? formatEventTime(event.ends_at, timezone, "HH:mm") : "";
  return end ? `${start}–${end}` : start;
}

function answerWords(verdict: string, sendUpdates: string): string {
  return `${VERDICT_WORDS[verdict] ?? verdict}, ${NOTIFY_WORDS[sendUpdates] ?? sendUpdates}`;
}

function agreementWords(docket: Docket, card: DocketCard): string | null {
  if (docket.policy_agreement === null) return "agreement unavailable";
  const rows = docket.policy_agreement.filter(
    (row) => row.policy_id === card.policy_id && row.policy_version === card.policy_version,
  );
  if (rows.length === 0) return null;
  const parts = rows.map((row) => `${String(row.count)} ${String(row.outcome).replaceAll("_", " ")}`);
  return parts.join(", ");
}

export interface CalendarDocketPanelProps {
  docket: Docket | undefined;
  isLoading?: boolean;
  isError?: boolean;
  error?: Error | null;
  timezone: string;
  /** Approve the ticked cards; resolves with what was approved and what was refused. */
  onSubmit: (items: DocketSubmitItem[]) => Promise<DocketSubmitResult>;
  submitting?: boolean;
}

export function CalendarDocketPanel({
  docket,
  isLoading = false,
  isError = false,
  error = null,
  timezone,
  onSubmit,
  submitting = false,
}: CalendarDocketPanelProps) {
  const [tickedRaw, setTicked] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<DocketSubmitResult | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const headingId = useId();

  const cards = useMemo(() => docket?.cards ?? [], [docket]);
  const ready = useMemo(() => cards.filter((card) => card.state === "current"), [cards]);
  // A card that is gone or no longer approvable cannot stay ticked: derive, don't sync.
  const titles = useMemo(
    () => new Map(cards.map((card) => [card.action_id, card.event?.title || "An invitation"])),
    [cards],
  );
  const ticked = useMemo(() => {
    const allowed = new Set(ready.map((card) => card.action_id));
    return new Set([...tickedRaw].filter((id) => allowed.has(id)));
  }, [ready, tickedRaw]);

  function toggle(actionId: string) {
    setTicked(() => {
      const next = new Set(ticked);
      if (next.has(actionId)) next.delete(actionId);
      else if (next.size < DOCKET_MAX_SUBMIT) next.add(actionId);
      return next;
    });
  }

  async function submit() {
    const items = ready
      .filter((card) => ticked.has(card.action_id))
      .map((card) => ({ action_id: card.action_id, args_digest: card.args_digest }));
    if (items.length === 0 || submitting) return;
    setSubmitError(null);
    try {
      setResult(await onSubmit(items));
      setTicked(new Set());
    } catch {
      setResult(null);
      // A transport failure or timeout says nothing about what the server did: some cards may
      // already be approved. The docket is refetched on settle; the owner checks it.
      setSubmitError(UNKNOWN_OUTCOME);
    }
  }

  if (isLoading) {
    return (
      <div data-testid="docket-panel" className="flex min-h-0 flex-1 flex-col gap-3">
        <p className="font-mono text-[11px] text-[var(--dim)]">Loading the docket…</p>
      </div>
    );
  }

  if (isError || !docket) {
    return (
      <div data-testid="docket-panel" role="alert" className="flex items-start gap-2 py-1">
        <span className="mt-[2px] font-mono text-[11px] text-[var(--red-text)]">●</span>
        <p className="text-sm text-fg">
          Couldn't read the docket, so nothing can be approved from here.{" "}
          <span className="text-[var(--mfg)]">{error?.message ?? ""}</span>
        </p>
      </div>
    );
  }

  const unpreparedParts = Object.entries(docket.unprepared_reasons)
    .filter(([, count]) => count > 0)
    .map(([reason, count]) => `${count} ${UNPREPARED_WORDS[reason] ?? reason.replaceAll("_", " ")}`);
  const tickedCount = ticked.size;
  // One submit carries at most DOCKET_MAX_SUBMIT cards; the rest wait for the next one.
  const tickAllCount = Math.min(ready.length, DOCKET_MAX_SUBMIT);
  const waiting = Math.max(0, ready.length - DOCKET_MAX_SUBMIT);

  return (
    <section
      data-testid="docket-panel"
      aria-labelledby={headingId}
      className="flex min-h-0 flex-1 flex-col gap-3"
    >
      <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] pb-2">
        <h2
          id={headingId}
          className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--mfg)]"
        >
          Invitation docket
        </h2>
        <span className="flex items-center gap-3 font-mono text-[11px] tabular-nums text-[var(--dim)]">
          <span>{cards.length} prepared</span>
          {ready.length > 0 ? (
            <button
              type="button"
              className="rounded-[3px] border border-[var(--border-strong)] px-2 py-0.5 text-[var(--mfg)] hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
              onClick={() =>
                setTicked(
                  tickedCount === tickAllCount
                    ? new Set()
                    : new Set(ready.slice(0, DOCKET_MAX_SUBMIT).map((c) => c.action_id)),
                )
              }
            >
              {tickedCount === tickAllCount
                ? "Untick all"
                : ready.length > DOCKET_MAX_SUBMIT
                  ? `Tick first ${DOCKET_MAX_SUBMIT}`
                  : `Tick all ${ready.length} ready`}
            </button>
          ) : null}
        </span>
      </div>

      <p data-testid="docket-unprepared" role="status" className="text-sm text-fg">
        {docket.unprepared > 0 ? (
          <>
            <span className="font-medium tabular-nums">{docket.unprepared} unprepared</span>
            <span className="text-[var(--mfg)]">
              {unpreparedParts.length > 0 ? `: ${unpreparedParts.join(", ")}` : ""}. An empty
              docket is not a clean one while this is above zero.
            </span>
          </>
        ) : (
          <span className="text-[var(--mfg)]">0 unprepared. Every invitation was evaluated.</span>
        )}
      </p>
      {!docket.complete && docket.degraded.length > 0 ? (
        <p data-testid="docket-degraded" role="status" className="text-sm text-fg">
          Some facts could not be read ({docket.degraded.map((d) => d.replaceAll("_", " ")).join(", ")}
          ). Nothing new was prepared this time.
        </p>
      ) : null}

      {docket.disagreed.length > 0 ? (
        <ul data-testid="docket-disagreed" className="flex flex-col gap-1">
          {docket.disagreed.map((clash) => (
            <DisagreementRow key={clash.entry_id} clash={clash} timezone={timezone} />
          ))}
        </ul>
      ) : null}

      {cards.length === 0 ? (
        <p data-testid="docket-empty" className="font-mono text-[11px] text-[var(--mfg)]">
          No prepared answers waiting for you.
        </p>
      ) : (
        <ul className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1">
          {cards.map((card) => {
            const approvable = card.state === "current";
            const checkId = `docket-${card.action_id}`;
            const agreement = agreementWords(docket, card);
            return (
              <li
                key={card.action_id}
                data-testid="docket-card"
                data-card-state={card.state}
                className={cn(
                  "rounded-[4px] border border-[var(--border)] bg-foreground/[0.015] p-3",
                  !approvable && "opacity-80",
                )}
              >
                <div className="flex items-start gap-3">
                  <input
                    id={checkId}
                    type="checkbox"
                    className="mt-1 size-4 shrink-0 accent-[var(--fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                    checked={ticked.has(card.action_id)}
                    disabled={
                      !approvable ||
                      submitting ||
                      (!ticked.has(card.action_id) && tickedCount >= DOCKET_MAX_SUBMIT)
                    }
                    onChange={() => toggle(card.action_id)}
                  />
                  <div className="flex min-w-0 flex-1 flex-col gap-1">
                    <label htmlFor={checkId} className="flex min-w-0 cursor-pointer flex-col gap-0.5">
                      <span className="truncate text-sm font-medium text-fg">
                        {card.event?.title || "Untitled invitation"}
                      </span>
                      <span className="font-mono text-[11px] text-[var(--mfg)]">
                        {whenLabel(card.event, timezone)}
                      </span>
                    </label>
                    <p className="text-sm text-fg" data-testid="docket-call">
                      {answerWords(card.verdict, card.send_updates)}
                    </p>
                    <p className="font-mono text-[11px] text-[var(--mfg)]">
                      Policy {card.policy_id.slice(0, 8)} v{card.policy_version}
                      {agreement === "agreement unavailable" ? ` · ${agreement}` : agreement ? ` · you've seen: ${agreement}` : ""}
                    </p>
                    {card.facts.length > 0 ? (
                      <ul className="flex flex-col gap-0.5 font-mono text-[11px] text-[var(--dim)]">
                        {card.facts.map((fact) => (
                          <li key={fact.kind}>
                            {fact.kind.replaceAll("_", " ")}: {fact.note}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    {!approvable ? (
                      <p
                        data-testid="docket-card-reason"
                        className="text-sm text-[var(--amber-text)]"
                      >
                        {card.state === "changed" ? "Changed: " : "Can't re-check: "}
                        {docketReasonWords(card.reason)}. It stays pending.
                      </p>
                    ) : null}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {result ? <SubmitReport result={result} titles={titles} waiting={waiting} /> : null}
      {submitError ? (
        <p data-testid="docket-submit-error" role="alert" className="text-sm text-[var(--red-text)]">
          {submitError}
        </p>
      ) : null}

      <div className="flex items-center justify-end gap-3 border-t border-[var(--border)] pt-2">
        <span className="font-mono text-[11px] text-[var(--mfg)]">
          {tickedCount === 0
            ? "Tick the answers to send. Unticked ones stay pending."
            : `${tickedCount} ticked`}
          {waiting > 0 ? ` · up to ${DOCKET_MAX_SUBMIT} per submit` : ""}
        </span>
        <button
          type="button"
          data-testid="docket-submit"
          disabled={tickedCount === 0 || submitting}
          onClick={() => void submit()}
          className="h-8 rounded-[3px] border border-fg px-3 font-mono text-[11px] text-fg hover:bg-foreground/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:cursor-not-allowed disabled:border-[var(--border-strong)] disabled:text-[var(--dim)]"
        >
          {submitting ? "Approving…" : tickedCount > 0 ? `Approve ${tickedCount} ticked` : "Approve ticked"}
        </button>
      </div>
    </section>
  );
}

function DisagreementRow({ clash, timezone }: { clash: DocketDisagreement; timezone: string }) {
  const named = clash.policies
    .map(
      (p) =>
        `policy ${p.id.slice(0, 8)} v${p.version} (${(VERDICT_WORDS[p.verdict] ?? p.verdict).toLowerCase()})`,
    )
    .join(" and ");
  return (
    <li data-testid="docket-disagreement" className="text-sm text-fg">
      <span className="font-medium">Policies disagree</span> on{" "}
      {clash.event?.title || "an invitation"}{" "}
      <span className="font-mono text-[11px] text-[var(--mfg)]">
        {whenLabel(clash.event, timezone)}
      </span>
      : {named}. Nothing was prepared for it.
    </li>
  );
}

function SubmitReport({
  result,
  titles,
  waiting,
}: {
  result: DocketSubmitResult;
  titles: Map<string, string>;
  waiting: number;
}) {
  const rejected = result.results.filter((r) => r.dispatched && r.execution_failure);
  const sent = result.results.filter((r) => r.dispatched && !r.execution_failure);
  const approvedNotSent = result.results.filter((r) => !r.dispatched);
  const name = (id: string) => titles.get(id) ?? "An invitation";
  return (
    <div data-testid="docket-result" role="status" aria-live="polite" className="text-sm text-fg">
      <p>
        Sent {sent.length}
        {rejected.length > 0 ? `, sent but rejected ${rejected.length}` : ""}
        {approvedNotSent.length > 0 ? `, approved but not sent ${approvedNotSent.length}` : ""}
        {result.refused.length > 0 ? `, refused ${result.refused.length}` : ""}.
        {result.stopped ? " The batch stopped early." : ""}
        {waiting > 0 ? ` ${waiting} more ${waiting === 1 ? "is" : "are"} waiting for the next submit.` : ""}
      </p>
      {rejected.length > 0 ? (
        <ul className="mt-1 flex flex-col gap-0.5">
          {rejected.map((outcome) => (
            <li key={outcome.action_id} data-testid="docket-rejected">
              {name(outcome.action_id)}: sent but rejected
              {outcome.execution_failure?.code ? ` (${outcome.execution_failure.code})` : ""}.
            </li>
          ))}
        </ul>
      ) : null}
      {approvedNotSent.length > 0 ? (
        <ul className="mt-1 flex flex-col gap-0.5">
          {approvedNotSent.map((outcome) => (
            <li key={outcome.action_id} data-testid="docket-not-sent">
              {name(outcome.action_id)}: approved but not sent yet ({outcome.status}). It can be
              retried from the approvals queue.
            </li>
          ))}
        </ul>
      ) : null}
      {result.refused.length > 0 ? (
        <ul className="mt-1 flex flex-col gap-0.5">
          {result.refused.map((refusal) => (
            <li key={refusal.action_id} data-testid="docket-refusal">
              {refusal.event?.title || titles.get(refusal.action_id) || "An invitation"}:{" "}
              {docketReasonWords(refusal.reason)}.
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

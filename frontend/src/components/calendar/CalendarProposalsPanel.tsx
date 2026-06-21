/**
 * Calendar proposals lane — accept / dismiss / edit affordances (bu-0l32t).
 *
 * Renders pending butler-extracted calendar proposals (`view=proposals`) as an
 * actionable lane. Each proposal carries provenance — a confidence chip plus the
 * `source_snippet` / `source_event_id` it was extracted from — and three inline
 * affordances:
 *
 *  - **Accept** → POST /proposals/{id}/accept: creates the butler event on the
 *    Butlers subcalendar and moves the proposal out of the pending lane.
 *  - **Dismiss** → POST /proposals/{id}/dismiss: discards it, no provider write.
 *  - **Edit** → inline overrides (title / start / end) applied before accept.
 *
 * Optimistic UX: an accepted/dismissed proposal is removed from the lane
 * immediately. The merged backend fails closed and is idempotent, so its error
 * semantics are reconciled rather than surfaced as hard failures:
 *  - 404 (unknown) / 409 (lost race — already accepted or dismissed) → the
 *    optimistic removal stands (the row is resolved server-side either way); an
 *    informational toast explains, and the query refetch reconciles canonical
 *    state.
 *  - any other error → the optimistic removal is reverted (row reappears) and an
 *    error toast is shown.
 *
 * Data fetching + cache invalidation live in the page via
 * {@link useCalendarProposals} / {@link useAcceptCalendarProposal} /
 * {@link useDismissCalendarProposal}; this component is prop-driven so it is
 * trivially unit-testable.
 */

import { useMemo, useState } from "react";
import { formatInTimeZone, fromZonedTime } from "date-fns-tz";
import { toast } from "sonner";

import { ApiError } from "@/api/client.ts";
import type {
  CalendarProposalAcceptRequest,
  UnifiedCalendarEntry,
} from "@/api/types.ts";
import type {
  useAcceptCalendarProposal,
  useDismissCalendarProposal,
} from "@/hooks/use-calendar-workspace.ts";
import { formatEventTime, tzDayKey } from "@/lib/calendar-grid.ts";
import { cn } from "@/lib/utils.ts";

/** Format a confidence score (0..1 float) as a rounded percentage. */
function formatConfidence(raw: unknown): string | null {
  if (typeof raw !== "number" || !Number.isFinite(raw)) return null;
  const pct = raw <= 1 ? raw * 100 : raw;
  return `${Math.round(pct)}%`;
}

/** Coerce an unknown metadata value to a trimmed non-empty string, or null. */
function metaString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * ISO → `yyyy-MM-dd'T'HH:mm` for a `datetime-local` input, rendered in the
 * workspace timezone (empty on parse failure).
 */
function toLocalInput(iso: string, tz: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime())
    ? ""
    : formatInTimeZone(parsed, tz, "yyyy-MM-dd'T'HH:mm");
}

/**
 * `datetime-local` string (workspace-local wall clock) → UTC ISO, or null when
 * blank/invalid. The wall-clock value is interpreted in `tz`.
 */
function localInputToIso(value: string, tz: string): string | null {
  if (!value.trim()) return null;
  try {
    const instant = fromZonedTime(value, tz);
    return Number.isNaN(instant.getTime()) ? null : instant.toISOString();
  } catch {
    return null;
  }
}

interface ProposalEditDraft {
  title: string;
  startLocal: string;
  endLocal: string;
}

export interface CalendarProposalsPanelProps {
  /** Pending proposal entries (projected `view=proposals` rows). */
  entries: UnifiedCalendarEntry[];
  /** Whether the proposals query is still loading (first fetch). */
  isLoading?: boolean;
  /** Whether the proposals query errored. */
  isError?: boolean;
  /** The query error, when present. */
  error?: Error | null;
  /** Accept mutation (from {@link useAcceptCalendarProposal}). */
  acceptMutation: ReturnType<typeof useAcceptCalendarProposal>;
  /** Dismiss mutation (from {@link useDismissCalendarProposal}). */
  dismissMutation: ReturnType<typeof useDismissCalendarProposal>;
  /** Workspace timezone used to render proposal times (not browser-local). */
  timezone: string;
}

/** Pill geometry shared with the calendar toolbar (Design Language §4c). */
const PILL =
  "inline-flex items-center justify-center gap-1.5 h-7 rounded-[3px] border px-2.5 " +
  "font-mono text-[11px] leading-none transition-colors " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30 " +
  "disabled:pointer-events-none disabled:opacity-40";

export function CalendarProposalsPanel({
  entries,
  isLoading = false,
  isError = false,
  error = null,
  acceptMutation,
  dismissMutation,
  timezone,
}: CalendarProposalsPanelProps) {
  // Proposals optimistically removed from the lane (accepted / dismissed / gone).
  const [resolved, setResolved] = useState<Set<string>>(new Set());
  // Proposal id currently mid-flight, so its row can show a busy/disabled state.
  const [busyId, setBusyId] = useState<string | null>(null);
  // Inline edit state: the proposal being edited and its working draft.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<ProposalEditDraft | null>(null);

  const visible = useMemo(
    () => entries.filter((entry) => !resolved.has(entry.entry_id)),
    [entries, resolved],
  );

  function markResolved(id: string) {
    setResolved((prev) => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  }

  function revertResolved(id: string) {
    setResolved((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }

  /**
   * Reconcile a failed mutation. A 404/409 means the proposal is already
   * resolved server-side (unknown, or lost a race to a concurrent action), so
   * the optimistic removal stands; any other error reverts it.
   */
  function reconcileError(id: string, err: unknown, action: "accept" | "dismiss") {
    if (err instanceof ApiError && (err.status === 404 || err.status === 409)) {
      const verb = action === "accept" ? "accepted" : "dismissed";
      toast.info(
        err.status === 404
          ? "Proposal no longer exists — it was already resolved."
          : `Proposal was already resolved and could not be ${verb}.`,
      );
      return; // keep the optimistic removal
    }
    revertResolved(id);
    toast.error(
      err instanceof Error ? err.message : `Failed to ${action} the proposal.`,
    );
  }

  async function handleAccept(entry: UnifiedCalendarEntry, overrides?: CalendarProposalAcceptRequest) {
    const id = entry.entry_id;
    setBusyId(id);
    markResolved(id);
    setEditingId(null);
    setEditDraft(null);
    try {
      await acceptMutation.mutateAsync({ proposalId: id, overrides });
      toast.success("Proposal accepted — event added to the Butlers calendar.");
    } catch (err) {
      reconcileError(id, err, "accept");
    } finally {
      setBusyId(null);
    }
  }

  async function handleDismiss(entry: UnifiedCalendarEntry) {
    const id = entry.entry_id;
    setBusyId(id);
    markResolved(id);
    try {
      await dismissMutation.mutateAsync({ proposalId: id });
      toast.success("Proposal dismissed.");
    } catch (err) {
      reconcileError(id, err, "dismiss");
    } finally {
      setBusyId(null);
    }
  }

  function beginEdit(entry: UnifiedCalendarEntry) {
    setEditingId(entry.entry_id);
    setEditDraft({
      title: entry.title,
      startLocal: toLocalInput(entry.start_at, timezone),
      endLocal: toLocalInput(entry.end_at, timezone),
    });
  }

  function cancelEdit() {
    setEditingId(null);
    setEditDraft(null);
  }

  function submitEdit(entry: UnifiedCalendarEntry) {
    if (!editDraft) return;

    // Effective start/end after applying any edits (falling back to the
    // proposal's current values). Guard against an inverted range before we
    // optimistically remove the row and hit the backend.
    const startIso =
      localInputToIso(editDraft.startLocal, timezone) ?? entry.start_at;
    const endIso = localInputToIso(editDraft.endLocal, timezone) ?? entry.end_at;
    const startMs = startIso ? Date.parse(startIso) : NaN;
    const endMs = endIso ? Date.parse(endIso) : NaN;
    if (!Number.isNaN(startMs) && !Number.isNaN(endMs) && startMs >= endMs) {
      toast.error("End time must be after the start time.");
      return;
    }

    const overrides: CalendarProposalAcceptRequest = {};
    const trimmedTitle = editDraft.title.trim();
    if (trimmedTitle && trimmedTitle !== entry.title) {
      overrides.title = trimmedTitle;
    }
    if (startIso && startIso !== entry.start_at) {
      overrides.start_at = startIso;
    }
    if (endIso && endIso !== entry.end_at) {
      overrides.end_at = endIso;
    }
    void handleAccept(entry, Object.keys(overrides).length > 0 ? overrides : undefined);
  }

  if (isLoading) {
    return (
      <div data-testid="proposals-panel" className="flex min-h-0 flex-1 flex-col gap-3">
        <p className="font-mono text-[11px] text-[var(--dim)]">Loading proposals…</p>
      </div>
    );
  }

  if (isError) {
    return (
      <div
        data-testid="proposals-panel"
        role="alert"
        className="flex items-start gap-2 py-1"
      >
        <span className="mt-[2px] font-mono text-[11px] text-[var(--red)]">●</span>
        <p className="text-sm text-[var(--fg)]">
          Failed to load proposals.{" "}
          <span className="text-[var(--mfg)]">
            {error instanceof Error ? error.message : "Unknown error"}
          </span>
        </p>
      </div>
    );
  }

  return (
    <div data-testid="proposals-panel" className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] pb-2">
        <h2 className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--mfg)]">
          Proposals
        </h2>
        <span className="font-mono text-[11px] tabular-nums text-[var(--dim)]">
          {visible.length} pending
        </span>
      </div>

      {visible.length === 0 ? (
        <p
          data-testid="proposals-empty"
          className="font-mono text-[11px] text-[var(--mfg)]"
        >
          No pending proposals — nothing awaiting your review.
        </p>
      ) : (
        <ul className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1">
          {visible.map((entry) => {
            const confidence = formatConfidence(entry.metadata?.confidence);
            const snippet = metaString(entry.metadata?.source_snippet);
            const sourceEventId = metaString(entry.metadata?.source_event_id);
            const isBusy = busyId === entry.entry_id;
            const isEditing = editingId === entry.entry_id;
            const start = new Date(entry.start_at);
            const end = new Date(entry.end_at);
            const startValid = !Number.isNaN(start.getTime());
            const endValid = !Number.isNaN(end.getTime());
            // For a multi-day span (e.g. an overnight event) show the full end
            // date; a bare end time would hide that it ends on another day.
            // Compare days in the workspace timezone so the label matches the
            // rendered times.
            const sameDay =
              startValid &&
              endValid &&
              tzDayKey(start, timezone) === tzDayKey(end, timezone);
            const whenLabel = !startValid
              ? ""
              : `${formatEventTime(start, timezone, "EEE, MMM d · HH:mm")}${
                  !endValid
                    ? ""
                    : sameDay
                      ? `–${formatEventTime(end, timezone, "HH:mm")}`
                      : ` – ${formatEventTime(end, timezone, "EEE, MMM d · HH:mm")}`
                }`;

            return (
              <li
                key={entry.entry_id}
                data-testid="proposal-row"
                data-proposal-id={entry.entry_id}
                className="rounded-[4px] border border-[var(--border)] bg-foreground/[0.015] p-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 flex-col gap-1">
                    <span className="truncate text-sm font-medium text-[var(--fg)]">
                      {entry.title}
                    </span>
                    {whenLabel ? (
                      <span className="font-mono text-[11px] tabular-nums text-[var(--mfg)]">
                        {whenLabel}
                      </span>
                    ) : null}
                  </div>
                  {confidence ? (
                    <span
                      data-testid="proposal-confidence"
                      title={`Extraction confidence: ${confidence}`}
                      className="shrink-0 rounded-[3px] border border-[var(--border-strong)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--mfg)]"
                    >
                      {confidence}
                    </span>
                  ) : null}
                </div>

                {/* Provenance — what the proposal was extracted from. */}
                {snippet || sourceEventId ? (
                  <div
                    data-testid="proposal-provenance"
                    className="mt-2 flex flex-col gap-1 border-l-2 border-[var(--border-strong)] pl-2"
                  >
                    {snippet ? (
                      <span className="font-serif text-[12px] italic leading-snug text-[var(--mfg)]">
                        “{snippet}”
                      </span>
                    ) : null}
                    {sourceEventId ? (
                      <span
                        data-testid="proposal-source-event"
                        title={`Source event ${sourceEventId}`}
                        className="truncate font-mono text-[10px] text-[var(--dim)]"
                      >
                        from event {sourceEventId}
                      </span>
                    ) : null}
                  </div>
                ) : null}

                {/* Inline edit form — overrides applied before accept. */}
                {isEditing && editDraft ? (
                  <div data-testid="proposal-edit-form" className="mt-3 flex flex-col gap-2">
                    <label className="flex flex-col gap-1">
                      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
                        Title
                      </span>
                      <input
                        data-testid="proposal-edit-title"
                        value={editDraft.title}
                        onChange={(e) =>
                          setEditDraft((prev) => (prev ? { ...prev, title: e.target.value } : prev))
                        }
                        className="rounded-[3px] border border-[var(--border-strong)] bg-transparent px-2.5 py-1.5 text-sm text-[var(--fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30"
                      />
                    </label>
                    <div className="flex flex-wrap gap-2">
                      <label className="flex flex-1 flex-col gap-1">
                        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
                          Start
                        </span>
                        <input
                          type="datetime-local"
                          data-testid="proposal-edit-start"
                          value={editDraft.startLocal}
                          onChange={(e) =>
                            setEditDraft((prev) =>
                              prev ? { ...prev, startLocal: e.target.value } : prev,
                            )
                          }
                          className="rounded-[3px] border border-[var(--border-strong)] bg-transparent px-2.5 py-1.5 font-mono text-[12px] text-[var(--fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30"
                        />
                      </label>
                      <label className="flex flex-1 flex-col gap-1">
                        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
                          End
                        </span>
                        <input
                          type="datetime-local"
                          data-testid="proposal-edit-end"
                          value={editDraft.endLocal}
                          onChange={(e) =>
                            setEditDraft((prev) =>
                              prev ? { ...prev, endLocal: e.target.value } : prev,
                            )
                          }
                          className="rounded-[3px] border border-[var(--border-strong)] bg-transparent px-2.5 py-1.5 font-mono text-[12px] text-[var(--fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30"
                        />
                      </label>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        data-testid="proposal-edit-save"
                        disabled={isBusy}
                        onClick={() => submitEdit(entry)}
                        className={cn(
                          PILL,
                          "border-[var(--fg)] bg-[var(--fg)] text-[var(--bg)] hover:opacity-90",
                        )}
                      >
                        Save &amp; accept
                      </button>
                      <button
                        type="button"
                        data-testid="proposal-edit-cancel"
                        disabled={isBusy}
                        onClick={cancelEdit}
                        className={cn(
                          PILL,
                          "border-[var(--border-strong)] text-[var(--mfg)] hover:text-[var(--fg)]",
                        )}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="mt-3 flex items-center gap-2">
                    <button
                      type="button"
                      data-testid="proposal-accept"
                      disabled={isBusy}
                      onClick={() => handleAccept(entry)}
                      className={cn(
                        PILL,
                        "border-[var(--fg)] bg-[var(--fg)] text-[var(--bg)] hover:opacity-90",
                      )}
                    >
                      Accept
                    </button>
                    <button
                      type="button"
                      data-testid="proposal-dismiss"
                      disabled={isBusy}
                      onClick={() => handleDismiss(entry)}
                      className={cn(
                        PILL,
                        "border-[var(--border-strong)] text-[var(--mfg)] hover:text-[var(--fg)]",
                      )}
                    >
                      Dismiss
                    </button>
                    <button
                      type="button"
                      data-testid="proposal-edit"
                      disabled={isBusy}
                      onClick={() => beginEdit(entry)}
                      className={cn(
                        PILL,
                        "border-[var(--border-strong)] text-[var(--mfg)] hover:text-[var(--fg)]",
                      )}
                    >
                      Edit
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default CalendarProposalsPanel;

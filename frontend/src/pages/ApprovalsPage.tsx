/**
 * ApprovalsPage — /approvals and /approvals/:id
 *
 * One Trust Console (JARVIS audit move 9): the queue, the decision dossier,
 * and the standing-autonomy ledger live on one screen so trust is never an
 * orphaned URL.
 *
 * Three-column body:
 *   - Left rail: pending approval summaries, ranked by expiry urgency +
 *     blast radius (not arrival order) — rule-separated rows.
 *   - Center pane: dossier for the selected approval
 *     - title headline (sans 500, 22px)
 *     - why: serif paragraph (max-width 50ch)
 *     - evidence: mono lines (rule-separated)
 *     - proposed_action summary
 *     - primary Approve button, secondary Deny / Defer pill buttons
 *   - Right pane: Autonomy panel — always-visible ledger of standing
 *     autonomy rules (formerly the orphaned /approvals/rules CRUD page),
 *     with live use counts and inline revoke.
 *   - Policy section: quiet-hours editor
 *   - History section: last 30 decided approvals — each row opens its
 *     (read-only) dossier via /approvals/:id.
 *
 * Every approval has a URL (/approvals/:id) so a notification, a history
 * row, or a bookmark can land the owner directly on the decision.
 *
 * No Kanban columns. No charts. No cards.
 *
 * bu-5xiu9 — Phase 6: /approvals replacement
 * bu-86c4c.12 — One Trust Console: Autonomy panel, /approvals/:id, ranking
 */

import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import {
  getApprovalDetail,
  getApprovalsFlat,
  getApprovalsHistory,
  getApprovalsPolicy,
  retryApproval,
  updateApprovalsPolicy,
} from "@/api/index.ts";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import { useListTriage, type ListTriageVerb } from "@/hooks/use-list-triage";
import { ListTriageFooterHint } from "@/components/ui/list-triage-footer";
import { POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy";
import type { ApprovalDetail, ApprovalSummary, ApprovalsPolicy } from "@/api/index.ts";
import {
  useAutonomySuggestions,
  useConfirmAutonomySuggestion,
  useDismissAutonomySuggestion,
} from "@/hooks/use-approvals.ts";
import {
  useApprovalDecisionMutations,
  UNDO_WINDOW_MS,
  type DecisionVerb,
} from "@/hooks/use-approval-decisions.ts";
import { AutonomySuggestionsBanner } from "@/components/approvals/autonomy-suggestions-banner.tsx";
import { AutonomyPanel } from "@/components/approvals/autonomy-panel.tsx";
import { ApprovalsVerdictOpener } from "@/components/approvals/approvals-verdict-opener.tsx";
import { QueryBoundary } from "@/components/ui/query-boundary.tsx";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PENDING_PAGE_SIZE = 100;

// Keyboard-triage decision timing (bu-86c4c.14 — Act loop / hot queue).
// Keyboard verbs (a/d/x) are the fast, blind-fire path — a slip of the key is
// cheap to make during rapid triage — so they route through a short
// delay-then-fire window with an undo toast rather than calling the mutation
// immediately. Mouse clicks on the dossier's own Approve/Deny/Defer buttons
// stay instant (bu-approvals-fast-deny's one-click design), since those are
// deliberate, already-read-the-dossier actions.
//
// The scheduling machinery itself (DecisionVerb, UNDO_WINDOW_MS, the
// module-scoped scheduled-decisions store, scheduleDecision/cancelDecision)
// now lives in useApprovalDecisionMutations (bu-qvnce.4) so DashboardPage's
// one-click attention-list rows share the IDENTICAL undo-window contract
// instead of firing irreversibly.
//
// Keyboard defer has no UI to pick an hour count -- match the dossier's own
// default (see Dossier's deferHours state below).
const KEYBOARD_DEFER_HOURS = 24;

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

const Q = {
  pending: (limit: number) => ["approvals", "flat", "waiting", limit] as const,
  detail: (id: string) => ["approvals", "detail", id] as const,
  history: () => ["approvals", "history"] as const,
  policy: () => ["approvals", "policy"] as const,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtTs(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

// "approved" ALWAYS means approved-but-not-yet-dispatched here (the backend
// only reaches "executed" once the tool call actually ran — see dispatched
// handling below). Rendering it green would claim a success that did not
// happen; it must read as a stalled, actionable amber state, never success
// green (JARVIS audit move 9 — "approved-but-never-dispatched renders amber,
// never success-green").
function statusColor(status: string): string {
  switch (status) {
    case "pending":
      return "text-[var(--amber-text)]";
    case "approved":
      return "text-[var(--amber-text)]";
    case "executed":
      return "text-blue-600 dark:text-blue-400";
    case "rejected":
      return "text-[var(--red-text)]";
    case "expired":
      return "text-muted-foreground";
    default:
      return "text-foreground";
  }
}

// Human label for a status badge. "approved" reads as "stalled" — it is an
// approval that never dispatched, not a settled success state.
function statusLabel(status: string): string {
  return status === "approved" ? "stalled" : status;
}

// ---------------------------------------------------------------------------
// Queue ranking — expiry urgency + blast radius, not arrival order
//
// summary.expires_at previously drove nothing: an approval about to expire
// looked identical to one that just arrived. Rank the pending rail by a
// combination of (1) how soon it expires and (2) how consequential the tool
// call is, so the first screenful is what most needs the owner's attention.
// ---------------------------------------------------------------------------

const HOUR_MS = 3_600_000;

// Coarse blast-radius tiers by tool-name keyword. Outbound/irreversible
// actions (message a human, delete/revoke something) outrank internal data
// writes (assert/store a fact). Unclassified tools default to the middle
// tier rather than silently sorting last.
const HIGH_BLAST_RADIUS = /notify|send|message|email|telegram|delete|revoke/i;
const LOW_BLAST_RADIUS = /assert|store|write|update|create/i;

function blastRadius(toolName: string): number {
  if (HIGH_BLAST_RADIUS.test(toolName)) return 3;
  if (LOW_BLAST_RADIUS.test(toolName)) return 1;
  return 2;
}

function expiryUrgency(expiresAt: string | null | undefined): number {
  if (!expiresAt) return 0;
  const expiresDate = new Date(expiresAt);
  if (Number.isNaN(expiresDate.getTime())) return 0;
  const msLeft = expiresDate.getTime() - Date.now();
  if (msLeft <= 0) return 4; // already past expiry — most urgent
  if (msLeft <= HOUR_MS) return 3;
  if (msLeft <= 6 * HOUR_MS) return 2;
  if (msLeft <= 24 * HOUR_MS) return 1;
  return 0;
}

function rankScore(summary: ApprovalSummary): number {
  // Expiry dominates the sort (it is time-bounded and irreversible once
  // missed); blast radius breaks ties among similarly-urgent items.
  return expiryUrgency(summary.expires_at) * 10 + blastRadius(summary.tool_name);
}

/** Countdown chip text + whether it should render in warning color (<=1h). */
function expiryCountdown(
  expiresAt: string | null | undefined,
): { text: string; warn: boolean } | null {
  if (!expiresAt) return null;
  const expiresDate = new Date(expiresAt);
  if (Number.isNaN(expiresDate.getTime())) return null;
  const msLeft = expiresDate.getTime() - Date.now();
  const warn = msLeft <= HOUR_MS;
  if (msLeft <= 0) return { text: "expired", warn: true };
  const mins = Math.round(msLeft / 60_000);
  if (mins < 60) return { text: `expires in ${mins}m`, warn };
  const hours = Math.round(msLeft / HOUR_MS);
  if (hours < 24) return { text: `expires in ${hours}h`, warn };
  const days = Math.round(msLeft / (24 * HOUR_MS));
  return { text: `expires in ${days}d`, warn };
}

// ---------------------------------------------------------------------------
// Predicate digest
//
// Many approvals are a single subject-predicate-object assertion (e.g.
// relationship_assert_fact: tool_args {subject, predicate, object}, where
// subject/object are entity UUIDs resolved in referenced_entities). When the
// action has that shape, we can render a one-line, human-readable summary —
// "Tze How Lee knows Yustynn Panicker" — instead of making the reviewer parse
// UUIDs out of the evidence block. Returns null for any non-predicate action.
// ---------------------------------------------------------------------------

// Subject/object UUIDs live under different keys depending on the tool
// (relationship_assert_fact uses subject/object; memory_store_fact edge-facts
// use entity_id/object_entity_id). Probe both conventions, first match wins.
const PREDICATE_SUBJECT_KEYS = ["subject", "entity_id", "subject_entity_id"];
const PREDICATE_OBJECT_KEYS = ["object", "object_entity_id"];

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function pickStr(
  args: Record<string, unknown>,
  keys: string[],
): string | undefined {
  for (const k of keys) {
    const v = args[k];
    if (typeof v === "string" && v) return v;
  }
  return undefined;
}

function humanizePredicate(p: string): string {
  return p.replace(/[_-]/g, " ").trim();
}

interface PredicateDigest {
  subject: string;
  predicate: string;
  object: string;
}

function predicateDigest(detail: ApprovalDetail): PredicateDigest | null {
  const args = detail.proposed_action?.tool_args ?? {};
  const predicate = args.predicate;
  if (typeof predicate !== "string" || !predicate) return null;

  const byId = new Map(
    (detail.referenced_entities ?? []).map(
      (e) => [e.id.toLowerCase(), e.name] as const,
    ),
  );

  const subjectId = pickStr(args, PREDICATE_SUBJECT_KEYS);
  const subjectName = subjectId ? byId.get(subjectId.toLowerCase()) : undefined;
  if (!subjectName) return null;

  // Object is usually another entity; fall back to a literal value (object_kind
  // != "entity") when it is not a bare UUID we'd otherwise show raw.
  const objectId = pickStr(args, PREDICATE_OBJECT_KEYS);
  let objectName = objectId ? byId.get(objectId.toLowerCase()) : undefined;
  if (!objectName) {
    const rawObject = args.object;
    if (
      typeof rawObject === "string" &&
      rawObject &&
      !UUID_RE.test(rawObject)
    ) {
      objectName = rawObject;
    }
  }
  if (!objectName) return null;

  return {
    subject: subjectName,
    predicate: humanizePredicate(predicate),
    object: objectName,
  };
}

// ---------------------------------------------------------------------------
// Rail item
// ---------------------------------------------------------------------------

// Present-tense verb label for a scheduled (undo-window) decision.
function pendingVerbLabel(verb: DecisionVerb): string {
  switch (verb) {
    case "approve":
      return "Approving…";
    case "deny":
      return "Denying…";
    case "defer":
      return "Deferring…";
  }
}

function RailItem({
  summary,
  selected,
  onSelect,
  pendingVerb,
  onUndo,
}: {
  summary: ApprovalSummary;
  selected: boolean;
  onSelect: () => void;
  /**
   * Set while a keyboard-triage decision (a/d/x) is scheduled but not yet
   * fired -- the row renders dimmed with a present-tense verb + inline undo
   * instead of its normal status/countdown, and clicking it undoes the
   * decision instead of selecting it (bu-86c4c.14 -- per-item pending state).
   */
  pendingVerb?: DecisionVerb | null;
  onUndo?: () => void;
}) {
  const countdown = expiryCountdown(summary.expires_at);
  const isPending = !!pendingVerb;
  return (
    <button
      onClick={isPending ? onUndo : onSelect}
      data-testid="rail-item"
      data-approval-id={summary.id}
      data-pending-verb={pendingVerb ?? undefined}
      aria-label={isPending ? `${pendingVerbLabel(pendingVerb!)} Undo` : undefined}
      className={[
        "w-full text-left px-3 py-3 border-b border-border last:border-b-0",
        "transition-colors focus-visible:outline focus-visible:outline-2",
        "focus-visible:outline-offset-[-2px] focus-visible:outline-foreground/40",
        isPending
          ? "opacity-50 hover:opacity-70"
          : selected
            ? "bg-foreground/5"
            : "hover:bg-foreground/[0.03]",
      ].join(" ")}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-muted-foreground truncate">
          {summary.butler}
        </span>
        <span
          className={`font-mono text-[10px] uppercase tracking-wider ${statusColor(summary.status)}`}
        >
          {statusLabel(summary.status)}
        </span>
      </div>
      <div className="mt-0.5 text-sm font-medium truncate">
        {summary.tool_name.replace(/_/g, " ")}
      </div>
      {summary.why && (
        <div className="mt-0.5 text-xs text-muted-foreground line-clamp-1 italic">
          {summary.why}
        </div>
      )}
      {isPending ? (
        <div className="mt-1 flex items-center gap-2 text-[10px] font-mono uppercase tracking-wide">
          <span>{pendingVerbLabel(pendingVerb!)}</span>
          <span className="underline underline-offset-2">Undo</span>
        </div>
      ) : (
        <div className="mt-1 flex items-center gap-2 text-[10px] font-mono text-muted-foreground">
          <span>{fmtTs(summary.created_at)}</span>
          {countdown && (
            <span
              className={
                countdown.warn
                  ? "text-[var(--red-text)] font-medium"
                  : "text-muted-foreground"
              }
            >
              · {countdown.text}
            </span>
          )}
        </div>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Dossier pane
// ---------------------------------------------------------------------------

function Dossier({
  actionId,
  onApprove,
  onDeny,
  onDefer,
  approvePending,
  denyPending,
  deferPending,
  pendingVerb,
  onCancelPending,
}: {
  actionId: string;
  onApprove: () => void;
  onDeny: (reason?: string) => void;
  onDefer: (hours: number) => void;
  approvePending: boolean;
  denyPending: boolean;
  deferPending: boolean;
  /**
   * Set when a keyboard-triage decision for this exact approval is scheduled
   * but not yet fired -- the decision cluster is replaced by a single undo
   * affordance so a stale bookmark/back-navigation can't fire a second,
   * conflicting decision on top of the one already counting down
   * (bu-86c4c.14).
   */
  pendingVerb?: DecisionVerb | null;
  onCancelPending?: () => void;
}) {
  const [deferHours, setDeferHours] = useState("24");
  const [showDefer, setShowDefer] = useState(false);
  const [denyReason, setDenyReason] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: Q.detail(actionId),
    queryFn: () => getApprovalDetail(actionId),
    enabled: !!actionId,
  });

  function handleDefer() {
    const h = parseInt(deferHours, 10);
    if (isNaN(h) || h < 1 || h > 168) {
      toast.error("Hours must be 1–168");
      return;
    }
    onDefer(h);
  }

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm font-mono">
        loading…
      </div>
    );
  }

  if (error || !data?.data) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm font-mono">
        failed to load dossier
      </div>
    );
  }

  const detail: ApprovalDetail = data.data;
  const isPending = detail.status === "pending";
  // A keyboard-triage decision for THIS exact approval is scheduled but not
  // yet fired (bu-86c4c.14). Guards against a stale bookmark/back-navigation
  // firing a second, conflicting decision on top of the one already counting
  // down -- the decision cluster below is replaced by a single undo bar.
  const isScheduled = !!pendingVerb;
  const digest = predicateDigest(detail);
  const refEntities = detail.referenced_entities ?? [];
  const expiryChip = expiryCountdown(detail.expires_at);

  return (
    <div className="flex-1 overflow-y-auto p-6 relative">
      {/* Floating decision cluster — a zero-height sticky overlay so it floats
          over the top-right corner without reserving flow space (the body is
          not pushed down). Stays pinned on scroll; the wrapper is click-through
          (pointer-events-none) so content beneath stays interactive. */}
      {isPending && (
        <div className="sticky top-0 z-20 h-0 pointer-events-none">
          <div className="absolute right-0 top-0 flex flex-col items-end gap-2">
            {isScheduled ? (
              /* A keyboard-triage decision is already counting down for this
                 approval -- offer only Undo, not a second competing decision. */
              <div className="pointer-events-auto flex items-center gap-3 rounded-lg border border-border bg-background/85 backdrop-blur-sm px-3 py-2 shadow-sm">
                <span className="font-mono text-xs uppercase tracking-wide text-muted-foreground">
                  {pendingVerbLabel(pendingVerb!)}
                </span>
                <button
                  onClick={onCancelPending}
                  className="py-1 px-3 rounded text-sm border border-border hover:border-foreground/40 transition-colors"
                >
                  Undo
                </button>
              </div>
            ) : (
              <>
                <div className="pointer-events-auto flex items-center gap-2 rounded-lg border border-border bg-background/85 backdrop-blur-sm px-2 py-2 shadow-sm">
                  <button
                    onClick={onApprove}
                    disabled={approvePending}
                    className={[
                      "py-1.5 px-4 rounded font-medium text-sm",
                      "bg-foreground text-background",
                      "hover:opacity-90 disabled:opacity-50 transition-opacity",
                    ].join(" ")}
                  >
                    {approvePending ? "Approving…" : "Approve"}
                  </button>
                  <button
                    onClick={() => onDeny(denyReason.trim() || undefined)}
                    disabled={denyPending}
                    className={[
                      "py-1.5 px-3 rounded text-sm border transition-colors",
                      "border-border text-foreground",
                      "hover:border-destructive/60 hover:text-destructive",
                      "disabled:opacity-50",
                    ].join(" ")}
                  >
                    {denyPending ? "Denying…" : "Deny"}
                  </button>
                  <button
                    onClick={() => setShowDefer(!showDefer)}
                    className={[
                      "py-1.5 px-3 rounded text-sm border transition-colors",
                      "border-border text-foreground hover:border-foreground/40",
                      showDefer ? "border-foreground/40 bg-foreground/5" : "",
                    ].join(" ")}
                  >
                    Defer
                  </button>
                </div>

                {/* Optional deny reason — always available, never gates the click.
                  Leave it blank for a one-tap deny, or jot a note that rides along
                  with the Deny button. Approve/Defer ignore it. */}
                <input
                  value={denyReason}
                  onChange={(e) => setDenyReason(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !denyPending) {
                      onDeny(denyReason.trim() || undefined);
                    }
                  }}
                  placeholder="Deny reason (optional)"
                  className={[
                    "pointer-events-auto w-72 max-w-[80vw] px-2 py-1 text-xs rounded",
                    "border border-border bg-background/85 backdrop-blur-sm shadow-sm",
                    "focus:outline-none focus:border-destructive/50",
                  ].join(" ")}
                />
              </>
            )}

            {/* Digest + referenced entities — pinned under the buttons so the
              "who/what" of the decision stays visible without scrolling. */}
            {(digest || refEntities.length > 0) && (
              <div className="pointer-events-auto w-80 max-w-[80vw] max-h-[50vh] overflow-y-auto space-y-2 p-3 rounded-lg border border-border bg-background/95 backdrop-blur-sm shadow-sm">
                {digest && (
                  <div className="text-sm leading-snug break-words">
                    <span className="text-muted-foreground">Approve: </span>
                    <span className="font-medium text-foreground">
                      {digest.subject}
                    </span>{" "}
                    <span className="italic text-muted-foreground">
                      {digest.predicate}
                    </span>{" "}
                    <span className="font-medium text-foreground">
                      {digest.object}
                    </span>
                  </div>
                )}
                {refEntities.length > 0 && (
                  <div className={digest ? "border-t border-border pt-2" : ""}>
                    <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">
                      Referenced Entities
                    </div>
                    {refEntities.map((ent) => (
                      <div
                        key={ent.id}
                        className="flex items-baseline gap-2 py-0.5 min-w-0"
                      >
                        <span className="text-sm text-foreground font-medium truncate">
                          {ent.name}
                        </span>
                        <span className="text-[10px] font-mono text-muted-foreground truncate shrink-0">
                          {[ent.entity_type, ...(ent.roles ?? [])]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Defer expansion — drops down under the cluster */}
            {!isScheduled && showDefer && (
              <div className="pointer-events-auto w-72 space-y-2 p-3 rounded-lg border border-border bg-background shadow-md">
                <label htmlFor="approvals-defer-hours" className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
                  Hours to defer (1–168)
                </label>
                <input
                  id="approvals-defer-hours"
                  type="number"
                  min={1}
                  max={168}
                  value={deferHours}
                  onChange={(e) => setDeferHours(e.target.value)}
                  className={[
                    "w-full px-2 py-1.5 text-sm border border-border rounded",
                    "bg-background focus:outline-none focus:border-foreground/40",
                  ].join(" ")}
                />
                <button
                  onClick={handleDefer}
                  disabled={deferPending}
                  className="w-full py-1.5 px-3 rounded text-sm border border-border hover:border-foreground/40 transition-colors"
                >
                  {deferPending ? "Deferring…" : "Confirm Defer"}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Body — wrapped so the floating overlay above does not participate in
          this column's vertical rhythm (no phantom gap above the title). */}
      <div className="space-y-6">
        {/* Title — right padding (when pending) keeps the headline clear of the
          floating decision cluster that overlays the top-right corner. */}
        <div className={isPending ? "pr-56" : undefined}>
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">
            {detail.butler} · {fmtTs(detail.created_at)}
            {detail.session_id && (
              <>
                {" · "}
                <Link
                  to={`/sessions/${encodeURIComponent(detail.session_id)}`}
                  className="hover:text-foreground hover:underline normal-case tracking-normal"
                >
                  originating session
                </Link>
              </>
            )}
            {expiryChip && (
              <span
                className={
                  expiryChip.warn
                    ? "ml-2 text-[var(--red-text)] font-medium"
                    : "ml-2"
                }
              >
                {expiryChip.text}
              </span>
            )}
          </div>
          <h2 className="text-[22px] font-medium leading-tight">
            {detail.title}
          </h2>
          <span
            className={`text-xs font-mono uppercase tracking-wide ${statusColor(detail.status)}`}
          >
            {statusLabel(detail.status)}
          </span>
        </div>

        {/* Why — serif paragraph, max-width 50ch */}
        <div className="border-t border-border pt-4">
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-2">
            Why
          </div>
          {detail.why ? (
            <p
              className="text-base leading-relaxed text-foreground"
              style={{
                fontFamily: "Georgia, 'Times New Roman', serif",
                maxWidth: "50ch",
              }}
            >
              {detail.why}
            </p>
          ) : (
            <p
              className="text-sm text-muted-foreground italic"
              style={{
                fontFamily: "Georgia, 'Times New Roman', serif",
                maxWidth: "50ch",
              }}
            >
              No rationale provided.
            </p>
          )}
        </div>

        {/* Referenced entities — resolve UUIDs in the action to canonical names.
          For pending approvals these are surfaced in the floating cluster above;
          here we keep the fuller list (with id prefixes) for decided actions. */}
        {!isPending &&
          detail.referenced_entities &&
          detail.referenced_entities.length > 0 && (
            <div className="border-t border-border pt-4">
              <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-2">
                Referenced Entities
              </div>
              <div>
                {detail.referenced_entities.map((ent, i) => (
                  <div
                    key={ent.id}
                    className={[
                      "flex items-baseline gap-2 py-1.5",
                      i > 0 ? "border-t border-border/50" : "",
                    ].join(" ")}
                  >
                    <span className="text-sm text-foreground font-medium">
                      {ent.name}
                    </span>
                    <span className="text-[11px] font-mono text-muted-foreground">
                      {[ent.entity_type, ...(ent.roles ?? [])]
                        .filter(Boolean)
                        .join(" · ")}
                      {ent.entity_type || (ent.roles && ent.roles.length > 0)
                        ? " · "
                        : ""}
                      {ent.id.slice(0, 8)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

        {/* Evidence — mono lines, rule-separated */}
        {detail.evidence && detail.evidence.length > 0 && (
          <div className="border-t border-border pt-4">
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-2">
              Evidence
            </div>
            <div>
              {detail.evidence.map((line, i) => (
                <div
                  key={i}
                  className={[
                    "py-1.5 font-mono text-xs text-foreground",
                    i > 0 ? "border-t border-border/50" : "",
                  ].join(" ")}
                >
                  {line}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Proposed action */}
        <div className="border-t border-border pt-4">
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-2">
            Proposed Action
          </div>
          <div className="font-mono text-sm">
            <span className="text-foreground font-medium">
              {detail.proposed_action.tool_name}
            </span>
            {detail.proposed_action.agent_summary && (
              <div className="mt-1 text-xs text-muted-foreground">
                {detail.proposed_action.agent_summary}
              </div>
            )}
            <pre className="mt-2 text-[11px] bg-muted/30 rounded px-3 py-2 overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(detail.proposed_action.tool_args, null, 2)}
            </pre>
          </div>
        </div>

        {/* Target contact — resolved from contact_id */}
        {detail.target_contact && (
          <div className="border-t border-border pt-4">
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-2">
              Target Contact
            </div>
            <div className="flex items-center gap-2 flex-wrap text-sm">
              <Link
                to={`/contacts/${encodeURIComponent(detail.target_contact.id)}`}
                className="font-medium text-foreground hover:underline"
              >
                {detail.target_contact.name || detail.target_contact.id}
              </Link>
              {detail.target_contact.roles?.map((role) => (
                <span
                  key={role}
                  className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground border border-border rounded px-1.5 py-0.5 capitalize"
                >
                  {role}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Policy section
// ---------------------------------------------------------------------------

function PolicySection() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ApprovalsPolicy>({
    quiet_start_hour: null,
    quiet_end_hour: null,
    timezone: "UTC",
  });

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: Q.policy(),
    queryFn: getApprovalsPolicy,
  });

  const policy = data?.data;

  const saveMut = useMutation({
    mutationFn: () => updateApprovalsPolicy(draft),
    onSuccess: () => {
      toast.success("Policy saved");
      qc.invalidateQueries({ queryKey: Q.policy() });
      setEditing(false);
    },
    onError: (e: Error) => toast.error(`Save failed: ${e.message}`),
  });

  function startEdit() {
    setDraft(
      policy ?? {
        quiet_start_hour: null,
        quiet_end_hour: null,
        timezone: "UTC",
      },
    );
    setEditing(true);
  }

  return (
    <div className="border-t border-border mt-8 pt-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
            Quiet Hours Policy
          </div>
          <div className="text-sm text-muted-foreground mt-0.5">
            Suppress approval paging during these hours
          </div>
        </div>
        {!editing && (
          <button
            onClick={startEdit}
            className="text-xs font-mono px-2 py-1 border border-border rounded hover:border-foreground/40 transition-colors"
          >
            Edit
          </button>
        )}
      </div>

      {!editing && (
        <QueryBoundary
          isLoading={isLoading}
          isError={isError}
          error={error}
          isEmpty={!policy}
          onRetry={() => void refetch()}
          sourceLabel="the quiet-hours policy"
          loadingFallback={
            <div className="font-mono text-sm text-muted-foreground">loading…</div>
          }
          emptyFallback={
            <div className="font-mono text-sm text-muted-foreground">
              No policy configured yet.
            </div>
          }
        >
          {policy && (
            <div className="font-mono text-sm space-y-1">
              <div>
                <span className="text-muted-foreground">Start:</span>{" "}
                {policy.quiet_start_hour != null
                  ? `${policy.quiet_start_hour}:00`
                  : "—"}
              </div>
              <div>
                <span className="text-muted-foreground">End:</span>{" "}
                {policy.quiet_end_hour != null
                  ? `${policy.quiet_end_hour}:00`
                  : "—"}
              </div>
              <div>
                <span className="text-muted-foreground">Timezone:</span>{" "}
                {policy.timezone}
              </div>
            </div>
          )}
        </QueryBoundary>
      )}

      {editing && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="approvals-quiet-start-hour" className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground block mb-1">
                Start hour (0–23)
              </label>
              <input
                id="approvals-quiet-start-hour"
                type="number"
                min={0}
                max={23}
                value={draft.quiet_start_hour ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({
                    ...d,
                    quiet_start_hour:
                      e.target.value === ""
                        ? null
                        : parseInt(e.target.value, 10),
                  }))
                }
                placeholder="None"
                className="w-full px-2 py-1.5 text-sm border border-border rounded bg-background focus:outline-none focus:border-foreground/40"
              />
            </div>
            <div>
              <label htmlFor="approvals-quiet-end-hour" className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground block mb-1">
                End hour (0–23)
              </label>
              <input
                id="approvals-quiet-end-hour"
                type="number"
                min={0}
                max={23}
                value={draft.quiet_end_hour ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({
                    ...d,
                    quiet_end_hour:
                      e.target.value === ""
                        ? null
                        : parseInt(e.target.value, 10),
                  }))
                }
                placeholder="None"
                className="w-full px-2 py-1.5 text-sm border border-border rounded bg-background focus:outline-none focus:border-foreground/40"
              />
            </div>
          </div>
          <div>
            <label htmlFor="approvals-quiet-timezone" className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground block mb-1">
              Timezone (IANA)
            </label>
            <input
              id="approvals-quiet-timezone"
              value={draft.timezone}
              onChange={(e) =>
                setDraft((d) => ({ ...d, timezone: e.target.value }))
              }
              placeholder="UTC"
              className="w-full px-2 py-1.5 text-sm border border-border rounded bg-background focus:outline-none focus:border-foreground/40"
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => saveMut.mutate()}
              disabled={saveMut.isPending}
              className="px-3 py-1.5 text-sm bg-foreground text-background rounded hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              {saveMut.isPending ? "Saving…" : "Save"}
            </button>
            <button
              onClick={() => setEditing(false)}
              className="px-3 py-1.5 text-sm border border-border rounded hover:border-foreground/40 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// History section
// ---------------------------------------------------------------------------

function RetryDispatchButton({ actionId }: { actionId: string }) {
  const qc = useQueryClient();
  const retryMut = useMutation({
    mutationFn: () => retryApproval(actionId),
    onSuccess: (res) => {
      const action = res?.data;
      const ran = action?.dispatched === true || action?.status === "executed";
      if (ran) {
        toast.success("Dispatched");
      } else {
        toast.warning("Still not run: no reachable butler");
      }
      qc.invalidateQueries({ queryKey: Q.history() });
      qc.invalidateQueries({ queryKey: ["approvals", "flat"] });
    },
    onError: (e: Error) => toast.error(`Retry failed: ${e.message}`),
  });

  return (
    <button
      onClick={() => retryMut.mutate()}
      disabled={retryMut.isPending}
      className={[
        "shrink-0 py-0.5 px-2 rounded text-[10px] font-mono uppercase tracking-wide border",
        "border-border text-foreground transition-colors",
        retryMut.isPending
          ? "opacity-50 cursor-not-allowed"
          : "hover:border-foreground/40",
      ].join(" ")}
    >
      {retryMut.isPending ? "retrying…" : "Retry dispatch"}
    </button>
  );
}

function HistorySection() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: Q.history(),
    queryFn: () => getApprovalsHistory(undefined, 30),
  });

  const items = data?.data ?? [];

  return (
    <div className="border-t border-border mt-8 pt-6">
      <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-4">
        History (last 30)
      </div>
      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={items.length === 0}
        onRetry={() => void refetch()}
        sourceLabel="approval history"
        loadingFallback={
          <div className="text-sm text-muted-foreground font-mono">loading…</div>
        }
        emptyFallback={
          <div className="text-sm text-muted-foreground font-mono">
            No decided approvals yet.
          </div>
        }
      >
        {items.map((item, i) => (
          <div
            key={item.id}
            className={[
              "py-2 flex items-center gap-3",
              i > 0 ? "border-t border-border/50" : "",
            ].join(" ")}
          >
            <span
              className={`font-mono text-[10px] uppercase w-16 shrink-0 ${statusColor(item.status)}`}
            >
              {statusLabel(item.status)}
            </span>
            {/* Auditability: every decided approval opens its (read-only)
                dossier via /approvals/:id — the Dossier pane already renders
                a decided state (no decision cluster, decided_by/decided_at
                shown) when status !== "pending". */}
            <Link
              to={`/approvals/${item.id}`}
              data-testid="history-row-link"
              className="text-sm truncate flex-1 hover:underline"
            >
              {item.tool_name.replace(/_/g, " ")}
            </Link>
            {/* "approved" in History = approved-but-un-run (dispatch silently
                failed). Offer a retry; "executed" rows ran successfully. */}
            {item.status === "approved" && (
              <RetryDispatchButton actionId={item.id} />
            )}
            <span className="font-mono text-xs text-muted-foreground shrink-0">
              {item.butler}
            </span>
            <span className="font-mono text-[10px] text-muted-foreground shrink-0">
              {fmtTs(item.created_at)}
            </span>
          </div>
        ))}
      </QueryBoundary>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Autonomy suggestions section
//
// Surfaces pending promotion/demotion suggestions above the two-pane dossier
// body (spec dashboard-approvals: "Autonomy Suggestions section displayed above
// the existing actions table when pending suggestions exist"). The legacy
// actions table has graduated to the Dispatch dossier layout, so the banner now
// sits between the page header and the two-pane body. The banner renders null
// when no pending suggestions exist; this wrapper also returns null so its
// padding and border do not reserve empty space on the page.
// ---------------------------------------------------------------------------

function AutonomySuggestionsSection() {
  const { data } = useAutonomySuggestions({ status: "pending" });
  const confirmMut = useConfirmAutonomySuggestion();
  const dismissMut = useDismissAutonomySuggestion();

  const suggestions = data?.data ?? [];

  // Mark in-flight suggestions so their card buttons disable while the mutation
  // resolves (the banner reads pendingIds to set per-card disabled state).
  const pendingIds = new Set<string>();
  if (confirmMut.isPending && typeof confirmMut.variables === "string") {
    pendingIds.add(confirmMut.variables);
  }
  if (dismissMut.isPending && dismissMut.variables?.suggestionId) {
    pendingIds.add(dismissMut.variables.suggestionId);
  }

  if (suggestions.length === 0) return null;

  return (
    <div className="px-6 pt-4 pb-2 border-b border-border shrink-0">
      <AutonomySuggestionsBanner
        suggestions={suggestions}
        pendingIds={pendingIds}
        onConfirm={(id) =>
          confirmMut.mutate(id, {
            onSuccess: () => toast.success("Standing rule created"),
            onError: (e: Error) => toast.error(`Confirm failed: ${e.message}`),
          })
        }
        onDismiss={(id) =>
          dismissMut.mutate(
            { suggestionId: id },
            {
              onSuccess: () => toast.success("Suggestion dismissed"),
              onError: (e: Error) =>
                toast.error(`Dismiss failed: ${e.message}`),
            },
          )
        }
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ApprovalsPage() {
  // Every approval has a URL (/approvals/:id) so a notification, a history
  // row, or a bookmark can land the owner directly on the decision. The
  // route param is the source of truth for an EXPLICIT selection; with no
  // :id in the URL the rail falls back to the top-ranked pending item
  // (see effectiveSelected below) without forcing a redirect.
  const { id: routeId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [pendingLimit, setPendingLimit] = useState<number>(PENDING_PAGE_SIZE);

  // Live updates come from the shared fleet event bus (bu-86c4c.8, wired
  // app-wide via RootLayout's EventBusProvider, bu-qvnce.14) — its
  // event-cache-registry.ts approvalPatch invalidates this page's flat /
  // history / detail / metrics keys on every approval state-transition
  // event. The bespoke useApprovalsStream WebSocket that used to live here
  // was deleted (bu-qvnce.14 slice 1): it duplicated that exact invalidation
  // (approvalPatch is a strict superset) and never used its own onEvent
  // callback for anything else. refetchInterval below is a reconciliation
  // sweep — a safety net for the rare case the bus is down, not the primary
  // update path.
  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: Q.pending(pendingLimit),
    queryFn: () => getApprovalsFlat("waiting", pendingLimit),
    refetchInterval: POLL_BUS_RECONCILE_MS,
    // Keep previous data visible while the expanded list is fetching to
    // prevent layout shifts when the limit is bumped (v5: keepPreviousData).
    placeholderData: (prev) => prev,
  });

  // Same queryKey as HistorySection's own useQuery below -- react-query
  // dedupes by key, so this does not add a second network request. Lifted
  // here purely so the verdict opener (JARVIS pursuit move 9) can name a
  // decided-but-never-dispatched approval without HistorySection needing to
  // thread its data back up.
  const { data: historyData, isLoading: historyLoading, isError: historyIsError } = useQuery({
    queryKey: Q.history(),
    queryFn: () => getApprovalsHistory(undefined, 30),
  });

  // `pending` may still carry stale cached rows from before a failed refetch
  // (react-query keeps the last good `data` around). QueryBoundary below
  // checks `isError` BEFORE `isEmpty`, so a fetch failure with zero cached
  // rows renders the distinct "Approvals queue unavailable" state rather than
  // the calm "No pending approvals." -- the exact truth-amnesty defect named
  // in the JARVIS audit (ApprovalsPage.tsx:913-926).
  // Rank by expiry urgency + blast radius rather than arrival order (JARVIS
  // audit move 9): summary.expires_at previously drove nothing, so an
  // approval about to expire looked identical to one that just arrived.
  const rankedPending = useMemo(
    () => [...(data?.data ?? [])].sort((a, b) => rankScore(b) - rankScore(a)),
    [data],
  );
  const pending = rankedPending;

  // Advance the URL selection past a just-decided item, skipping any ids in
  // `skipIds` (other items already scheduled mid-triage). Shared by the
  // instant dossier-button path (via onDecided below) and the keyboard
  // schedule path (called immediately at schedule time, since the real
  // mutation -- and its own onDecided call -- doesn't fire until later).
  // (Function declaration -- hoisted, so it's safe to reference from the
  // useApprovalDecisionMutations() call below before this textual point.)
  function advanceSelectionPast(id: string, skipIds: Set<string> = new Set()) {
    if (routeId !== id) return;
    const idx = rankedPending.findIndex((a) => a.id === id);
    const isUsable = (a: ApprovalSummary) => a.id !== id && !skipIds.has(a.id);
    const nextId =
      rankedPending.slice(idx + 1).find(isUsable)?.id ??
      rankedPending
        .slice(0, idx)
        .reverse()
        .find(isUsable)?.id;
    navigate(nextId ? `/approvals/${nextId}` : "/approvals", { replace: true });
  }

  // Optimistic decision flow (bu-approvals-fast-deny): a decision drops the
  // row from every "waiting" cache immediately, with rollback-on-error --
  // shared with DashboardPage's inline attention-list actions via
  // useApprovalDecisionMutations (bu-86c4c.14, built on useOptimisticMutation
  // from bu-86c4c.13). `undoWindow: true` also gives this page the shared
  // scheduleDecision/cancelDecision/scheduledDecisions undo-window contract
  // (bu-qvnce.4) instead of a page-local copy of the same machinery.
  const {
    approveMut,
    denyMut,
    deferMut,
    scheduledDecisions,
    scheduleDecision: scheduleHookDecision,
    cancelDecision,
  } = useApprovalDecisionMutations({
    onDecided: (id) => advanceSelectionPast(id),
    undoWindow: true,
  });

  // The implicit default selection (no :id in the URL) must skip scheduled
  // items -- otherwise triaging the top-ranked item via keyboard would keep
  // re-selecting the very item just decided until its undo window elapses.
  const actionablePending = useMemo(
    () => rankedPending.filter((a) => !scheduledDecisions.has(a.id)),
    [rankedPending, scheduledDecisions],
  );
  const firstId = actionablePending[0]?.id;
  const effectiveSelected = routeId ?? firstId ?? null;
  // Show "Load more" only when the last response was full (may be more results).
  const hasMore = pending.length === pendingLimit;

  // ---------------------------------------------------------------------
  // Keyboard-triage scheduling (bu-86c4c.14 -- a/d/x, undo-toast)
  //
  // Keyboard verbs are the fast, blind-fire path used while rapidly triaging
  // with j/k -- a slip of the key is cheap to make, so instead of firing the
  // mutation immediately they schedule it UNDO_WINDOW_MS in the future and
  // surface it as an undoable, per-item pending state. Nothing reaches the
  // backend unless the window elapses without an undo.
  // ---------------------------------------------------------------------
  function summaryLabel(id: string): string {
    const summary = rankedPending.find((a) => a.id === id);
    return summary ? summary.tool_name.replace(/_/g, " ") : "approval";
  }

  // Page-specific wrapper around the hook's shared scheduleDecision: adds
  // this page's own toast-with-undo-action + URL-selection-advance behavior.
  // (DashboardPage's rows render their own inline "Approving in 5s" state via
  // AttentionList instead of a toast -- same underlying schedule, different
  // page-level presentation.)
  function scheduleDecision(id: string, verb: DecisionVerb, run: () => void) {
    const scheduled = scheduleHookDecision(id, verb, run);
    if (!scheduled) return; // already scheduled -- ignore repeat verbs

    advanceSelectionPast(id, new Set(scheduledDecisions.keys()));

    toast(`${pendingVerbLabel(verb)} ${summaryLabel(id)}`, {
      action: { label: "Undo", onClick: () => cancelDecision(id) },
      duration: UNDO_WINDOW_MS,
    });
  }

  // j/k roving focus + a/d/x decision verbs + u=undo, active anywhere on the
  // page (not just while a rail item has DOM focus) -- built on the shared
  // useListTriage hook (bu-qvnce.11 slice 4, extracted from this page's own
  // former hand-rolled moveSelection/shortcutBindings). useListTriage itself
  // registers through the shared page-scoped shortcut registry, which
  // publishes these to the '?' help sheet's "On this page" section
  // (previously zero hints existed anywhere in the product for the
  // approvals triage keys, the fleet's best interaction) and centrally
  // guards against a pending g-chord / editable fields / open overlays.
  const pendingIds = useMemo(() => pending.map((p) => p.id), [pending]);

  const approvalVerbs = useMemo<ListTriageVerb[]>(() => {
    if (!effectiveSelected) return [];
    const id = effectiveSelected;
    if (scheduledDecisions.has(id)) {
      return [{ key: "u", description: "Undo scheduled decision", handler: () => cancelDecision(id) }];
    }
    return [
      {
        key: "a",
        description: "Approve selected",
        handler: () => scheduleDecision(id, "approve", () => approveMut.mutate(id)),
      },
      {
        key: "d",
        description: "Deny selected",
        handler: () => scheduleDecision(id, "deny", () => denyMut.mutate({ id })),
      },
      {
        key: "x",
        description: "Defer selected",
        handler: () =>
          scheduleDecision(id, "defer", () => deferMut.mutate({ id, hours: KEYBOARD_DEFER_HOURS })),
      },
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps -- scheduleDecision closes over scheduledDecisions/effectiveSelected directly and is recreated every render; listing the actual dependencies (effectiveSelected, scheduledDecisions, the mutations, cancelDecision) is what keeps this memo fresh without recomputing on every unrelated render.
  }, [effectiveSelected, scheduledDecisions, approveMut, denyMut, deferMut, cancelDecision]);

  const { hints: triageHints } = useListTriage({
    ids: pendingIds,
    selectedId: effectiveSelected,
    // replace: true -- j/k roves the rail one keypress at a time, and a
    // default push here would spam one history entry per keypress, the same
    // "N Back-clicks to leave the page" defect PR #2928's follow-up
    // (bu-k14bg) fixed for free-text filter inputs and bu-wlku1 fixed for
    // SessionsPage's ?selected= mirroring. RailItem's click-select below
    // gets the same treatment for the same reason (bu-2nnhj).
    onSelect: (id) => navigate(`/approvals/${id}`, { replace: true }),
    verbs: approvalVerbs,
  });

  // Keep DOM focus in sync with the current selection so the browser's
  // native focus-visible ring (RailItem's focus-visible:outline classes)
  // visibly tracks j/k roving focus, not just which dossier is shown.
  useEffect(() => {
    if (!effectiveSelected) return;
    const nodes = document.querySelectorAll<HTMLButtonElement>('[data-testid="rail-item"]');
    for (const node of nodes) {
      if (node.getAttribute("data-approval-id") === effectiveSelected) {
        node.focus({ preventScroll: true });
        break;
      }
    }
  }, [effectiveSelected]);

  function handleLoadMore() {
    setPendingLimit((prev) => prev + PENDING_PAGE_SIZE);
  }

  // ---------------------------------------------------------------------
  // Command menu Actions (bu-86c4c.7 — per-page command registration API).
  // "Approve next" only exists while mounted here and while there's a
  // pending item to approve; it disappears from the command menu the
  // moment the owner navigates away or the queue empties.
  // ---------------------------------------------------------------------
  const commandMenuCommands = useMemo<PaletteCommand[]>(() => {
    if (!effectiveSelected) return [];
    return [
      {
        id: "approve-next",
        label: "Approve next",
        keywords: ["approval", "queue"],
        perform: () => approveMut.mutate(effectiveSelected),
        binding: ["a"],
      },
    ];
  }, [effectiveSelected, approveMut]);
  useRegisterCommands(commandMenuCommands);

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Page header */}
      <div className="px-6 pt-6 pb-4 border-b border-border shrink-0">
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">
          system · approvals
        </div>
        <h1 className="text-2xl font-medium">Approvals</h1>
      </div>

      {/* Verdict opener — synthesizes the queue + history data this page
          already fetches into one line (JARVIS pursuit move 9). */}
      <div className="px-6 py-3 border-b border-border shrink-0">
        <ApprovalsVerdictOpener
          pending={pending}
          pendingLoading={isLoading}
          pendingError={isError}
          history={historyData?.data ?? []}
          historyLoading={historyLoading}
          historyError={historyIsError}
        />
      </div>

      {/* Autonomy suggestions — rendered above the two-pane body when pending
          promotion/demotion suggestions exist (returns null otherwise). */}
      <AutonomySuggestionsSection />

      {/* Two-pane body */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Left rail */}
        <div className="w-72 shrink-0 border-r border-border flex flex-col min-h-0">
          <div className="flex-1 overflow-y-auto">
            <QueryBoundary
              isLoading={isLoading}
              isError={isError}
              error={error}
              isEmpty={pending.length === 0}
              onRetry={() => void refetch()}
              sourceLabel="the approvals queue"
              loadingFallback={
                <div className="p-4 text-sm text-muted-foreground font-mono">
                  loading…
                </div>
              }
              emptyFallback={
                <div className="p-4 text-sm text-muted-foreground font-mono">
                  No pending approvals.
                </div>
              }
            >
              {pending.map((summary) => (
                <RailItem
                  key={summary.id}
                  summary={summary}
                  selected={summary.id === effectiveSelected}
                  onSelect={() => navigate(`/approvals/${summary.id}`, { replace: true })}
                  pendingVerb={scheduledDecisions.get(summary.id)?.verb ?? null}
                  onUndo={() => cancelDecision(summary.id)}
                />
              ))}
              {/* Load more — shown only when the previous response was full */}
              {hasMore && (
                <div className="p-3 border-t border-border">
                  <button
                    onClick={handleLoadMore}
                    disabled={isFetching}
                    className={[
                      "w-full py-1.5 px-3 rounded text-xs font-mono border border-border",
                      "text-muted-foreground transition-colors",
                      isFetching
                        ? "opacity-50 cursor-not-allowed"
                        : "hover:border-foreground/40 hover:text-foreground",
                    ].join(" ")}
                  >
                    {isFetching ? "loading…" : "Load more"}
                  </button>
                </div>
              )}
            </QueryBoundary>
          </div>
          {/* Shared footer hint strip (bu-qvnce.11 slice 4) -- advertises the
              EXACT j/k/a/d/x/u bindings useListTriage just registered, so the
              rail's keyboard triage is discoverable without opening '?'. */}
          <ListTriageFooterHint bindings={triageHints} />
        </div>

        {/* Right dossier pane */}
        {effectiveSelected ? (
          <Dossier
            key={effectiveSelected}
            actionId={effectiveSelected}
            onApprove={() => approveMut.mutate(effectiveSelected)}
            onDeny={(reason) => denyMut.mutate({ id: effectiveSelected, reason })}
            onDefer={(hours) => deferMut.mutate({ id: effectiveSelected, hours })}
            // Scoped to THIS approval's id, not just "some mutation of this
            // kind is in flight" -- approving item A no longer mislabels the
            // very next dossier (item B) as already approving (JARVIS audit
            // move 9's page-global-pending finding).
            approvePending={approveMut.isPending && approveMut.variables === effectiveSelected}
            denyPending={denyMut.isPending && denyMut.variables?.id === effectiveSelected}
            deferPending={deferMut.isPending && deferMut.variables?.id === effectiveSelected}
            pendingVerb={scheduledDecisions.get(effectiveSelected)?.verb ?? null}
            onCancelPending={() => cancelDecision(effectiveSelected)}
          />
        ) : (
          <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground font-mono">
            Select a pending approval to review.
          </div>
        )}

        {/* Autonomy panel — always-visible ledger of standing autonomy
            grants (replaces the orphaned /approvals/rules page, which had
            zero inbound links anywhere in the product). */}
        <AutonomyPanel />
      </div>

      {/* Bottom sections — policy and history */}
      <div className="px-6 pb-8 border-t border-border overflow-y-auto shrink-0 max-h-[40vh]">
        <PolicySection />
        <HistorySection />
      </div>
    </div>
  );
}

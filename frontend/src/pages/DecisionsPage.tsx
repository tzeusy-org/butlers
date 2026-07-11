/**
 * DecisionsPage -- /decisions (bu-ckkpz.2, epic bu-ckkpz "Owner Decision Desk")
 *
 * Owner-facing view of the open decision-bead digest GET /api/decisions
 * already computes server-side (butlers.jobs.decision_review, bu-ckkpz.4's
 * title-marker heuristic -- see that module's docstring). Before this page,
 * `grep OWNER|DECISION frontend/src/pages` returned zero hits: the 14+
 * owner-decision beads sitting open since 07-04/05 were invisible anywhere
 * in the dashboard.
 *
 * Layout mirrors the fleet's standard triage-queue shape (ApprovalsPage,
 * IssuesPage, NotificationsPage): a page-opener verdict line synthesizing
 * the digest ("N decisions waiting, oldest Xd"), then a rule-separated row
 * list with j/k roving selection (useListTriage) -- each row is a door: the
 * selected row expands inline to show everything the digest carries about
 * it (age, priority, and escalation detail when it is blocking a P1 bug or
 * a deploy for >48h).
 *
 * There are no approve/deny/close actions here yet -- the structured
 * options/default/deadline convention (bu-ckkpz.1) and the attention-ledger
 * + Telegram one-tap close routing (bu-ckkpz.3) have not shipped, so a
 * decision is detected by title marker only and has no machine-actionable
 * "options" payload. This page is deliberately read-only until those land;
 * see bu-97qrw for the tracked follow-up (switch detection off the title
 * heuristic once bu-ckkpz.1 ships real fields).
 */

import { useEffect, useMemo, useState } from "react";

import { useDecisions } from "@/hooks/use-decisions";
import { useListTriage } from "@/hooks/use-list-triage";
import { ListTriageFooterHint } from "@/components/ui/list-triage-footer";
import { QueryBoundary, SourceDegradedNote } from "@/components/ui/query-boundary.tsx";
import { DecisionsVerdictOpener } from "@/components/decisions/decisions-verdict-opener.tsx";
import type { DecisionBeadSummary } from "@/api/index.ts";

/**
 * Same coarse age vocabulary as DecisionsVerdictOpener's own formatAgeHours
 * (kept in sync manually -- both read `age_hours`/`escalated_block_hours`
 * the same way; mirrors ApprovalsPage/ApprovalsVerdictOpener's identical
 * documented convention for expiry countdowns).
 */
function formatAgeHours(ageHours: number): string {
  if (ageHours >= 24) {
    const days = Math.floor(ageHours / 24);
    return `${days}d`;
  }
  return `${Math.max(Math.floor(ageHours), 0)}h`;
}

function blockedKindLabel(kind: string | null | undefined): string {
  return kind === "deploy" ? "a deploy" : "a P1 bug";
}

// ---------------------------------------------------------------------------
// Row -- selection (via j/k or click) doubles as "open the door": the
// selected row's inline detail panel is the entirety of what the digest
// knows about that decision (mirrors ApprovalsPage's select === view-dossier
// model, scaled down since there is no separate dossier route here).
// ---------------------------------------------------------------------------

function DecisionRow({
  decision,
  selected,
  onSelect,
}: {
  decision: DecisionBeadSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      data-testid="decision-item"
      data-item-id={decision.id}
      onClick={onSelect}
      className={[
        "block w-full text-left px-3 py-3 border-b border-border last:border-b-0",
        "transition-colors focus-visible:outline focus-visible:outline-2",
        "focus-visible:outline-offset-[-2px] focus-visible:outline-foreground/40",
        selected ? "bg-foreground/5" : "hover:bg-foreground/[0.03]",
      ].join(" ")}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-muted-foreground truncate">{decision.id}</span>
        <div className="flex items-center gap-2 shrink-0">
          {decision.priority != null && (
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              P{decision.priority}
            </span>
          )}
          {decision.escalated && (
            <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--red-text)] font-medium">
              escalated
            </span>
          )}
        </div>
      </div>
      <div className="mt-0.5 text-sm font-medium">{decision.title}</div>
      <div className="mt-1 flex items-center gap-2 text-[10px] font-mono text-muted-foreground">
        <span>waiting {formatAgeHours(decision.age_hours)}</span>
        {decision.escalated && (
          <span className="text-[var(--red-text)]">
            · blocking {blockedKindLabel(decision.escalated_blocked_kind)}{" "}
            {decision.escalated_blocked_id} for{" "}
            {formatAgeHours(decision.escalated_block_hours ?? 0)}
          </span>
        )}
      </div>

      {selected && (
        <div
          data-testid="decision-detail"
          className="mt-3 border-t border-border pt-3 text-xs text-muted-foreground space-y-1"
        >
          <div>
            <span className="font-mono uppercase tracking-wide">Created: </span>
            {new Date(decision.created_at).toLocaleString()}
          </div>
          {decision.escalated ? (
            <div>
              Blocking{" "}
              <span className="font-medium text-foreground">
                {decision.escalated_blocked_title}
              </span>{" "}
              ({decision.escalated_blocked_id}), {blockedKindLabel(decision.escalated_blocked_kind)}
              , for {formatAgeHours(decision.escalated_block_hours ?? 0)}.
            </div>
          ) : (
            <div className="italic">
              No structured options yet — the decision-bead convention
              (options/default/deadline, bu-ckkpz.1) hasn't shipped. This
              decision is detected by title marker only.
            </div>
          )}
        </div>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function DecisionsPage() {
  const { data, isLoading, isError, error, refetch } = useDecisions();
  const decisions = useMemo(() => data?.data ?? [], [data]);
  const decisionsAvailable = data?.meta.decisions_available;

  const [selectedId, setSelectedId] = useState<string | null>(null);

  const ids = useMemo(() => decisions.map((d) => d.id), [decisions]);

  // Pure j/k roving selection -- no act-verbs yet (there is nothing
  // machine-actionable to do to a decision until bu-ckkpz.1/.3 ship; see
  // useListTriage's own doc comment: "Omit or return [] for a list that is
  // j/k-navigable but has no keyboard act.").
  const { hints } = useListTriage({
    ids,
    selectedId,
    onSelect: setSelectedId,
  });

  // Keep DOM focus in sync with the current selection, mirroring
  // ApprovalsPage/DashboardPage's identical roving-focus effect.
  useEffect(() => {
    if (!selectedId) return;
    const nodes = document.querySelectorAll<HTMLElement>('[data-testid="decision-item"]');
    for (const node of nodes) {
      if (node.getAttribute("data-item-id") === selectedId) {
        node.focus({ preventScroll: true });
        break;
      }
    }
  }, [selectedId]);

  // Never fabricate an all-clear (CLAUDE.md degraded-envelope convention):
  // `decisions_available === false` means the beads-export digest could not
  // be read -- an empty `decisions` list must not render as "nothing
  // waiting" (bu-jad4j.4's queueDegradedNote pattern from ApprovalsPage).
  const degradedNote =
    decisionsAvailable === false ? (
      <SourceDegradedNote
        label="Decisions"
        detail={`${data?.meta.unavailable_reason ?? "beads export unreachable"} — decisions may be missing`}
        onRetry={() => void refetch()}
        testId="decisions-degraded"
      />
    ) : null;

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Page header */}
      <div className="px-6 pt-6 pb-4 border-b border-border shrink-0">
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">
          system · decisions
        </div>
        <h1 className="text-2xl font-medium">Decisions</h1>
      </div>

      {/* Verdict opener -- "N decisions waiting, oldest Xd" */}
      <div className="px-6 py-3 border-b border-border shrink-0">
        <DecisionsVerdictOpener
          decisions={decisions}
          isLoading={isLoading}
          isError={isError}
          decisionsAvailable={decisionsAvailable}
        />
      </div>

      <div className="flex-1 overflow-y-auto min-h-0">
        <QueryBoundary
          isLoading={isLoading}
          isError={isError}
          error={error}
          isEmpty={decisions.length === 0}
          onRetry={() => void refetch()}
          sourceLabel="the decisions digest"
          loadingFallback={
            <div className="p-6 text-sm text-muted-foreground font-mono">loading…</div>
          }
          emptyFallback={
            // Degraded before empty: a non-genuine empty (the beads export
            // was unreadable) must not read as the calm "No decisions
            // waiting." all-clear.
            degradedNote ? (
              <div className="p-6">{degradedNote}</div>
            ) : (
              <div className="p-6 text-sm text-muted-foreground font-mono">
                No decisions waiting.
              </div>
            )
          }
        >
          <div role="list" aria-label="Open decisions" className="px-6">
            {/* Partial-but-present case would not happen here today (the
                digest is either fully available or fully unavailable, unlike
                approvals' per-butler-pool fan-out) -- the note is still
                rendered above the rows for shape-consistency should that
                ever change. */}
            {degradedNote && <div className="py-3">{degradedNote}</div>}
            {decisions.map((decision) => (
              // role="listitem" lives on this wrapper, not the interactive
              // <button> inside DecisionRow -- overriding a button's own
              // implicit role to "listitem" is an ARIA violation (jsx-a11y
              // no-interactive-element-to-noninteractive-role); wrapping it
              // instead satisfies role="list"'s required-children contract
              // without touching the button's native semantics.
              <div role="listitem" key={decision.id}>
                <DecisionRow
                  decision={decision}
                  selected={decision.id === selectedId}
                  onSelect={() => setSelectedId(decision.id)}
                />
              </div>
            ))}
          </div>
        </QueryBoundary>
      </div>

      {/* Shared footer hint strip advertising the exact j/k bindings
          useListTriage just registered. */}
      <ListTriageFooterHint bindings={hints} />
    </div>
  );
}

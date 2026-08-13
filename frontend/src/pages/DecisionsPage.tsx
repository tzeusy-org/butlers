/**
 * DecisionsPage -- /decisions (bu-ckkpz.2, epic bu-ckkpz "Owner Decision Desk")
 *
 * Owner-facing view of the open decision-bead digest GET /api/decisions
 * already computes server-side (butlers.jobs.decision_review, bu-ckkpz.4's
 * label-only classifier -- see that module's docstring). Before this page,
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
 * There are no approve/deny/close actions here yet. This deliberately
 * read-only digest projects source-authored decision context when available,
 * but never carries mutation controls.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { useDecisions } from "@/hooks/use-decisions";
import { useListTriage } from "@/hooks/use-list-triage";
import { ListTriageFooterHint } from "@/components/ui/list-triage-footer";
import { QueryBoundary, SourceDegradedNote } from "@/components/ui/query-boundary.tsx";
import { DecisionsVerdictOpener } from "@/components/decisions/decisions-verdict-opener.tsx";
import { Time } from "@/components/ui/time";
import type { DecisionBeadSummary } from "@/api/index.ts";
import { beadDetailPath } from "@/lib/bead-detail";

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

/**
 * bu-hmdqz.6: the export's own age, always shown when known -- a stale
 * (but not yet 14-day-stale, which flips `decisions_available` to false)
 * beads export must still be visible as stale, not rendered as calm current
 * data (CLAUDE.md degraded-envelope convention: "stale export must be
 * visible, not calm"). Same-day exports read "as of just now" rather than
 * "as of 0h ago" to avoid implying false precision.
 */
function formatExportAsOf(exportAsOf: string | null | undefined): string | null {
  if (!exportAsOf) return null;
  const then = new Date(exportAsOf).getTime();
  if (Number.isNaN(then)) return null;
  const ageHours = (Date.now() - then) / (1000 * 60 * 60);
  if (ageHours < 1) return "export as of just now";
  return `export as of ${formatAgeHours(ageHours)} ago`;
}

/** Past this age the export plaque switches from muted to a warning tint --
 * well before the 14-day `_STALE_EXPORT_AGE` cliff that flips availability
 * off entirely, so a slowly-aging export gets a visible tell early. */
const _EXPORT_AS_OF_WARN_HOURS = 48;

/**
 * Whether *exportAsOf* is old enough to warrant the warning tint. A
 * standalone function (mirrors time.tsx's resolveSmartMode) so the
 * react-hooks/purity rule doesn't flag a direct Date.now() call inside the
 * component body.
 */
function resolveExportAsOfIsWarn(exportAsOf: string | null | undefined): boolean {
  if (!exportAsOf) return false;
  const then = new Date(exportAsOf).getTime();
  if (Number.isNaN(then)) return false;
  const ageHours = (Date.now() - then) / (1000 * 60 * 60);
  return ageHours >= _EXPORT_AS_OF_WARN_HOURS;
}

function blockedKindLabel(kind: string | null | undefined): string {
  return kind === "deploy" ? "a deploy" : "a P1 bug";
}

function formatStructuredDetailsReason(reason: string | null | undefined): string {
  return reason ? reason.replaceAll("_", " ") : "source metadata unavailable";
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
  const detailId = `decision-detail-${decision.id}`;

  return (
    <div
      className={[
        "border-b border-border last:border-b-0",
        selected ? "bg-foreground/5" : "",
      ].join(" ")}
    >
      <button
        type="button"
        data-testid="decision-item"
        data-item-id={decision.id}
        aria-expanded={selected}
        aria-controls={selected ? detailId : undefined}
        onClick={onSelect}
        className={[
          "block w-full text-left px-3 py-3 transition-colors",
          "focus-visible:outline focus-visible:outline-2",
          "focus-visible:outline-offset-[-2px] focus-visible:outline-foreground/40",
          selected ? "" : "hover:bg-foreground/[0.03]",
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
      </button>

      <div className="px-3 pb-3">
        <Link
          data-testid={`decision-bead-link-${decision.id}`}
          to={beadDetailPath(decision.id)}
          className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground underline decoration-border-strong underline-offset-4 hover:text-foreground hover:decoration-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
        >
          Open Bead detail
        </Link>
      </div>

      {selected && (
        <div
          id={detailId}
          data-testid="decision-detail"
          role="region"
          aria-label={`Decision context for ${decision.id}`}
          className="border-t border-border px-3 py-3 text-xs text-muted-foreground space-y-2"
        >
          <div>
            <span className="font-mono uppercase tracking-wide">Created: </span>
            <Time value={decision.created_at} mode="absolute" precision="minute" />
          </div>
          {decision.description && <p>{decision.description}</p>}
          {decision.due_at && (
            <div data-testid="decision-due-at">
              <span className="font-mono uppercase tracking-wide">Due: </span>
              <Time value={decision.due_at} mode="absolute" precision="minute" />
            </div>
          )}
          {decision.options && (
            <section aria-label="Decision options">
              <div className="font-mono uppercase tracking-wide">Options</div>
              <ol className="mt-1 list-decimal space-y-1 pl-5 text-foreground">
                {decision.options.map((option) => (
                  <li key={option}>{option}</li>
                ))}
              </ol>
            </section>
          )}
          {decision.default && (
            <div>
              <span className="font-mono uppercase tracking-wide">Default: </span>
              <span className="text-foreground">{decision.default}</span>
            </div>
          )}
          {!decision.structured_details_available && (
            <div data-testid="decision-structured-details-unavailable" role="status">
              Structured decision details unavailable: {formatStructuredDetailsReason(
                decision.structured_details_unavailable_reason,
              )}.
            </div>
          )}
          {decision.escalated ? (
            <div>
              Blocking {" "}
              {decision.escalated_blocked_id ? (
                <Link
                  to={beadDetailPath(decision.escalated_blocked_id)}
                  className="font-medium text-foreground underline decoration-border-strong underline-offset-4 hover:decoration-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                >
                  {decision.escalated_blocked_title ?? decision.escalated_blocked_id}
                </Link>
              ) : (
                <span className="font-medium text-foreground">{decision.escalated_blocked_title}</span>
              )}{" "}
              ({decision.escalated_blocked_id}), {blockedKindLabel(decision.escalated_blocked_kind)}, for{" "}
              {formatAgeHours(decision.escalated_block_hours ?? 0)}.
            </div>
          ) : (
            <div className="italic">
              Read-only context: this digest cannot apply a default or close a decision.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function DecisionsPage() {
  const { data, isLoading, isError, error, refetch } = useDecisions();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedBeadId = searchParams.get("bead");
  const decisions = useMemo(() => data?.data ?? [], [data]);
  const decisionsAvailable = data?.meta.decisions_available;
  const exportAsOf = data?.meta.export_as_of;
  const exportAsOfLabel = formatExportAsOf(exportAsOf);
  const exportAsOfIsWarn = resolveExportAsOfIsWarn(exportAsOf);

  const [selectedId, setSelectedId] = useState<string | null>(() => requestedBeadId);

  const ids = useMemo(() => decisions.map((d) => d.id), [decisions]);

  const selectDecision = useCallback(
    (id: string) => {
      setSelectedId(id);
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set("bead", id);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // Keep direct URL navigation authoritative. The initial state mirrors the
  // query so a server-rendered known deep link opens immediately; an unknown
  // id deliberately has no matching row, leaving the normal list usable.
  useEffect(() => {
    setSelectedId(requestedBeadId);
  }, [requestedBeadId]);

  // Pure j/k roving selection -- this read-only summary has no action payload
  // or mutation endpoint. See useListTriage's own doc comment: "Omit or
  // return [] for a list that is j/k-navigable but has no keyboard act."
  const { hints } = useListTriage({
    ids,
    selectedId,
    onSelect: selectDecision,
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
        detail={`${data?.meta.unavailable_reason ?? "beads export unreachable"}: decisions may be missing`}
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
        {/* bu-hmdqz.6: as-of plaque -- the single-file export bind-mount
            tolerates up to 14 days of staleness before decisionsAvailable
            flips to false, so a slowly-aging-but-not-yet-unavailable export
            still needs a visible tell (never render stale data as calm). */}
        {exportAsOfLabel && (
          <div
            data-testid="decisions-export-as-of"
            className={[
              "mt-1 font-mono text-[10px] uppercase tracking-wider",
              exportAsOfIsWarn ? "text-[var(--amber-text)]" : "text-muted-foreground",
            ].join(" ")}
          >
            {exportAsOfLabel}
          </div>
        )}
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
                  onSelect={() => selectDecision(decision.id)}
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

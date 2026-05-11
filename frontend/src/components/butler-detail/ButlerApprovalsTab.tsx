/**
 * ButlerApprovalsTab
 *
 * Full-width scroll panel listing the butler's pending approval actions.
 * Each row contains a severity dot, title (tool_name), sub-line with age,
 * and an action link navigating to the global /approvals page.
 *
 * Severity is derived from how soon the action expires:
 *   high   -- expires within 1 hour (or already expired)
 *   medium -- expires within 24 hours
 *   low    -- no expiry or expires later
 *
 * Filtering: passes butlerName to useApprovalActions for forward compatibility.
 * The backend does not yet filter by butler; server-side scoping is a follow-up.
 *
 * Empty state (per project voice rules -- no em-dashes, sentence case):
 *   "No items pending review."
 *
 * bead: bu-iuol4.18
 */

import { Link } from "react-router"

import { MonoLabel, Panel } from "@/components/butler-detail/atoms"
import { Time } from "@/components/ui/time"
import { useApprovalActions } from "@/hooks/use-approvals"
import type { ApprovalAction } from "@/api/types"

// ---------------------------------------------------------------------------
// Severity helpers
// ---------------------------------------------------------------------------

type Severity = "high" | "medium" | "low"

/**
 * Derive a three-level severity from the action's expiry timestamp.
 *
 *   high   -- expires within 1 h (or already past expiry)
 *   medium -- expires within 24 h
 *   low    -- no expiry, or expiry > 24 h away
 */
function deriveSeverity(action: ApprovalAction): Severity {
  if (!action.expires_at) return "low"
  const msUntilExpiry = new Date(action.expires_at).getTime() - Date.now()
  if (msUntilExpiry <= 60 * 60 * 1_000) return "high"
  if (msUntilExpiry <= 24 * 60 * 60 * 1_000) return "medium"
  return "low"
}

/**
 * Return the Tailwind bg-* utility for the severity dot.
 *
 * Token mapping (no raw oklch/hex):
 *   high   -- bg-destructive      (red/danger)
 *   medium -- bg-amber-500        (amber)
 *   low    -- bg-muted-foreground (dim)
 */
function severityDotClass(severity: Severity): string {
  switch (severity) {
    case "high":
      return "bg-destructive"
    case "medium":
      return "bg-amber-500"
    case "low":
      return "bg-muted-foreground"
  }
}

// ---------------------------------------------------------------------------
// ApprovalRow -- single list item
// ---------------------------------------------------------------------------

interface ApprovalRowProps {
  action: ApprovalAction
}

function ApprovalRow({ action }: ApprovalRowProps) {
  const severity = deriveSeverity(action)
  const dotClass = severityDotClass(severity)

  return (
    <li
      className="flex items-center gap-3 py-2.5 border-b border-border/40 last:border-b-0"
      data-testid="approval-row"
    >
      {/* 8px severity dot */}
      <span
        className={`shrink-0 h-2 w-2 rounded-full ${dotClass}`}
        data-severity={severity}
        aria-label={`${severity} severity`}
        data-testid="severity-dot"
      />

      {/* Title + sub-line */}
      <div className="flex-1 min-w-0">
        <p className="text-sm leading-tight truncate" data-testid="approval-title">
          {action.tool_name}
        </p>
        <div className="flex items-center gap-1.5 mt-0.5">
          <MonoLabel className="text-[10px] truncate max-w-xs">
            {action.agent_summary || action.id.slice(0, 8)}
          </MonoLabel>
          <span className="font-mono text-[10px] opacity-60" aria-hidden>·</span>
          <MonoLabel className="text-[10px] opacity-60">
            <Time value={action.requested_at} mode="relative-compact" />
          </MonoLabel>
        </div>
      </div>

      {/* Action link -- navigate to the global approvals page */}
      <Link
        to="/approvals"
        className="shrink-0 text-xs text-primary hover:underline"
        data-testid="approval-action-link"
        aria-label={`Review approval for ${action.tool_name}`}
      >
        Review
      </Link>
    </li>
  )
}

// ---------------------------------------------------------------------------
// ButlerApprovalsTab -- entry point
// ---------------------------------------------------------------------------

export interface ButlerApprovalsTabProps {
  butlerName: string
}

export default function ButlerApprovalsTab({ butlerName }: ButlerApprovalsTabProps) {
  // NOTE: the backend GET /approvals/actions endpoint does not yet accept a
  // `butler` query param — it aggregates across all pools and the response does
  // not include a butler field. The `butler` param is passed for forward
  // compatibility; actual server-side scoping is tracked in a follow-up bead.
  const { data, isLoading, error } = useApprovalActions({ status: "pending", butler: butlerName })

  const actions = data?.data ?? []
  const meta = data?.meta

  return (
    <div data-testid="butler-approvals-tab">
      <Panel
        title="Pending approvals"
        span={4}
        scroll
        height="calc(100vh - 18rem)"
        className="border-r-0"
      >
        {isLoading ? (
          <p
            className="text-sm text-muted-foreground"
            data-testid="approvals-loading"
          >
            Loading…
          </p>
        ) : error ? (
          <p
            className="text-sm text-destructive"
            data-testid="approvals-error"
          >
            {error instanceof Error ? error.message : "Failed to load approvals."}
          </p>
        ) : actions.length === 0 ? (
          <p
            className="text-sm text-muted-foreground"
            data-testid="approvals-empty"
          >
            No items pending review.
          </p>
        ) : (
          <>
            <ul data-testid="approvals-list">
              {actions.map((action) => (
                <ApprovalRow key={action.id} action={action} />
              ))}
            </ul>
            {meta?.has_more && (
              <div
                className="mt-2 text-xs text-muted-foreground tnum"
                data-testid="approvals-has-more"
              >
                Showing first{" "}
                <span>{actions.length}</span> of{" "}
                <span>{meta.total}</span>.{" "}
                <Link to="/approvals" className="underline" data-testid="approvals-view-all-link">
                  View all approvals →
                </Link>
              </div>
            )}
          </>
        )}
      </Panel>
    </div>
  )
}

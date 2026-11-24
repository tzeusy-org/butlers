/**
 * Typed review of an external MCP fact proposal (REQ-dashboard-approvals-904).
 *
 * Shown in place of the generic stored-call preview. It names both entities by
 * name, type and ID, the predicate and the proposing client, and states the
 * fact's third-party authority. The note and evidence URL are outside-authored
 * text: they render as plain text only, never as a link, image or preview, so
 * opening the review cannot navigate to or fetch the URL. There is no raw
 * tool-argument dump and no standing-rule control here.
 */

import type { ExternalProposalEntity, ExternalProposalReview } from "@/api/types";

/** Element id of the reason Approve is unavailable; the Approve button references it. */
export const EXTERNAL_REVIEW_REASON_ID = "external-proposal-approve-unavailable";

const STATUS_TEXT: Record<ExternalProposalEntity["status"], string> = {
  live: "",
  missing: "no longer exists",
  merged: "merged into another entity",
  deleted: "deleted",
};

const LABEL = "text-[10px] font-mono uppercase tracking-wider text-muted-foreground";

function EntityValue({ entity }: { entity?: ExternalProposalEntity | null }) {
  if (!entity) return <span className="text-muted-foreground">Not shown</span>;
  const gone = entity.status !== "live";
  return (
    <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 min-w-0">
      <span className="font-medium text-foreground break-words">
        {entity.name || "Unknown entity"}
      </span>
      <span className="font-mono text-[11px] text-muted-foreground">
        {entity.entity_type ?? "unknown type"}
      </span>
      <code className="font-mono text-[11px] text-muted-foreground break-all">{entity.id}</code>
      {gone && (
        <span className="font-mono text-[11px] font-medium text-[var(--red-text)]">
          {STATUS_TEXT[entity.status]}
        </span>
      )}
    </span>
  );
}

export function ExternalProposalReviewPanel({
  review,
  pending,
}: {
  review: ExternalProposalReview;
  /** Only a pending proposal states why Approve is unavailable. */
  pending: boolean;
}) {
  const reason = pending && !review.displayed_call ? review.approve_unavailable_reason : null;
  return (
    <section
      // A named region in reading order before the decision controls: screen
      // readers reach every field, and Approve carries the unavailable reason
      // as its description.
      aria-labelledby="external-proposal-review-heading"
      className="mb-6"
      data-testid="external-proposal-review"
    >
      <h3 id="external-proposal-review-heading" className="font-medium">
        External proposal review
      </h3>
      <p className="mt-1 text-sm text-foreground" data-testid="external-proposal-authority">
        {review.authority_statement}
      </p>
      {reason && (
        <p
          id={EXTERNAL_REVIEW_REASON_ID}
          data-testid="external-proposal-unavailable"
          className="mt-2 flex items-start gap-2 rounded-sm border border-[var(--red)]/40 bg-[var(--red)]/10 px-3 py-2 text-xs text-[var(--red-text)]"
        >
          <span className="font-medium">Approve unavailable:</span>
          <span className="break-words">{reason}</span>
        </p>
      )}
      <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
        <dt className={LABEL}>External AI client</dt>
        <dd className="font-mono text-xs break-all">
          {review.client_label ?? <span className="text-muted-foreground">Not recorded</span>}
        </dd>
        <dt className={LABEL}>Subject</dt>
        <dd>
          <EntityValue entity={review.subject} />
        </dd>
        <dt className={LABEL}>Predicate</dt>
        <dd className="font-mono text-xs">
          {review.predicate ?? <span className="text-muted-foreground">Not shown</span>}
        </dd>
        <dt className={LABEL}>Object</dt>
        <dd>
          <EntityValue entity={review.object} />
        </dd>
        <dt className={LABEL}>Note</dt>
        <dd className="whitespace-pre-wrap break-words" data-testid="external-proposal-note">
          {review.note ?? <span className="text-muted-foreground">None</span>}
        </dd>
        <dt className={LABEL}>Evidence URL (not opened)</dt>
        <dd>
          {review.evidence_url ? (
            <code
              translate="no"
              className="font-mono text-xs break-all select-text"
              data-testid="external-proposal-evidence-url"
            >
              {review.evidence_url}
            </code>
          ) : (
            <span className="text-muted-foreground">None</span>
          )}
        </dd>
      </dl>
    </section>
  );
}

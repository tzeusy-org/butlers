import { ApiError } from "@/api/client";
import { useQaCaseProposal } from "@/hooks/use-qa";
import { cn } from "@/lib/utils";

interface ProposalExportPanelProps {
  caseId: string;
  className?: string;
}

const short = (sha: string) => sha.slice(0, 12);

/** What the owner is told for each way the retained patch can be unavailable. */
function unavailableMessage(error: unknown): { text: string; alert: boolean } {
  const status = error instanceof ApiError ? error.status : null;
  if (status === 410) {
    return { text: "The retained patch was pruned and can no longer be exported.", alert: false };
  }
  if (status === 409) {
    return { text: "The retained patch failed its integrity check. Do not adopt it.", alert: true };
  }
  if (status === 503) {
    return { text: "The retained patch could not be read. Try again.", alert: false };
  }
  if (status === 404) {
    return { text: "No retained patch is available for this case.", alert: false };
  }
  return { text: "The retained patch is not available for export.", alert: false };
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

/**
 * The retained, validated patch an owner can publish under their own identity. It shows exactly
 * what would be pushed (bound branch, commits, digest, paths); it carries no credential and
 * performs no publication.
 */
export function ProposalExportPanel({ caseId, className }: ProposalExportPanelProps) {
  const query = useQaCaseProposal(caseId, true);

  if (query.isLoading) return null;
  const proposal = query.data?.data;
  if (query.isError || !proposal) {
    const { text, alert } = unavailableMessage(query.error);
    return (
      <p
        className={cn(
          "text-[11px]",
          alert ? "text-destructive" : "text-muted-foreground",
          className,
        )}
        role={alert ? "alert" : undefined}
        data-testid="qa-proposal-export-unavailable"
      >
        {text}
      </p>
    );
  }

  return (
    <div
      className={cn("space-y-2 border border-border px-3 py-2", className)}
      data-testid="qa-proposal-export"
    >
      <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        Retained patch · not published
      </p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-[11px] tnum">
        <dt className="text-muted-foreground">Branch</dt>
        <dd className="min-w-0 break-all text-foreground">{proposal.branch_name}</dd>
        <dt className="text-muted-foreground">Base</dt>
        <dd className="text-foreground">{short(proposal.base_sha)}</dd>
        <dt className="text-muted-foreground">Head</dt>
        <dd className="text-foreground">
          {short(proposal.head_sha)} ({proposal.commits.length} commit
          {proposal.commits.length === 1 ? "" : "s"})
        </dd>
        <dt className="text-muted-foreground">Digest</dt>
        <dd className="min-w-0 break-all text-foreground" data-testid="qa-proposal-export-digest">
          {proposal.digest}
        </dd>
        <dt className="text-muted-foreground">Size</dt>
        <dd className="text-foreground">{formatBytes(proposal.bundle_bytes)}</dd>
      </dl>
      {proposal.paths.length > 0 ? (
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          Changed paths
        </p>
      ) : null}
      {proposal.paths.length > 0 ? (
        <ul className="space-y-0.5 font-mono text-[11px] text-foreground">
          {proposal.paths.map((path) => (
            <li key={path} className="break-all">
              {path}
            </li>
          ))}
          {proposal.diff_truncated ? (
            <li className="text-muted-foreground">More changed files are not listed.</li>
          ) : null}
        </ul>
      ) : null}
      {proposal.sealed_before_isolation ? (
        <p
          className="text-[11px] text-[var(--amber-text)]"
          data-testid="qa-proposal-export-pre-isolation"
        >
          Sealed before investigator isolation: it needs your explicit approval before it is
          pushed.
        </p>
      ) : null}
    </div>
  );
}

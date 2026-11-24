import { useQaCaseProposal } from "@/hooks/use-qa";
import { cn } from "@/lib/utils";

interface ProposalExportPanelProps {
  caseId: string;
  className?: string;
}

const short = (sha: string) => sha.slice(0, 12);

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
    return (
      <p
        className={cn("text-[11px] text-muted-foreground", className)}
        data-testid="qa-proposal-export-unavailable"
      >
        The retained patch is no longer available for export.
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

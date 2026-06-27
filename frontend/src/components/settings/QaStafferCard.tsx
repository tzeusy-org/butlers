/**
 * QaStafferCard — the QA Staffer settings surface on /settings.
 *
 * Spec: openspec/specs/qa-dashboard/spec.md :: "QA Settings Surface".
 * Renders the operator-managed configuration QA investigations need to clone,
 * commit, and open PRs:
 *   - repository configuration (repo URL + clone/sync status)
 *   - GitHub token status (BUTLERS_QA_GH_TOKEN presence)
 *   - git author identity status (BUTLERS_QA_GIT_AUTHOR_NAME / _EMAIL presence)
 *   - the allowed-repositories whitelist
 *
 * Wired entirely to the existing use-qa hooks — this component adds no new
 * data fetching. The git author identity STATUS comes from
 * GET /api/qa/summary's credentials_status block; the identity itself is
 * EDITABLE here via PUT /api/qa/settings/git-author (useUpdateQaGitAuthor),
 * which stores BUTLERS_QA_GIT_AUTHOR_NAME / _EMAIL in the shared secrets backend
 * that the QA staffer reads at dispatch time. The GitHub token remains
 * status-only (no write endpoint).
 *
 * Design language: Dispatch — mono eyebrows, hairline rules, no drop shadows.
 *
 * bu-r5bnn, bu-481kf
 */

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import {
  useAddQaAllowedRepo,
  useDeleteQaAllowedRepo,
  usePatchQaAllowedRepo,
  useQaAllowedRepos,
  useQaRepoConfig,
  useQaSummary,
  useSyncQaRepo,
  useUpdateQaGitAuthor,
  useUpdateQaRepoConfig,
} from "@/hooks/use-qa";
import { resolveQaRepoUrlInputValue } from "@/components/settings/qa-settings-state";

// ---------------------------------------------------------------------------
// Small atoms (Dispatch vocabulary)
// ---------------------------------------------------------------------------

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground leading-none">
      {children}
    </p>
  );
}

/** A {dot + label} status line: green when present, red when missing, dim when unknown. */
function StatusLine({
  label,
  state,
  detail,
}: {
  label: string;
  /** present | missing | unknown */
  state: "present" | "missing" | "unknown";
  detail?: string;
}) {
  const tone =
    state === "present"
      ? "var(--green)"
      : state === "missing"
        ? "var(--red)"
        : "var(--muted-foreground)";
  const word = state === "present" ? "present" : state === "missing" ? "missing" : "unknown";
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <div className="flex items-center gap-2 min-w-0">
        <span
          className="h-1.5 w-1.5 rounded-full shrink-0"
          style={{ backgroundColor: tone }}
          aria-hidden
        />
        <span className="text-sm truncate">{label}</span>
      </div>
      <span
        className="font-mono text-[11px] uppercase tracking-wider tabular-nums shrink-0"
        style={{ color: tone }}
        aria-label={`${label}: ${word}`}
      >
        {detail ?? word}
      </span>
    </div>
  );
}

function boolToState(v: boolean | null | undefined): "present" | "missing" | "unknown" {
  if (v === true) return "present";
  if (v === false) return "missing";
  return "unknown";
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

export default function QaStafferCard() {
  const summary = useQaSummary();
  const repoConfig = useQaRepoConfig();
  const allowedRepos = useQaAllowedRepos();

  const updateRepo = useUpdateQaRepoConfig();
  const syncRepo = useSyncQaRepo();
  const updateGitAuthor = useUpdateQaGitAuthor();
  const addRepo = useAddQaAllowedRepo();
  const patchRepo = usePatchQaAllowedRepo();
  const deleteRepo = useDeleteQaAllowedRepo();

  const [repoDraft, setRepoDraft] = useState<string | null>(null);
  const [repoDirty, setRepoDirty] = useState(false);
  const [newRepo, setNewRepo] = useState("");
  const [authorName, setAuthorName] = useState("");
  const [authorEmail, setAuthorEmail] = useState("");

  const creds = summary.data?.data?.credentials_status;
  const ghTokenState = boolToState(creds?.gh_token_present);
  const authorNameState = boolToState(creds?.git_author_name_present);
  const authorEmailState = boolToState(creds?.git_author_email_present);

  const repo = repoConfig.data?.data;
  const repos = allowedRepos.data?.data ?? [];

  // Configuration badge per spec: "Configured" only when repo settings exist,
  // the GH token is present, and BOTH git author identity fields are present.
  const isConfigured =
    !!repo?.repo_url &&
    creds?.gh_token_present === true &&
    creds?.git_author_name_present === true &&
    creds?.git_author_email_present === true;

  const repoUrlValue = resolveQaRepoUrlInputValue({
    draft: repoDraft,
    isDirty: repoDirty,
    repoConfig: repo,
  });

  function handleSaveRepo() {
    const trimmed = repoUrlValue.trim();
    if (!trimmed || trimmed === repo?.repo_url) return;
    updateRepo.mutate(
      { repo_url: trimmed },
      {
        onSuccess: () => {
          setRepoDraft(null);
          setRepoDirty(false);
        },
      },
    );
  }

  // Mirror the backend validation (qa.py update_git_author): the email must
  // contain "@" and may not start or end with it, so a malformed value disables
  // Save instead of triggering an unexpected 422.
  const trimmedEmail = authorEmail.trim();
  const emailValid =
    trimmedEmail.includes("@") && !trimmedEmail.startsWith("@") && !trimmedEmail.endsWith("@");
  const canSaveAuthor =
    authorName.trim().length > 0 && emailValid && !updateGitAuthor.isPending;

  function handleSaveAuthor() {
    if (!canSaveAuthor) return;
    updateGitAuthor.mutate(
      { name: authorName.trim(), email: authorEmail.trim() },
      {
        onSuccess: () => {
          setAuthorName("");
          setAuthorEmail("");
        },
      },
    );
  }

  function handleAddRepo() {
    const trimmed = newRepo.trim();
    if (!trimmed) return;
    addRepo.mutate(
      { owner_repo: trimmed },
      { onSuccess: () => setNewRepo("") },
    );
  }

  return (
    <section
      className="flex flex-col gap-5 border border-border/60 p-5"
      aria-label="QA Staffer settings"
    >
      {/* Header --------------------------------------------------------- */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Eyebrow>system · qa staffer</Eyebrow>
          <h2 className="text-base font-medium tracking-tight">QA Staffer</h2>
          <p className="text-xs text-muted-foreground leading-relaxed">
            Repository, credentials, and commit identity the QA staffer uses to clone, commit,
            and open PRs.
          </p>
        </div>
        {summary.isLoading ? (
          <Skeleton className="h-5 w-20" />
        ) : (
          <Badge
            variant={isConfigured ? "outline" : "destructive"}
            aria-label={isConfigured ? "QA staffer configured" : "QA staffer needs setup"}
          >
            {isConfigured ? "Configured" : "Needs setup"}
          </Badge>
        )}
      </div>

      {/* Repository configuration --------------------------------------- */}
      <div className="flex flex-col gap-2">
        <Eyebrow>repository</Eyebrow>
        {repoConfig.isLoading ? (
          <Skeleton className="h-9 w-full" />
        ) : repoConfig.isError ? (
          <p className="text-sm text-muted-foreground">
            Could not load repository configuration.{" "}
            <button
              type="button"
              onClick={() => repoConfig.refetch()}
              className="font-mono text-[11px] uppercase tracking-wider underline underline-offset-2 hover:text-foreground transition-colors cursor-pointer"
            >
              Retry →
            </button>
          </p>
        ) : (
          <>
            <div className="flex gap-2">
              <Input
                aria-label="QA repository URL"
                placeholder="https://github.com/owner/repo"
                value={repoUrlValue}
                disabled={updateRepo.isPending}
                onChange={(e) => {
                  setRepoDraft(e.target.value);
                  setRepoDirty(true);
                }}
              />
              <Button
                variant="outline"
                onClick={handleSaveRepo}
                disabled={
                  updateRepo.isPending ||
                  !repoUrlValue.trim() ||
                  repoUrlValue.trim() === repo?.repo_url
                }
              >
                {updateRepo.isPending ? "Saving…" : "Save"}
              </Button>
              <Button
                variant="ghost"
                onClick={() => syncRepo.mutate()}
                disabled={syncRepo.isPending || !repo?.repo_url}
                aria-label="Sync QA repository"
              >
                {syncRepo.isPending ? "Syncing…" : "Sync"}
              </Button>
            </div>
            <p className="font-mono text-[11px] text-muted-foreground tabular-nums">
              {repo?.clone_path ? `clone · ${repo.clone_path}` : "clone · not yet synced"}
              {repo?.last_synced_at ? ` · synced ${repo.last_synced_at}` : ""}
            </p>
            {repo?.last_sync_error ? (
              <p className="font-mono text-[11px] text-[var(--red)]">
                sync error · {repo.last_sync_error}
              </p>
            ) : null}
          </>
        )}
      </div>

      {/* Credentials: GH token + git author identity -------------------- */}
      <div className="flex flex-col gap-1 border-t border-border/60 pt-3">
        <Eyebrow>credentials</Eyebrow>
        {summary.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : (
          <>
            <div className="flex flex-col divide-y divide-border/40">
              <StatusLine label="GitHub token · BUTLERS_QA_GH_TOKEN" state={ghTokenState} />
              <StatusLine
                label="Git author name · BUTLERS_QA_GIT_AUTHOR_NAME"
                state={authorNameState}
              />
              <StatusLine
                label="Git author email · BUTLERS_QA_GIT_AUTHOR_EMAIL"
                state={authorEmailState}
              />
            </div>

            {/* Editable git author identity ----------------------------- */}
            <div className="flex flex-col gap-2 mt-3">
              <Eyebrow>edit commit identity</Eyebrow>
              <div className="flex gap-2">
                <Input
                  aria-label="Git author name"
                  placeholder={authorNameState === "present" ? "Name set · enter to replace" : "QA Staffer"}
                  value={authorName}
                  disabled={updateGitAuthor.isPending}
                  onChange={(e) => setAuthorName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleSaveAuthor();
                  }}
                />
                <Input
                  aria-label="Git author email"
                  type="email"
                  placeholder={authorEmailState === "present" ? "Email set · enter to replace" : "qa@example.com"}
                  value={authorEmail}
                  disabled={updateGitAuthor.isPending}
                  onChange={(e) => setAuthorEmail(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleSaveAuthor();
                  }}
                />
                <Button
                  variant="outline"
                  onClick={handleSaveAuthor}
                  disabled={!canSaveAuthor}
                  aria-label="Save git author identity"
                >
                  {updateGitAuthor.isPending ? "Saving…" : "Save"}
                </Button>
              </div>
              {updateGitAuthor.isError ? (
                <p className="font-mono text-[11px] text-[var(--red)]">
                  Could not save commit identity. Try again.
                </p>
              ) : null}
            </div>
          </>
        )}
        {creds?.provisioning_hint ? (
          <p className="font-mono text-[11px] text-muted-foreground mt-1">
            {creds.provisioning_hint}
          </p>
        ) : null}
        <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
          The git author identity is stored in the secrets backend and used to author QA
          investigation commits. The GitHub token is provisioned through the secrets store.
        </p>
      </div>

      {/* Allowed repositories whitelist --------------------------------- */}
      <div className="flex flex-col gap-2 border-t border-border/60 pt-3">
        <Eyebrow>allowed repositories</Eyebrow>
        {allowedRepos.isLoading ? (
          <Skeleton className="h-12 w-full" />
        ) : allowedRepos.isError ? (
          <p className="text-sm text-muted-foreground">
            Could not load the allowed-repositories whitelist.{" "}
            <button
              type="button"
              onClick={() => allowedRepos.refetch()}
              className="font-mono text-[11px] uppercase tracking-wider underline underline-offset-2 hover:text-foreground transition-colors cursor-pointer"
            >
              Retry →
            </button>
          </p>
        ) : repos.length === 0 ? (
          <p className="font-serif italic text-sm text-muted-foreground">
            No repositories whitelisted.
          </p>
        ) : (
          <ul className="flex flex-col divide-y divide-border/40">
            {repos.map((r) => (
              <li
                key={r.id}
                className="flex items-center justify-between gap-3 py-2"
                data-testid={`qa-allowed-repo-${r.owner}-${r.repo}`}
              >
                <span
                  className={cn(
                    "font-mono text-sm truncate",
                    r.enabled ? "text-foreground" : "text-muted-foreground",
                  )}
                >
                  {r.owner}/{r.repo}
                </span>
                <div className="flex items-center gap-3 shrink-0">
                  <Switch
                    checked={r.enabled}
                    disabled={patchRepo.isPending || deleteRepo.isPending}
                    onCheckedChange={(enabled) =>
                      patchRepo.mutate({ owner: r.owner, repo: r.repo, enabled })
                    }
                    aria-label={`Toggle ${r.owner}/${r.repo}`}
                  />
                  <button
                    type="button"
                    disabled={deleteRepo.isPending || patchRepo.isPending}
                    onClick={() => deleteRepo.mutate({ owner: r.owner, repo: r.repo })}
                    className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground underline underline-offset-2 hover:text-[var(--red)] transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                    aria-label={`Remove ${r.owner}/${r.repo}`}
                  >
                    Remove
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
        <div className="flex gap-2 mt-1">
          <Input
            aria-label="Add allowed repository"
            placeholder="owner/repo"
            value={newRepo}
            onChange={(e) => setNewRepo(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleAddRepo();
            }}
          />
          <Button
            variant="outline"
            onClick={handleAddRepo}
            disabled={addRepo.isPending || !newRepo.trim()}
          >
            {addRepo.isPending ? "Adding…" : "Add"}
          </Button>
        </div>
      </div>
    </section>
  );
}

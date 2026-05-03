/**
 * QASettingsCard — QA Staffer configuration card for the settings page.
 *
 * Four sections:
 * 1. Repository URL — the repo QA clones for investigations
 * 2. GitHub Token — BUTLERS_QA_GH_TOKEN credential
 * 3. Git author identity — commit author name/email for QA-generated commits
 * 4. Allowed Repositories — whitelist for PR creation
 */

import { useState } from "react";
import { Time } from "@/components/ui/time";

import {
  Eye,
  EyeOff,
  Loader2,
  Plus,
  Search,
  ToggleLeft,
  ToggleRight,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { getSecretMeta, upsertSecret } from "@/api/index.ts";
import type { QaAllowedRepo, SecretUpsertRequest } from "@/api/index.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { resolveQaRepoUrlInputValue } from "@/components/settings/qa-settings-state";
import {
  useAddQaAllowedRepo,
  useDeleteQaAllowedRepo,
  usePatchQaAllowedRepo,
  useQaAllowedRepos,
  useQaRepoConfig,
  useSyncQaRepo,
  useUpdateQaRepoConfig,
} from "@/hooks/use-qa";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SHARED_TARGET = "shared";
const GH_TOKEN_KEY = "BUTLERS_QA_GH_TOKEN";
const GIT_AUTHOR_NAME_KEY = "BUTLERS_QA_GIT_AUTHOR_NAME";
const GIT_AUTHOR_EMAIL_KEY = "BUTLERS_QA_GIT_AUTHOR_EMAIL";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function QASettingsCard() {
  // --- Repo config ---
  const repoQuery = useQaRepoConfig();
  const repoConfig = repoQuery.data?.data;
  const updateRepo = useUpdateQaRepoConfig();
  const syncRepo = useSyncQaRepo();

  const [repoUrl, setRepoUrl] = useState<string | null>(null);
  const [repoUrlDirty, setRepoUrlDirty] = useState(false);
  const repoUrlValue = resolveQaRepoUrlInputValue({
    draft: repoUrl,
    isDirty: repoUrlDirty,
    repoConfig,
  });

  // --- GH Token ---
  const queryClient = useQueryClient();
  const tokenQuery = useQuery({
    queryKey: ["qa-gh-token-meta"],
    queryFn: () => getSecretMeta(SHARED_TARGET, GH_TOKEN_KEY),
    staleTime: 60_000,
    retry: false,
  });
  const tokenIsSet = tokenQuery.data?.data?.is_set === true;

  const saveTokenMutation = useMutation({
    mutationFn: (value: string) =>
      upsertSecret(SHARED_TARGET, GH_TOKEN_KEY, {
        value,
        category: "qa",
        is_sensitive: true,
        description: "GitHub token for QA investigations",
      } as SecretUpsertRequest),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["qa-gh-token-meta"] });
    },
  });

  const [ghToken, setGhToken] = useState("");
  const [showToken, setShowToken] = useState(false);

  // --- Git author identity ---
  const authorNameQuery = useQuery({
    queryKey: ["qa-git-author-name-meta"],
    queryFn: () => getSecretMeta(SHARED_TARGET, GIT_AUTHOR_NAME_KEY),
    staleTime: 60_000,
    retry: false,
  });
  const authorEmailQuery = useQuery({
    queryKey: ["qa-git-author-email-meta"],
    queryFn: () => getSecretMeta(SHARED_TARGET, GIT_AUTHOR_EMAIL_KEY),
    staleTime: 60_000,
    retry: false,
  });
  const authorNameIsSet = authorNameQuery.data?.data?.is_set === true;
  const authorEmailIsSet = authorEmailQuery.data?.data?.is_set === true;

  const saveIdentityMutation = useMutation({
    mutationFn: async ({ name, email }: { name: string; email: string }) => {
      const writes = [];
      if (name.trim()) {
        writes.push(
          upsertSecret(SHARED_TARGET, GIT_AUTHOR_NAME_KEY, {
            value: name.trim(),
            category: "qa",
            is_sensitive: false,
            description: "Git author name for QA-generated commits",
          } as SecretUpsertRequest),
        );
      }
      if (email.trim()) {
        writes.push(
          upsertSecret(SHARED_TARGET, GIT_AUTHOR_EMAIL_KEY, {
            value: email.trim(),
            category: "qa",
            is_sensitive: false,
            description: "Git author email for QA-generated commits",
          } as SecretUpsertRequest),
        );
      }
      await Promise.all(writes);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["qa-git-author-name-meta"] });
      queryClient.invalidateQueries({ queryKey: ["qa-git-author-email-meta"] });
    },
  });

  const [gitAuthorName, setGitAuthorName] = useState("");
  const [gitAuthorEmail, setGitAuthorEmail] = useState("");

  // --- Allowed repos ---
  const allowedReposQuery = useQaAllowedRepos();
  const allowedRepos: QaAllowedRepo[] = allowedReposQuery.data?.data ?? [];
  const addRepo = useAddQaAllowedRepo();
  const patchRepo = usePatchQaAllowedRepo();
  const deleteRepo = useDeleteQaAllowedRepo();

  const [newRepoInput, setNewRepoInput] = useState("");

  // --- Handlers ---

  async function handleSaveRepoUrl() {
    try {
      const nextRepoUrl = repoUrlValue.trim();
      await updateRepo.mutateAsync({ repo_url: nextRepoUrl });
      toast.success("Repository URL updated");
      setRepoUrl(nextRepoUrl);
      setRepoUrlDirty(false);
    } catch (err) {
      toast.error(`Failed to save: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  }

  async function handleSyncRepo() {
    try {
      const resp = await syncRepo.mutateAsync();
      if (resp.data.synced) {
        toast.success("Repository synced");
      } else {
        toast.error(`Sync failed: ${resp.data.error}`);
      }
    } catch (err) {
      toast.error(`Sync failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  }

  async function handleSaveToken() {
    if (!ghToken.trim()) return;
    try {
      await saveTokenMutation.mutateAsync(ghToken.trim());
      toast.success("GitHub token saved");
      setGhToken("");
    } catch (err) {
      toast.error(`Failed to save: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  }

  async function handleSaveIdentity() {
    if (!gitAuthorName.trim() && !gitAuthorEmail.trim()) return;
    try {
      await saveIdentityMutation.mutateAsync({
        name: gitAuthorName,
        email: gitAuthorEmail,
      });
      toast.success("Git author identity saved");
      setGitAuthorName("");
      setGitAuthorEmail("");
    } catch (err) {
      toast.error(`Failed to save: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  }

  async function handleAddRepo() {
    const val = newRepoInput.trim();
    if (!val) return;
    try {
      await addRepo.mutateAsync({ owner_repo: val });
      toast.success(`Added ${val}`);
      setNewRepoInput("");
    } catch (err) {
      toast.error(`Failed to add: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  }

  // --- Badge ---
  const hasGitIdentity = authorNameIsSet && authorEmailIsSet;
  const isConfigured = repoConfig && tokenIsSet && hasGitIdentity;
  const badgeInfo = isConfigured
    ? { variant: "default" as const, label: "Configured" }
    : { variant: "outline" as const, label: "Setup required" };

  // --- Loading ---
  if (repoQuery.isLoading && tokenQuery.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Search className="h-5 w-5" />
            QA Staffer
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-48 w-full" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Search className="h-5 w-5" />
              QA Staffer
            </CardTitle>
            <CardDescription className="mt-1">
              Repository, credentials, and PR whitelist for automated QA investigations.
            </CardDescription>
          </div>
          <Badge variant={badgeInfo.variant}>{badgeInfo.label}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* ---- Section 1: Repository URL ---- */}
        <div className="space-y-3">
          <h4 className="text-sm font-medium">Repository</h4>
          <div className="space-y-1.5">
            <Label htmlFor="qa-repo-url">Repository URL</Label>
            <Input
              id="qa-repo-url"
              value={repoUrlValue}
              onChange={(e) => {
                setRepoUrl(e.target.value);
                setRepoUrlDirty(true);
              }}
              placeholder="https://github.com/owner/repo"
              disabled={updateRepo.isPending}
            />
            {repoConfig?.last_synced_at && (
              <p className="text-xs text-muted-foreground">
                Last synced: <Time value={repoConfig.last_synced_at} mode="absolute" />
              </p>
            )}
            {repoConfig?.last_sync_error && (
              <p className="text-xs text-destructive">
                Sync error: {repoConfig.last_sync_error}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              onClick={handleSaveRepoUrl}
              disabled={updateRepo.isPending || !repoUrlDirty}
            >
              {updateRepo.isPending ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
                  Saving...
                </>
              ) : (
                "Save"
              )}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleSyncRepo}
              disabled={syncRepo.isPending}
            >
              {syncRepo.isPending ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
                  Syncing...
                </>
              ) : (
                "Sync Now"
              )}
            </Button>
          </div>
        </div>

        <hr className="border-border" />

        {/* ---- Section 2: GitHub Token ---- */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <h4 className="text-sm font-medium">GitHub Token</h4>
            {tokenQuery.isSuccess && (
              <Badge variant={tokenIsSet ? "default" : "destructive"} className="text-xs">
                {tokenIsSet ? "Set" : "Missing"}
              </Badge>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="qa-gh-token">BUTLERS_QA_GH_TOKEN</Label>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Input
                  id="qa-gh-token"
                  type={showToken ? "text" : "password"}
                  value={ghToken}
                  onChange={(e) => setGhToken(e.target.value)}
                  placeholder={tokenIsSet ? "********** (saved)" : "github_pat_..."}
                  autoComplete="off"
                  disabled={saveTokenMutation.isPending}
                />
                <button
                  type="button"
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  onClick={() => setShowToken(!showToken)}
                  tabIndex={-1}
                >
                  {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              GitHub personal access token used for investigation PRs.
              Needs <code className="text-xs">repo</code> scope.
            </p>
          </div>
          <Button
            size="sm"
            onClick={handleSaveToken}
            disabled={saveTokenMutation.isPending || !ghToken.trim()}
          >
            {saveTokenMutation.isPending ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
                Saving...
              </>
            ) : (
              "Save Token"
            )}
          </Button>
        </div>

        <hr className="border-border" />

        {/* ---- Section 3: Git author identity ---- */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <h4 className="text-sm font-medium">Git Author Identity</h4>
            {(authorNameQuery.isSuccess || authorEmailQuery.isSuccess) && (
              <Badge variant={hasGitIdentity ? "default" : "destructive"} className="text-xs">
                {hasGitIdentity ? "Set" : "Missing"}
              </Badge>
            )}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="qa-git-author-name">Author name</Label>
              <Input
                id="qa-git-author-name"
                value={gitAuthorName}
                onChange={(e) => setGitAuthorName(e.target.value)}
                placeholder={authorNameIsSet ? "(saved)" : "QA Staffer"}
                autoComplete="off"
                disabled={saveIdentityMutation.isPending}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="qa-git-author-email">Author email</Label>
              <Input
                id="qa-git-author-email"
                type="email"
                value={gitAuthorEmail}
                onChange={(e) => setGitAuthorEmail(e.target.value)}
                placeholder={authorEmailIsSet ? "(saved)" : "qa-bot@example.com"}
                autoComplete="off"
                disabled={saveIdentityMutation.isPending}
              />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Used as <code className="text-xs">GIT_AUTHOR_*</code> and{" "}
            <code className="text-xs">GIT_COMMITTER_*</code> for QA-generated commits.
          </p>
          <Button
            size="sm"
            onClick={handleSaveIdentity}
            disabled={
              saveIdentityMutation.isPending ||
              (!gitAuthorName.trim() && !gitAuthorEmail.trim())
            }
          >
            {saveIdentityMutation.isPending ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
                Saving...
              </>
            ) : (
              "Save Identity"
            )}
          </Button>
        </div>

        <hr className="border-border" />

        {/* ---- Section 4: Allowed Repositories ---- */}
        <div className="space-y-3">
          <h4 className="text-sm font-medium">Allowed Repositories</h4>
          <p className="text-xs text-muted-foreground">
            QA can only create PRs in whitelisted repositories. Add repos in{" "}
            <code className="text-xs">owner/repo</code> format or as full GitHub URLs.
          </p>

          {/* Add form */}
          <div className="flex gap-2">
            <Input
              value={newRepoInput}
              onChange={(e) => setNewRepoInput(e.target.value)}
              placeholder="owner/repo or GitHub URL"
              disabled={addRepo.isPending}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleAddRepo();
              }}
            />
            <Button
              size="sm"
              onClick={handleAddRepo}
              disabled={addRepo.isPending || !newRepoInput.trim()}
            >
              {addRepo.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Plus className="h-3.5 w-3.5" />
              )}
            </Button>
          </div>

          {/* Repo list */}
          {allowedReposQuery.isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : allowedRepos.length === 0 ? (
            <p className="text-xs text-muted-foreground italic">
              No repositories whitelisted. QA PR creation is blocked.
            </p>
          ) : (
            <div className="space-y-1">
              {allowedRepos.map((r) => (
                <div
                  key={r.id}
                  className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
                >
                  <div className="flex items-center gap-2">
                    <span className={r.enabled ? "" : "text-muted-foreground line-through"}>
                      {r.owner}/{r.repo}
                    </span>
                    {!r.enabled && (
                      <Badge variant="outline" className="text-xs">
                        disabled
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 w-7 p-0"
                      onClick={() =>
                        patchRepo.mutate(
                          { owner: r.owner, repo: r.repo, enabled: !r.enabled },
                          {
                            onError: (err) =>
                              toast.error(
                                `Failed: ${err instanceof Error ? err.message : "Unknown error"}`,
                              ),
                          },
                        )
                      }
                      title={r.enabled ? "Disable" : "Enable"}
                    >
                      {r.enabled ? (
                        <ToggleRight className="h-4 w-4" />
                      ) : (
                        <ToggleLeft className="h-4 w-4 text-muted-foreground" />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 w-7 p-0 text-destructive hover:text-destructive"
                      onClick={() =>
                        deleteRepo.mutate(
                          { owner: r.owner, repo: r.repo },
                          {
                            onSuccess: () => toast.success(`Removed ${r.owner}/${r.repo}`),
                            onError: (err) =>
                              toast.error(
                                `Failed: ${err instanceof Error ? err.message : "Unknown error"}`,
                              ),
                          },
                        )
                      }
                      title="Remove"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

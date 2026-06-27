/**
 * QaStafferCard — QA Settings Surface (qa-dashboard spec).
 *
 * Verifies the card renders on the settings surface wired to the existing
 * use-qa hooks (no orphaned hooks, no dead onClick):
 *   - repository configuration (URL input + Save/Sync)
 *   - GitHub token status
 *   - git author identity status
 *   - allowed-repositories whitelist (toggle + remove + add)
 *
 * bu-r5bnn
 */

// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, cleanup, screen, fireEvent } from "@testing-library/react";

// ---------------------------------------------------------------------------
// Mock the use-qa hooks (the card adds no new data fetching).
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-qa", () => ({
  useQaSummary: vi.fn(),
  useQaRepoConfig: vi.fn(),
  useQaAllowedRepos: vi.fn(),
  useUpdateQaRepoConfig: vi.fn(),
  useSyncQaRepo: vi.fn(),
  useAddQaAllowedRepo: vi.fn(),
  usePatchQaAllowedRepo: vi.fn(),
  useDeleteQaAllowedRepo: vi.fn(),
}));

import QaStafferCard from "@/components/settings/QaStafferCard";
import {
  useAddQaAllowedRepo,
  useDeleteQaAllowedRepo,
  usePatchQaAllowedRepo,
  useQaAllowedRepos,
  useQaRepoConfig,
  useQaSummary,
  useSyncQaRepo,
  useUpdateQaRepoConfig,
} from "@/hooks/use-qa";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

const updateMutate = vi.fn();
const syncMutate = vi.fn();
const addMutate = vi.fn();
const patchMutate = vi.fn();
const deleteMutate = vi.fn();

function mutation(mutate: ReturnType<typeof vi.fn>) {
  return { mutate, isPending: false };
}

function setup(opts?: {
  ghToken?: boolean | null;
  authorName?: boolean | null;
  authorEmail?: boolean | null;
  repoUrl?: string;
  repos?: Array<{ id: string; owner: string; repo: string; enabled: boolean }>;
}) {
  (useQaSummary as AnyMock).mockReturnValue({
    data: {
      data: {
        credentials_status: {
          gh_token_present: opts?.ghToken ?? true,
          git_author_name_present: opts?.authorName ?? true,
          git_author_email_present: opts?.authorEmail ?? true,
          provisioning_hint: null,
        },
      },
    },
    isLoading: false,
    isError: false,
  });
  (useQaRepoConfig as AnyMock).mockReturnValue({
    data: {
      data: {
        repo_url: opts?.repoUrl ?? "https://github.com/example/repo",
        clone_path: "/srv/qa/clone",
        last_synced_at: "2026-06-27T00:00:00Z",
        last_sync_error: null,
        created_at: "2026-06-01T00:00:00Z",
        updated_at: "2026-06-27T00:00:00Z",
      },
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
  (useQaAllowedRepos as AnyMock).mockReturnValue({
    data: {
      data:
        opts?.repos ??
        [{ id: "r1", owner: "acme", repo: "widgets", enabled: true }],
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
  (useUpdateQaRepoConfig as AnyMock).mockReturnValue(mutation(updateMutate));
  (useSyncQaRepo as AnyMock).mockReturnValue(mutation(syncMutate));
  (useAddQaAllowedRepo as AnyMock).mockReturnValue(mutation(addMutate));
  (usePatchQaAllowedRepo as AnyMock).mockReturnValue(mutation(patchMutate));
  (useDeleteQaAllowedRepo as AnyMock).mockReturnValue(mutation(deleteMutate));

  return render(<QaStafferCard />);
}

describe("QaStafferCard", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });
  afterEach(() => cleanup());

  it("renders repo config, GH token status, and the allowed-repos whitelist", () => {
    setup();
    // Repository configuration
    expect(
      (screen.getByLabelText("QA repository URL") as HTMLInputElement).value,
    ).toBe("https://github.com/example/repo");
    // GitHub token status surfaced from the summary credentials block
    expect(screen.getByLabelText(/GitHub token .*: present/)).toBeTruthy();
    // Git author identity status
    expect(screen.getByLabelText(/Git author name .*: present/)).toBeTruthy();
    expect(screen.getByLabelText(/Git author email .*: present/)).toBeTruthy();
    // Allowed-repositories whitelist row
    expect(screen.getByTestId("qa-allowed-repo-acme-widgets")).toBeTruthy();
  });

  it("shows 'Configured' only when repo + GH token + both author fields present", () => {
    setup();
    expect(screen.getByLabelText("QA staffer configured")).toBeTruthy();
    cleanup();
    setup({ authorEmail: false });
    expect(screen.getByLabelText("QA staffer needs setup")).toBeTruthy();
    expect(screen.getByLabelText(/Git author email .*: missing/)).toBeTruthy();
  });

  it("wires the repo Save mutation (no dead onClick)", () => {
    setup();
    const input = screen.getByLabelText("QA repository URL");
    fireEvent.change(input, { target: { value: "https://github.com/example/next" } });
    fireEvent.click(screen.getByText("Save"));
    expect(updateMutate).toHaveBeenCalledWith(
      { repo_url: "https://github.com/example/next" },
      expect.anything(),
    );
  });

  it("wires the repo Sync mutation", () => {
    setup();
    fireEvent.click(screen.getByLabelText("Sync QA repository"));
    expect(syncMutate).toHaveBeenCalled();
  });

  it("wires whitelist remove, toggle, and add mutations", () => {
    setup();
    fireEvent.click(screen.getByLabelText("Remove acme/widgets"));
    expect(deleteMutate).toHaveBeenCalledWith({ owner: "acme", repo: "widgets" });

    fireEvent.click(screen.getByLabelText("Toggle acme/widgets"));
    expect(patchMutate).toHaveBeenCalled();

    const addInput = screen.getByLabelText("Add allowed repository");
    fireEvent.change(addInput, { target: { value: "foo/bar" } });
    fireEvent.click(screen.getByText("Add"));
    expect(addMutate).toHaveBeenCalledWith({ owner_repo: "foo/bar" }, expect.anything());
  });

  it("renders an empty-state line when no repos are whitelisted", () => {
    setup({ repos: [] });
    expect(screen.getByText("No repositories whitelisted.")).toBeTruthy();
  });
});

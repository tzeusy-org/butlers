import { describe, expect, it } from "vitest";

import type { QaRepoConfig } from "@/api/index.ts";
import { resolveQaRepoUrlInputValue } from "@/components/settings/qa-settings-state";

function makeRepoConfig(overrides: Partial<QaRepoConfig> = {}): QaRepoConfig {
  return {
    repo_url: "https://github.com/example/repo",
    clone_path: null,
    last_synced_at: null,
    last_sync_error: null,
    created_at: "2026-04-10T00:00:00Z",
    updated_at: "2026-04-10T00:00:00Z",
    ...overrides,
  };
}

describe("resolveQaRepoUrlInputValue", () => {
  it("uses the fetched repo URL when there is no local draft", () => {
    expect(
      resolveQaRepoUrlInputValue({
        draft: null,
        isDirty: false,
        repoConfig: makeRepoConfig(),
      }),
    ).toBe("https://github.com/example/repo");
  });

  it("prefers the local draft while the field is dirty", () => {
    expect(
      resolveQaRepoUrlInputValue({
        draft: "https://github.com/example/new-repo",
        isDirty: true,
        repoConfig: makeRepoConfig(),
      }),
    ).toBe("https://github.com/example/new-repo");
  });

  it("keeps an explicitly saved empty value instead of falling back to stale remote data", () => {
    expect(
      resolveQaRepoUrlInputValue({
        draft: "",
        isDirty: false,
        repoConfig: makeRepoConfig(),
      }),
    ).toBe("");
  });
});

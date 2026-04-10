import type { QaRepoConfig } from "@/api/index.ts";

export function resolveQaRepoUrlInputValue({
  draft,
  isDirty,
  repoConfig,
}: {
  draft: string | null;
  isDirty: boolean;
  repoConfig: QaRepoConfig | undefined;
}): string {
  if (isDirty || draft !== null) {
    return draft ?? "";
  }
  return repoConfig?.repo_url ?? "";
}

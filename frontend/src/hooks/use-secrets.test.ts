import { describe, expect, it } from "vitest";

import type { SecretEntry } from "@/api/index.ts";
import { isSharedSecretsTarget, mergeResolvedSecrets } from "@/hooks/use-secrets";

function secret(overrides: Partial<SecretEntry> & Pick<SecretEntry, "key">): SecretEntry {
  return {
    key: overrides.key,
    category: overrides.category ?? "general",
    description: overrides.description ?? null,
    is_sensitive: overrides.is_sensitive ?? true,
    is_set: overrides.is_set ?? true,
    created_at: overrides.created_at ?? "2026-02-20T00:00:00Z",
    updated_at: overrides.updated_at ?? "2026-02-20T00:00:00Z",
    expires_at: overrides.expires_at ?? null,
    source: overrides.source ?? "database",
  };
}

describe("isSharedSecretsTarget", () => {
  it("normalizes case and surrounding whitespace", () => {
    expect(isSharedSecretsTarget("shared")).toBe(true);
    expect(isSharedSecretsTarget(" SHARED ")).toBe(true);
    expect(isSharedSecretsTarget("general")).toBe(false);
  });
});

describe("mergeResolvedSecrets", () => {
  it("keeps local overrides and adds only shared-missing keys", () => {
    const merged = mergeResolvedSecrets(
      [
        secret({
          key: "ANTHROPIC_API_KEY",
          source: "database",
        }),
      ],
      [
        secret({
          key: "anthropic_api_key",
          source: "database",
        }),
        secret({
          key: "BUTLER_EMAIL_PASSWORD",
          source: "database",
        }),
      ],
    );

    expect(merged).toHaveLength(2);
    expect(merged.find((s) => s.key === "ANTHROPIC_API_KEY")?.source).toBe("database");
    expect(merged.find((s) => s.key === "BUTLER_EMAIL_PASSWORD")?.source).toBe("shared");
  });
});

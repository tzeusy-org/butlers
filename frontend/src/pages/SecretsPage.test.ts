import { describe, expect, it } from "vitest";

import { buildSecretsTargets, SHARED_SECRETS_TARGET } from "@/pages/SecretsPage";

describe("buildSecretsTargets", () => {
  it("always includes shared as the first target", () => {
    expect(buildSecretsTargets([])).toEqual([SHARED_SECRETS_TARGET]);
    expect(buildSecretsTargets(["general"])).toEqual([SHARED_SECRETS_TARGET, "general"]);
  });

  it("deduplicates shared from butler-discovery results", () => {
    expect(buildSecretsTargets(["shared", "general", "SHARED", "health"])).toEqual([
      SHARED_SECRETS_TARGET,
      "general",
      "health",
    ]);
  });
});

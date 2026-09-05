import { describe, expect, it } from "vitest";

import { SHELL_CAPABILITIES } from "./shell-capability";
import { PAGE_CONTEXT_REGISTRY } from "./page-context-registry";

const VALID_POLICIES = ["snapshot", "ref-only", "none"];

describe("shell capability manifest — page-context coverage (bu-0ynlk.4)", () => {
  it("gives every capability a registry descriptor and an explicit contextPolicy", () => {
    expect(SHELL_CAPABILITIES.length).toBeGreaterThan(0);
    for (const capability of SHELL_CAPABILITIES) {
      expect(PAGE_CONTEXT_REGISTRY[capability.path], capability.path).toBeDefined();
      expect(capability.contextPolicy, capability.path).toBeDefined();
      expect(VALID_POLICIES, capability.path).toContain(capability.contextPolicy);
      expect(capability.contextPolicy, capability.path).toBe(
        PAGE_CONTEXT_REGISTRY[capability.path]?.policy,
      );
    }
  });

  it("never lets /secrets or /settings/* default to a full snapshot", () => {
    const sensitiveAdjacent = SHELL_CAPABILITIES.filter(
      (capability) => capability.path === "/secrets" || capability.path.startsWith("/settings/"),
    );
    expect(sensitiveAdjacent.length).toBeGreaterThan(0);
    for (const capability of sensitiveAdjacent) {
      expect(capability.contextPolicy, capability.path).not.toBe("snapshot");
    }
  });

  it("treats /secrets as fully suppressed (policy 'none')", () => {
    const secrets = SHELL_CAPABILITIES.find((capability) => capability.path === "/secrets");
    expect(secrets?.contextPolicy).toBe("none");
  });
});

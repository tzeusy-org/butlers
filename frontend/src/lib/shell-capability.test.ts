import { describe, expect, it } from "vitest";

import {
  SHELL_CAPABILITIES,
  getShellCapability,
  type ShellCapability,
} from "./shell-capability";

describe("shell capability manifest", () => {
  it("gives every capability an explicit discoverability policy and lazy loader", () => {
    expect(SHELL_CAPABILITIES.length).toBeGreaterThan(0);
    for (const capability of SHELL_CAPABILITIES) {
      expect(capability.loader, capability.path).toEqual(expect.any(Function));
      expect(capability.discoverability, capability.path).toBeDefined();
      expect(["global", "contextual", "context-only"]).toContain(
        capability.discoverability,
      );
    }
  });

  it("keeps ingestion subroutes globally discoverable with their own chords policy", () => {
    for (const path of ["/ingestion/connectors", "/ingestion/filters"]) {
      const capability = getShellCapability(path);
      expect(capability).toMatchObject({
        path,
        discoverability: "global",
      });
      expect(capability?.loader).toEqual(expect.any(Function));
    }
  });

  it("models dynamic destinations as contextual or search-backed capabilities", () => {
    const dynamic = SHELL_CAPABILITIES.filter((capability) => capability.dynamic);
    expect(dynamic.length).toBeGreaterThan(0);
    for (const capability of dynamic) {
      expect(["contextual", "context-only"]).toContain(capability.discoverability);
    }
  });

  it("is structurally typed as a single projection source", () => {
    const capability: ShellCapability = SHELL_CAPABILITIES[0];
    expect(capability.family).toBeTruthy();
    expect(capability.placement).toBeDefined();
  });
});

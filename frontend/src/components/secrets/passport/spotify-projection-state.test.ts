import { describe, expect, it } from "vitest";

import { isUnverified, needsHand } from "./constants.ts";
import { spotifyProjectionState } from "./spotify-projection-state.ts";

describe("spotifyProjectionState", () => {
  it.each([
    ["connected", "connected", "ok", false],
    ["unconfigured", "unconfigured", "never_set", false],
    ["authorization needed", "authorization_needed", "authorization_needed", true],
    ["needs reauth", "needs_reauth", "authorization_needed", true],
    ["connector error", "error", "failed", true],
  ] as const)("maps %s to its presentation state and group", (_label, state, expected, needsHandGroup) => {
    const presentation = spotifyProjectionState({ isLoading: false, isError: false, state });

    expect(presentation).toBe(expected);
    expect(needsHand(presentation)).toBe(needsHandGroup);
    expect(isUnverified(presentation)).toBe(false);
  });

  it("maps loading to checking outside stale", () => {
    const presentation = spotifyProjectionState({
      isLoading: true,
      isError: false,
      state: undefined,
    });

    expect(presentation).toBe("checking");
    expect(needsHand(presentation)).toBe(false);
    expect(isUnverified(presentation)).toBe(false);
  });

  it.each([
    ["query failure", { isLoading: false, isError: true, state: undefined }],
    ["missing status", { isLoading: false, isError: false, state: undefined }],
  ] as const)("maps %s to failed", (_label, input) => {
    const presentation = spotifyProjectionState(input);

    expect(presentation).toBe("failed");
    expect(needsHand(presentation)).toBe(true);
    expect(isUnverified(presentation)).toBe(false);
  });
});

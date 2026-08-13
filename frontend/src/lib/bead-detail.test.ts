import { describe, expect, it } from "vitest";

import { beadDetailPath } from "./bead-detail";

describe("beadDetailPath", () => {
  it("always constructs a same-origin route from an encoded id", () => {
    expect(beadDetailPath("bu-safe")).toBe("/beads/bu-safe");
    expect(beadDetailPath("https://outside.invalid/issue?x=1")).toBe(
      "/beads/https%3A%2F%2Foutside.invalid%2Fissue%3Fx%3D1",
    );
  });
});

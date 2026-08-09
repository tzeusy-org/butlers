import { describe, expect, it } from "vitest";

import { getAllTabs } from "./butler-detail-tabs";

describe("Messenger tracking retirement", () => {
  it("does not expose the retired Conversations bespoke tab", () => {
    expect(getAllTabs("messenger")).not.toContain("conversations");
  });
});

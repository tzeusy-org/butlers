// The finance and travel tab-registry cases lived beside the components they
// name; they test the console registry (@/pages/butler-detail-tabs), so they
// live here and the domain components import nothing from the console.

import { describe, it, expect } from "vitest";

import { getAllTabs, isValidTab } from "@/pages/butler-detail-tabs";

describe("ButlerDetailPage — finance finances tab in getAllTabs", () => {
  it("finance butler has 'finances' tab", () => {
    expect(getAllTabs("finance")).toContain("finances");
  });

  it("'finances' is a valid tab for finance butler", () => {
    expect(isValidTab("finances", "finance")).toBe(true);
  });

  it("'finances' is NOT a valid tab for non-finance butlers", () => {
    expect(isValidTab("finances", "general")).toBe(false);
    expect(isValidTab("finances", "education")).toBe(false);
  });

  it("non-finance butlers do not include 'finances' tab", () => {
    expect(getAllTabs("general")).not.toContain("finances");
    expect(getAllTabs("education")).not.toContain("finances");
  });
});

describe("ButlerDetailPage — travel trips tab in getAllTabs", () => {
  it("travel butler has 'trips' tab", () => {
    expect(getAllTabs("travel")).toContain("trips");
  });

  it("'trips' is a valid tab for travel butler", () => {
    expect(isValidTab("trips", "travel")).toBe(true);
  });

  it("'trips' is NOT a valid tab for non-travel butlers", () => {
    expect(isValidTab("trips", "general")).toBe(false);
    expect(isValidTab("trips", "health")).toBe(false);
  });

  it("non-travel butlers do not include 'trips' tab", () => {
    expect(getAllTabs("general")).not.toContain("trips");
    expect(getAllTabs("health")).not.toContain("trips");
  });
});

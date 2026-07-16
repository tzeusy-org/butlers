// @vitest-environment jsdom
/**
 * The single command/route registry (bu-86c4c.7).
 *
 * Covers the audit's two concrete failure modes:
 * - deep routes: /entities/circles and the six health sub-pages must remain
 *   indexed, whether they are palette-only or promoted to the sidebar.
 * - chord drift: g-h used to point at /health/measurements (pre-redesign);
 *   chords must be unique and resolvable from the same registry that builds
 *   the sidebar.
 */
import { describe, expect, it } from "vitest";

import { ALL_ROUTES, G_CHORD_ROUTES } from "@/lib/route-registry";

describe("route-registry", () => {
  it("indexes every deep route", () => {
    const paths = ALL_ROUTES.map((r) => r.path);
    for (const deepPath of [
      "/entities/circles",
      "/health/measurements",
      "/health/medications",
      "/health/conditions",
      "/health/symptoms",
      "/health/meals",
      "/health/research",
    ]) {
      expect(paths).toContain(deepPath);
    }
  });

  it("keeps each sidebar-promoted Health ledger route under Health exactly once", () => {
    for (const path of [
      "/health/measurements",
      "/health/medications",
      "/health/conditions",
      "/health/symptoms",
      "/health/meals",
      "/health/research",
    ]) {
      const matches = ALL_ROUTES.filter((route) => route.path === path);
      expect(matches).toHaveLength(1);
      expect(matches[0]).toMatchObject({ section: "Health", butler: "health" });
    }
  });

  it("promotes /spend straight to the sidebar instead of indexing it as an orphan (bu-86c4c.11 merged /costs + /settings/spend into one nav-visible Spend page)", () => {
    const paths = ALL_ROUTES.map((r) => r.path);
    expect(paths).toContain("/spend");
    expect(paths).not.toContain("/costs");
    expect(paths).not.toContain("/settings/spend");
  });

  it("does not index /approvals/rules (bu-86c4c.12 merged it into /approvals as the Autonomy panel and deleted the standalone route)", () => {
    const paths = ALL_ROUTES.map((r) => r.path);
    expect(paths).not.toContain("/approvals/rules");
    expect(paths).toContain("/approvals");
  });

  it("does not index /groups or /qa/investigations (bu-86c4c.19 retired /groups into the Circles lens at /entities/circles and folded /qa/investigations into /qa's own URL-persisted filters)", () => {
    const paths = ALL_ROUTES.map((r) => r.path);
    expect(paths).not.toContain("/groups");
    expect(paths).not.toContain("/qa/investigations");
    expect(paths).toContain("/entities/circles");
    expect(paths).toContain("/qa");
  });

  it("still indexes every sidebar-promoted route (sanity — sidebar routes are a subset)", () => {
    const paths = ALL_ROUTES.map((r) => r.path);
    expect(paths).toContain("/");
    expect(paths).toContain("/butlers");
    expect(paths).toContain("/memory");
  });

  it("fixes the g-h drift: the chord resolves to the actual Health overview page", () => {
    expect(G_CHORD_ROUTES.h).toBe("/health");
    expect(G_CHORD_ROUTES.h).not.toBe("/health/measurements");
  });

  it("has no duplicate chord letters (a chord can only ever mean one destination)", () => {
    const chords = ALL_ROUTES.map((r) => r.chord).filter((c): c is string => !!c);
    const unique = new Set(chords);
    expect(unique.size).toBe(chords.length);
  });

  it("carries every route the original hardcoded g-chord switch statement supported", () => {
    // Historical chord set from use-keyboard-shortcuts.ts before bu-86c4c.7.
    const expectedChordLetters = ["o", "b", "s", "t", "n", "i", "a", "m", "c", "h", "e"];
    for (const letter of expectedChordLetters) {
      expect(G_CHORD_ROUTES[letter]).toBeDefined();
    }
  });
});

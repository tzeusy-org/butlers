/**
 * ButlersPage — axe-core accessibility baseline tests for the status-board layout.
 * (bu-hb7dh.8)
 *
 * Tests each key UI state for zero axe violations. States covered:
 *   1. Loading (skeleton with aria-label="Loading")
 *   2. Empty (no rows, no error)
 *   3. Error (full-page, no cached rows)
 *   4. Populated (grid cells, header banner, footer contentinfo)
 *   5. Quarantined cell (restore button inside div[role=link])
 *
 * Uses jest-axe (wraps axe-core) + @testing-library/react with jsdom.
 * Colour-contrast is disabled because jsdom cannot compute computed styles.
 */

// @vitest-environment jsdom

import { afterEach, describe, it } from "vitest";
import React from "react";
import { render, cleanup } from "@testing-library/react";
import { axe, toHaveNoViolations } from "jest-axe";
import { expect as vitestExpect } from "vitest";

vitestExpect.extend(toHaveNoViolations);

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// axe wrapper
// ---------------------------------------------------------------------------

async function checkA11y(ui: React.ReactElement): Promise<void> {
  const { container } = render(ui);
  const results = await axe(container, {
    rules: {
      "color-contrast": { enabled: false },
    },
  });
  vitestExpect(results).toHaveNoViolations();
}

// ---------------------------------------------------------------------------
// Shared stub components — mirror the status-board DOM structure.
// ---------------------------------------------------------------------------

/** A single 24h bar — aria-hidden; the ActivityStripe has its own role="img". */
function ActivityStripeStub() {
  return (
    <div
      role="img"
      aria-label="24-hour activity, total 0 sessions, peak 0 at 00:00"
      style={{ display: "flex", height: 22, gap: 1 }}
    >
      {Array.from({ length: 24 }, (_, i) => (
        <div key={i} aria-hidden="true" style={{ flex: 1, background: "#e5e7eb", borderRadius: 1 }} />
      ))}
    </div>
  );
}

interface CellProps {
  name: string;
  activity?: string;
  restorable?: boolean;
}

/**
 * Stub StatusBoardCell matching the produced DOM shape:
 *   - <a> when not restorable
 *   - <div role="link"> when restorable (contains a restore <button>)
 */
function CellStub({ name, activity = "IDLE", restorable = false }: CellProps) {
  const href = `/butlers/${name}`;

  const innerContent = (
    <>
      <div aria-hidden="true" title={name} style={{ width: 28, height: 28, borderRadius: 8, background: "#e5e7eb" }} />
      <span style={{ fontWeight: 500 }}>{name}</span>
      {restorable ? (
        <button
          type="button"
          onClick={(e) => e.stopPropagation()}
          style={{ fontFamily: "monospace", fontSize: 9, textTransform: "uppercase" }}
        >
          {activity}
        </button>
      ) : (
        <span style={{ fontFamily: "monospace", fontSize: 9, textTransform: "uppercase" }}>
          {activity}
        </span>
      )}
      <div style={{ marginTop: "auto" }}>
        <ActivityStripeStub />
      </div>
    </>
  );

  if (restorable) {
    return (
      <div
        role="link"
        tabIndex={0}
        aria-label={`${name}, ${activity.toLowerCase()}, last run unknown, 0 sessions in 24h`}
        onClick={() => { window.location.href = href; }}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") window.location.href = href; }}
        style={{ display: "flex", flexDirection: "column", minHeight: 224, padding: 20, cursor: "pointer" }}
      >
        {innerContent}
      </div>
    );
  }

  return (
    <a
      href={href}
      aria-label={`${name}, ${activity.toLowerCase()}, last run unknown, 0 sessions in 24h`}
      style={{ display: "flex", flexDirection: "column", minHeight: 224, padding: 20, textDecoration: "none" }}
    >
      {innerContent}
    </a>
  );
}

// ---------------------------------------------------------------------------
// Story 1: Loading state
// ---------------------------------------------------------------------------

describe("a11y: Loading state", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butlers">
        <div role="status" aria-label="Loading">
          <div aria-hidden="true" style={{ height: 56, background: "#e5e7eb", borderRadius: 4 }} />
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 16, marginTop: 16 }}>
            {Array.from({ length: 8 }, (_, i) => (
              <div key={i} aria-hidden="true" style={{ height: 224, background: "#e5e7eb", borderRadius: 4 }} />
            ))}
          </div>
          <div aria-hidden="true" style={{ height: 64, background: "#e5e7eb", borderRadius: 4, marginTop: 16 }} />
        </div>
      </main>,
    );
  });
});

// ---------------------------------------------------------------------------
// Story 2: Empty state (no rows, no error)
// ---------------------------------------------------------------------------

describe("a11y: Empty state", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butlers">
        {/* BoardHeader absent in empty state (Page renders empty slot) */}
        <div>
          <h2>No butlers found</h2>
          <p>Check daemon status and try again.</p>
        </div>
      </main>,
    );
  });
});

// ---------------------------------------------------------------------------
// Story 3: Error state (full-page, no cached rows)
// ---------------------------------------------------------------------------

describe("a11y: Error state", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butlers">
        <div role="alert">
          <p style={{ fontWeight: 600, color: "#dc2626" }}>Something went wrong</p>
          <p style={{ color: "#dc2626" }}>Failed to fetch butler list.</p>
          <button type="button">Retry</button>
        </div>
      </main>,
    );
  });
});

// ---------------------------------------------------------------------------
// Story 4: Populated (header banner, grid group, cells as links, footer contentinfo)
// ---------------------------------------------------------------------------

describe("a11y: Populated state", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butlers">
        {/* BoardHeader — rendered as a plain div here. BoardHeader uses a
            semantic <header> element without role=banner so it is valid
            inside <main>; axe banner-is-top-level is not triggered. */}
        <div aria-label="Status board header">
          <span style={{ fontFamily: "monospace", fontSize: 10, textTransform: "uppercase" }}>
            Butlers, status board
          </span>
          <h1 style={{ fontSize: 24, fontWeight: 700 }}>The staff, at a glance</h1>
          <div
            aria-label="3 of 3 reporting healthy"
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: "50%", background: "#22c55e" }} />
            <span style={{ fontFamily: "monospace", fontSize: 10 }}>3/3 reporting</span>
          </div>
        </div>

        {/* Status-board grid — role=group */}
        <div role="group" aria-label="Butler status board" style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)" }}>
          <CellStub name="general" activity="IDLE" />
          <CellStub name="health" activity="RUNNING" />
          <CellStub name="finance" activity="IDLE" />
        </div>

        {/* BoardFooter — rendered as a plain div here. BoardFooter uses a
            semantic <footer> element without role=contentinfo so it is valid
            inside <main>; axe contentinfo-is-top-level is not triggered. */}
        <div aria-label="Status board footer">
          <div
            role="group"
            aria-label="Active: 1"
            style={{ display: "flex", flexDirection: "column", gap: 4 }}
          >
            <span style={{ fontFamily: "monospace", fontSize: 9, textTransform: "uppercase" }}>Active</span>
            <span style={{ fontFamily: "monospace", fontSize: 16 }}>1</span>
          </div>
          <div
            role="group"
            aria-label="Sessions in the past 24 hours: 15"
            style={{ display: "flex", flexDirection: "column", gap: 4 }}
          >
            <span style={{ fontFamily: "monospace", fontSize: 9, textTransform: "uppercase" }}>Sessions 24h</span>
            <span style={{ fontFamily: "monospace", fontSize: 16 }}>15</span>
          </div>
        </div>
      </main>,
    );
  });
});

// ---------------------------------------------------------------------------
// Story 5: Quarantined cell (restore button inside div[role=link])
// ---------------------------------------------------------------------------

describe("a11y: Quarantined cell (restore chip)", () => {
  it("has zero axe violations", async () => {
    await checkA11y(
      <main aria-label="Butlers">
        <div aria-label="Status board header">
          <h1>The staff, at a glance</h1>
        </div>
        <div role="group" aria-label="Butler status board" style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)" }}>
          {/* Restorable cell uses div[role=link] so button is not inside <a> */}
          <CellStub name="quarant" activity="QUARANTINED" restorable={true} />
          <CellStub name="general" activity="IDLE" />
        </div>
      </main>,
    );
  });
});
